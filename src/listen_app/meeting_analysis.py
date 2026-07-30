"""Extrair áudio de vídeo, transcrever com timestamps e agrupar em tópicos."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .transcriber import Transcriber, TranscriptSegment
from .meeting_summary_ai import MeetingAISummaryOptions

logger = logging.getLogger(__name__)

try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
except ImportError:
    TfidfVectorizer = None  # type: ignore[misc, assignment]

# Palavras muito comuns em reuniões (PT/EN leve); TF-IDF foca no que difere.
_STOPWORDS = frozenset(
    {
        "a",
        "o",
        "os",
        "as",
        "um",
        "uma",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "sem",
        "que",
        "se",
        "né",
        "pra",
        "lá",
        "aqui",
        "então",
        "tá",
        "ok",
        "certo",
        "bom",
        "bem",
        "tipo",
        "assim",
        "gente",
        "pessoal",
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "at",
        "we",
        "you",
        "i",
        "so",
        "just",
        "yeah",
        "uh",
        "um",
    }
)


@dataclass
class MeetingTopic:
    index: int
    start_sec: float
    end_sec: float
    title_slug: str
    title_display: str
    text: str
    segment_count: int


def _local_now() -> datetime:
    """Data/hora local do sistema para pastas e cabeçalhos."""
    return datetime.now().astimezone()


def _folder_name_with_local_datetime(stem: str, *, prefix: str = "reuniao") -> str:
    """Nome da pasta de saída: slug + data e hora locais."""
    now = _local_now()
    safe = _slugify(stem, 40) or "reuniao"
    return f"{prefix}_{safe}_{now:%Y-%m-%d}_{now:%H-%M-%S}"


def _fmt_hms(seconds: float) -> str:
    s = max(0.0, float(seconds))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(round(s % 60))
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _slugify(label: str, max_len: int = 48) -> str:
    s = label.lower().strip()
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.UNICODE)
    s = re.sub(r"[\s\-]+", "_", s).strip("_")
    return (s[:max_len] if s else "topico").rstrip("_") or "topico"


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _extract_topic_blurb(text: str, max_chars: int = 320) -> str:
    """
    Trecho inicial do tópico para o resumo no indice.txt (sem repetir o arquivo inteiro).
    """
    t = _one_line(text)
    if not t:
        return ""
    if len(t) <= max_chars:
        return t
    chunks = re.split(r"(?<=[.!?…])\s+", t)
    acc: list[str] = []
    cur = 0
    for part in chunks:
        if not part:
            continue
        add = len(part) if not acc else len(part) + 1
        if acc and cur + add > max_chars:
            break
        acc.append(part)
        cur += add
        if cur >= int(max_chars * 0.88):
            break
    out = " ".join(acc).strip()
    if len(out) < 50:
        out = t[:max_chars].rsplit(" ", 1)[0] + "…"
    elif len(t) > len(out) + 15:
        out = out.rstrip(".!?") + "…"
    return out


def _topic_output_filename(t: MeetingTopic) -> str:
    start = _fmt_hms(t.start_sec)
    end = _fmt_hms(t.end_sec)
    fname = f"{t.index:02d}_{start.replace(':', '')}-{end.replace(':', '')}_{t.title_slug}.txt"
    if len(fname) > 180:
        fname = f"{t.index:02d}_{t.title_slug}.txt"
    return fname


def _title_from_tfidf(text: str, lang_hint: str | None) -> str | None:
    if TfidfVectorizer is None or len(text.strip()) < 40:
        return None
    try:
        vec = TfidfVectorizer(
            max_features=12,
            ngram_range=(1, 2),
            min_df=1,
            token_pattern=r"(?u)\b\w\w+\b",
            stop_words=None,
        )
        mat = vec.fit_transform([text])
        scores = mat.toarray()[0]
        names = vec.get_feature_names_out()
        pairs = sorted(zip(scores, names), reverse=True)
        terms: list[str] = []
        for sc, name in pairs:
            if sc <= 0 or not name:
                continue
            low = name.lower()
            if low in _STOPWORDS:
                continue
            if low not in terms:
                terms.append(low)
            if len(terms) >= 4:
                break
        if not terms:
            return None
        connector = " · " if lang_hint and lang_hint.lower().startswith("pt") else " | "
        return connector.join(terms[:4])
    except Exception:
        return None


def _title_heuristic(text: str, lang_hint: str | None) -> str:
    t = re.sub(r"\s+", " ", text.strip())
    if not t:
        return "topico"
    tfidf = _title_from_tfidf(text, lang_hint)
    if tfidf:
        return tfidf
    return t[:72] + ("…" if len(t) > 72 else "")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def yt_dlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401

        return True
    except ImportError:
        return False


def is_probably_youtube_url(url: str) -> bool:
    u = url.strip().lower()
    return "youtube.com/" in u or "youtu.be/" in u or u.startswith("youtu.be/")


def is_remote_media_url(raw: str) -> bool:
    u = raw.strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def is_google_drive_url(url: str) -> bool:
    u = url.strip().lower()
    return "drive.google.com/" in u or "docs.google.com/" in u


def ensure_media_download_url(raw: str) -> str:
    """
    Valida URL para download automático (yt-dlp): YouTube, Google Drive, etc.
    O Listen descarrega para pasta temporária — não precisa de ficheiro local.
    """
    u = raw.strip()
    if not u:
        raise ValueError(
            "Cole o link do vídeo (ex.: Google Drive da gravação Meet ou YouTube)."
        )

    low = u.lower()
    if low.endswith(".txt"):
        raise ValueError(
            "Isto parece um ficheiro de transcrição (.txt), não um link de vídeo."
        )
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".mpeg", ".mpg", ".m4a"):
        if low.endswith(ext) and "://" not in u:
            normalized = u.replace("\\", "/")
            if normalized.count("/") == 0 or normalized.startswith(
                ("/", "./", "../", "~/")
            ):
                raise ValueError(
                    "Isto parece um ficheiro local. Use «Escolher vídeo» ou o caminho completo."
                )

    if "meet.google.com/" in low:
        raise ValueError(
            "O Google Meet não permite descarregar pelo link da reunião ao vivo. "
            "Abra a gravação no Google Drive (ícone Gravações ou e-mail do Google), "
            "copie o link do ficheiro de vídeo (drive.google.com/file/d/…) e cole aqui. "
            "O vídeo tem de estar partilhado com a sua conta ou «Qualquer pessoa com o link»."
        )

    if not is_remote_media_url(u):
        if not u.startswith(("http://", "https://")):
            u = "https://" + u.lstrip("/")
        if not is_remote_media_url(u):
            raise ValueError(
                "Indique um link completo (https://…), por exemplo gravação no Google Drive."
            )

    return u


def ensure_youtube_download_url(raw: str) -> str:
    """
    Valida entrada antes do yt-dlp. Evita nomes de ficheiros locais (.txt, .mp4) confundidos com URL.
    """
    u = raw.strip()
    if not u:
        raise ValueError("Cole o link do vídeo (URL do YouTube).")

    low = u.lower()
    if low.endswith(".txt"):
        raise ValueError(
            "Isto parece um ficheiro de transcrição (.txt), não um link do YouTube. "
            "Para analisar um vídeo no computador, use «Vídeo → tópicos» ou: "
            "listen --meeting-video /caminho/para/o/video.mp4"
        )
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".mpeg", ".mpg", ".m4a"):
        if low.endswith(ext) and "://" not in u:
            normalized = u.replace("\\", "/")
            if normalized.count("/") == 0 or normalized.startswith(("/", "./", "../", "~/")):
                raise ValueError(
                    "Isto parece um ficheiro de vídeo local, não uma URL. "
                    "Use «Vídeo → tópicos» ou: listen --meeting-video /caminho/completo/ficheiro.mp4"
                )

    if "youtube.com" not in low and "youtu.be" not in low:
        raise ValueError(
            "O link tem de ser do YouTube "
            "(ex.: https://www.youtube.com/watch?v=… ou https://youtu.be/…)."
        )

    if not u.startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")

    return u


def download_media_from_url(url: str, work_dir: Path) -> tuple[Path, str]:
    """
    Baixa o vídeo com yt-dlp para work_dir (temporário; não precisa de ficheiro local).
    Retorna (caminho local, título para nomear pasta).
    """
    import yt_dlp

    url = ensure_media_download_url(url)
    work_dir.mkdir(parents=True, exist_ok=True)

    probe_opts: dict = {"quiet": True, "noplaylist": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(probe_opts) as ydl_probe:
            info = ydl_probe.extract_info(url, download=False)
    except Exception as exc:
        hint = ""
        if is_google_drive_url(url):
            hint = (
                " Verifique se a gravação está partilhada com a sua conta Google "
                "ou «Qualquer pessoa com o link»."
            )
        raise RuntimeError(f"Não foi possível aceder ao link: {exc}.{hint}") from exc

    vid = str(info.get("id") or "video")
    title = str(info.get("title") or vid).strip() or vid
    outtmpl = str(work_dir / f"{vid}.%(ext)s")

    ydl_opts: dict = {
        "outtmpl": outtmpl,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        hint = ""
        if is_google_drive_url(url):
            hint = (
                " No Drive, abra a gravação → Partilhar → acesso com link ou a sua conta."
            )
        raise RuntimeError(f"Download do vídeo falhou: {exc}.{hint}") from exc

    exts = (".mp4", ".webm", ".mkv", ".mov", ".m4a", ".opus", ".ogg", ".wav")
    candidates = [p for p in work_dir.glob(f"{vid}.*") if p.suffix.lower() in exts]
    if not candidates:
        candidates = list(work_dir.glob(f"{vid}.*"))
    if not candidates:
        raise RuntimeError(
            "yt-dlp concluiu, mas nenhum arquivo esperado foi encontrado."
        )

    media_path = max(candidates, key=lambda p: p.stat().st_mtime)
    return media_path, title


def download_youtube_video(url: str, work_dir: Path) -> tuple[Path, str]:
    """Baixa vídeo do YouTube (validação restrita a URLs YouTube)."""
    url = ensure_youtube_download_url(url)
    return download_media_from_url(url, work_dir)


def extract_audio_wav_16k_mono(video_path: Path, wav_path: Path) -> None:
    """Usa ffmpeg para gerar WAV 16 kHz mono (ideal para Whisper)."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(wav_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "ffmpeg falhou").strip()
        raise RuntimeError(f"ffmpeg: {msg}")


def group_segments_into_topics(
    segments: list[TranscriptSegment],
    *,
    gap_seconds: float = 10.0,
    lang_hint: str | None = None,
) -> list[MeetingTopic]:
    """
    Junta segmentos do Whisper em tópicos quando há pausa longa entre falas.

    gap_seconds: silêncio mínimo entre o fim de um segmento e o início do
    próximo para iniciar um novo tópico (ajustável conforme a reunião).
    """
    segs = [s for s in segments if s.text.strip()]
    if not segs:
        return []

    topics: list[MeetingTopic] = []
    bucket: list[TranscriptSegment] = [segs[0]]

    for prev, cur in zip(segs, segs[1:]):
        pause = cur.start - prev.end
        if pause >= gap_seconds and bucket:
            topics.append(_flush_topic_bucket(len(topics) + 1, bucket, lang_hint))
            bucket = [cur]
        else:
            bucket.append(cur)

    if bucket:
        topics.append(_flush_topic_bucket(len(topics) + 1, bucket, lang_hint))
    return topics


def _flush_topic_bucket(
    index: int, bucket: list[TranscriptSegment], lang_hint: str | None
) -> MeetingTopic:
    start = bucket[0].start
    end = bucket[-1].end
    text = " ".join(s.text.strip() for s in bucket).strip()
    display = _title_heuristic(text, lang_hint)
    return MeetingTopic(
        index=index,
        start_sec=start,
        end_sec=end,
        title_slug=_slugify(display),
        title_display=display,
        text=text,
        segment_count=len(bucket),
    )


def _build_dados_salvos_section(
    topics: list[MeetingTopic],
    file_names: list[str],
    blurbs: list[str],
) -> str:
    """Resumo dos .txt gravados nesta pasta (base do índice)."""
    lines = [
        "## Dados salvos nesta pasta",
        "",
        "Transcrição por tópico (ficheiros `.txt` gerados nesta análise):",
        "",
    ]
    for t, fname, blurb in zip(topics, file_names, blurbs):
        start = _fmt_hms(t.start_sec)
        end = _fmt_hms(t.end_sec)
        title = _one_line(t.title_display) or f"Tópico {t.index}"
        lines.append(f"### {t.index:02d}. {fname}")
        lines.append(f"- Horário: {start} – {end}")
        lines.append(f"- Título: {title}")
        if blurb:
            lines.append(f"- Trecho: {blurb}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_indice_header(
    *,
    created_at: datetime,
    whisper_model: str,
    indice_ai_provider: str | None = None,
    indice_ai_model: str | None = None,
    output_dir_name: str,
    source_video: Path | None = None,
    source_url: str | None = None,
    language: str = "",
    topics: list[MeetingTopic],
) -> list[str]:
    """Cabeçalho do indice.txt: data/hora local e modelos usados."""
    local = created_at.astimezone()
    header: list[str] = [
        "# indice.txt",
        f"# Data e hora (local): {local:%Y-%m-%d %H:%M:%S}",
        f"# Pasta: {output_dir_name}",
        f"# Modelo Whisper (transcrição): {whisper_model or '—'}",
    ]
    if indice_ai_provider == "cursor":
        header.append(
            f"# Modelo Cursor (índice IA): {indice_ai_model or 'predefinido (CLI)'}"
        )
    elif indice_ai_model:
        header.append(f"# Modelo OpenRouter (índice IA): {indice_ai_model}")
    else:
        header.append("# Modelo IA (índice): — (índice automático)")
    if source_url:
        header.append(f"# URL: {source_url.strip()}")
    if source_video is not None:
        header.append(f"# Arquivo local: {source_video.name}")
    if language:
        header.append(f"# Idioma detectado: {language}")
    if topics:
        head = topics[0].start_sec
        tail = topics[-1].end_sec
        span_sec = max(0.0, tail - head)
        header.append(
            f"# Trecho analisado: {_fmt_hms(head)} → {_fmt_hms(tail)} "
            f"(~{span_sec / 60.0:.1f} min)"
        )
    header.append(f"# Quantidade de tópicos: {len(topics)}")
    header.append("")
    return header


def _build_indice_content(
    topics: list[MeetingTopic],
    file_names: list[str],
    blurbs: list[str],
    *,
    created_at: datetime,
    whisper_model: str,
    indice_ai_provider: str | None = None,
    indice_ai_model: str | None = None,
    output_dir_name: str = "",
    source_video: Path | None = None,
    source_url: str | None = None,
    language: str = "",
    ai_indice_body: str | None = None,
    ai_indice_error: str | None = None,
    ai_skipped_note: str | None = None,
) -> str:
    """Texto completo do indice.txt: metadados, dados salvos, depois IA ou automático."""
    header = _build_indice_header(
        created_at=created_at,
        whisper_model=whisper_model,
        indice_ai_provider=indice_ai_provider,
        indice_ai_model=indice_ai_model,
        output_dir_name=output_dir_name,
        source_video=source_video,
        source_url=source_url,
        language=language,
        topics=topics,
    )
    dados_salvos = _build_dados_salvos_section(topics, file_names, blurbs)

    body: list[str] = list(header)
    body.append(dados_salvos)
    body.append("")

    if ai_skipped_note:
        body.append(f"_{ai_skipped_note}_")
        body.append("")

    if ai_indice_body:
        if indice_ai_provider == "cursor":
            note = (
                "_Resumo e índice detalhado gerados pelo Cursor Agent (local) "
                "com base nos dados salvos acima._"
            )
        else:
            note = (
                "_Resumo e índice detalhado gerados por IA (OpenRouter) "
                "com base nos dados salvos acima._"
            )
        body.append(note)
        body.append("")
        body.append(ai_indice_body.strip())
        body.append("")
        return "\n".join(body).rstrip() + "\n"

    if ai_indice_error:
        body.append("## Aviso — IA")
        body.append("")
        body.append(
            f"Não foi possível gerar o índice com IA: {ai_indice_error}. "
            "Segue índice automático a partir da transcrição."
        )
        body.append("")

    body.append("## Resumo executivo")
    body.append("")
    body.append(
        "Lista rápida do que foi tratado em cada bloco (com horário e um trecho da fala)."
    )
    body.append("")

    for t, fname, blurb in zip(topics, file_names, blurbs):
        start = _fmt_hms(t.start_sec)
        end = _fmt_hms(t.end_sec)
        title = _one_line(t.title_display) or f"Tópico {t.index}"
        body.append(f"■ {start} – {end} | {title}")
        if blurb:
            body.append(f"  {blurb}")
        body.append(f"  → ver: {fname}")
        body.append("")

    body.append("## Índice detalhado")
    body.append("")
    for t, fname, blurb in zip(topics, file_names, blurbs):
        start = _fmt_hms(t.start_sec)
        end = _fmt_hms(t.end_sec)
        title = _one_line(t.title_display) or f"Tópico {t.index}"
        body.append(f"{t.index}. {start} – {end} | {title}")
        body.append(f"   Arquivo: {fname}")
        if blurb:
            body.append(f"   Trecho: {blurb}")
        body.append("")

    return "\n".join(body).rstrip() + "\n"


def save_meeting_topics(
    topics: list[MeetingTopic],
    output_dir: Path,
    *,
    created_at: datetime | None = None,
    whisper_model: str = "",
    indice_ai_provider: str | None = None,
    indice_ai_model: str | None = None,
    source_video: Path | None = None,
    source_url: str | None = None,
    language: str = "",
    ai_indice_body: str | None = None,
    ai_indice_error: str | None = None,
    ai_skipped_note: str | None = None,
) -> Path:
    """Grava um .txt por tópico mais indice.txt (resumo + índice) na pasta criada."""
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_at = created_at or _local_now()

    file_names: list[str] = []
    blurbs: list[str] = []
    file_bodies: list[tuple[str, str]] = []

    for t in topics:
        fname = _topic_output_filename(t)
        blurb = _extract_topic_blurb(t.text)
        start = _fmt_hms(t.start_sec)
        end = _fmt_hms(t.end_sec)

        topic_body = "\n".join(
            [
                f"# Gravado em (local): {saved_at.astimezone():%Y-%m-%d %H:%M:%S}",
                f"# Modelo Whisper: {whisper_model or '—'}",
                f"Início: {start} ({t.start_sec:.1f}s)",
                f"Fim:    {end} ({t.end_sec:.1f}s)",
                f"Título sugerido: {t.title_display}",
                f"Segmentos Whisper: {t.segment_count}",
                "",
                t.text,
                "",
            ]
        )
        file_names.append(fname)
        blurbs.append(blurb)
        file_bodies.append((fname, topic_body))

    for fname, topic_body in file_bodies:
        (output_dir / fname).write_text(topic_body, encoding="utf-8")

    indice_text = _build_indice_content(
        topics,
        file_names,
        blurbs,
        created_at=saved_at,
        whisper_model=whisper_model,
        indice_ai_provider=indice_ai_provider,
        indice_ai_model=indice_ai_model,
        output_dir_name=output_dir.name,
        source_video=source_video,
        source_url=source_url,
        language=language,
        ai_indice_body=ai_indice_body,
        ai_indice_error=ai_indice_error,
        ai_skipped_note=ai_skipped_note,
    )
    (output_dir / "indice.txt").write_text(indice_text, encoding="utf-8")
    return output_dir


def analyze_media_to_topics(
    media_path: Path,
    transcriber: Transcriber,
    output_base_dir: Path,
    *,
    folder_stem: str,
    language: str | None = None,
    gap_seconds: float = 10.0,
    source_video: Path | None = None,
    source_url: str | None = None,
    ai_summary_options: MeetingAISummaryOptions | None = None,
) -> Path:
    """
    Núcleo: extrai áudio do arquivo local, transcreve, agrupa tópicos e salva .txt.
    ``folder_stem`` entra no nome da pasta (já pode ser um título humano).
    """
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg não encontrado no PATH. Instale com: sudo apt install ffmpeg"
        )
    media_path = media_path.expanduser().resolve()
    if not media_path.is_file():
        raise FileNotFoundError(str(media_path))

    created_at = _local_now()
    out_dir = output_base_dir / _folder_name_with_local_datetime(folder_stem)
    whisper_model = str(getattr(transcriber, "model_size", "") or "")
    try:
        whisper_model = str(transcriber.get_model_info().get("model_size", whisper_model))
    except Exception:
        pass

    indice_ai_provider: str | None = None
    indice_ai_model: str | None = None
    ai_provider = (
        (ai_summary_options.provider or "openrouter").strip().lower()
        if ai_summary_options is not None
        else ""
    )
    if ai_summary_options is not None and ai_summary_options.enabled:
        if ai_provider == "cursor":
            indice_ai_provider = "cursor"
            indice_ai_model = (
                (ai_summary_options.cursor_model or "").strip() or "predefinido (CLI)"
            )
        else:
            indice_ai_provider = "openrouter"
            indice_ai_model = ai_summary_options.model

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    try:
        extract_audio_wav_16k_mono(media_path, wav_path)
        result = transcriber.transcribe_with_segments(str(wav_path), language=language)
        topics = group_segments_into_topics(
            result.segments, gap_seconds=gap_seconds, lang_hint=result.language
        )
        if not topics and result.text.strip():
            topics = [
                MeetingTopic(
                    index=1,
                    start_sec=0.0,
                    end_sec=float(result.duration),
                    title_slug="transcricao_completa",
                    title_display="Transcrição completa",
                    text=result.text.strip(),
                    segment_count=len(result.segments),
                )
            ]
        lang_out = result.language or (language or "")

        file_names = [_topic_output_filename(t) for t in topics]
        ai_body: str | None = None
        ai_err: str | None = None
        ai_skipped_note: str | None = None
        if ai_summary_options is not None and ai_summary_options.enabled:
            from .meeting_summary_ai import TopicBrief

            briefs = [
                TopicBrief(
                    index=t.index,
                    start_sec=t.start_sec,
                    end_sec=t.end_sec,
                    title_display=t.title_display,
                    text=t.text,
                    filename=fname,
                )
                for t, fname in zip(topics, file_names)
            ]
            if ai_provider == "cursor":
                from .meeting_summary_cursor import generate_meeting_indice_cursor

                logger.info("Indice IA: Cursor Agent (pasta %s)", out_dir.name)
                out_dir.mkdir(parents=True, exist_ok=True)
                ai_body, ai_err = generate_meeting_indice_cursor(
                    briefs,
                    language=lang_out,
                    options=ai_summary_options,
                    work_dir=out_dir,
                )
            else:
                from .meeting_summary_ai import generate_meeting_indice_ai

                logger.info("Indice IA: OpenRouter (%s)", ai_summary_options.model)
                ai_body, ai_err = generate_meeting_indice_ai(
                    briefs, language=lang_out, options=ai_summary_options
                )
        elif ai_summary_options is not None and not ai_summary_options.enabled:
            ai_skipped_note = (
                "Resumo por IA desactivado (active em Configurações → IA no indice)."
            )

        save_meeting_topics(
            topics,
            out_dir,
            created_at=created_at,
            whisper_model=whisper_model,
            indice_ai_provider=indice_ai_provider,
            indice_ai_model=indice_ai_model,
            source_video=source_video if source_video is not None else media_path,
            source_url=source_url,
            language=lang_out,
            ai_indice_body=ai_body,
            ai_indice_error=ai_err,
            ai_skipped_note=ai_skipped_note,
        )
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass

    return out_dir


def analyze_video_google_meet(
    video_path: Path,
    transcriber: Transcriber,
    output_base_dir: Path,
    *,
    language: str | None = None,
    source_url: str | None = None,
    folder_stem: str | None = None,
) -> Path:
    """
    Modo Google Meet: transcreve e grava transcricao.txt com [início – fim] por frase.
    """
    from .google_meet_analysis import save_chronological_transcription

    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg não encontrado no PATH. Instale com: sudo apt install ffmpeg"
        )
    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(str(video_path))

    created_at = _local_now()
    stem = folder_stem or video_path.stem
    out_dir = output_base_dir / _folder_name_with_local_datetime(
        stem, prefix="reuniao_meet"
    )
    whisper_model = str(getattr(transcriber, "model_size", "") or "")
    try:
        whisper_model = str(transcriber.get_model_info().get("model_size", whisper_model))
    except Exception:
        pass

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    try:
        extract_audio_wav_16k_mono(video_path, wav_path)
        result = transcriber.transcribe_with_segments(str(wav_path), language=language)
        segments = list(result.segments)
        if not segments and result.text.strip():
            segments = [
                TranscriptSegment(
                    start=0.0,
                    end=float(result.duration),
                    text=result.text.strip(),
                )
            ]
        save_chronological_transcription(
            segments,
            out_dir,
            created_at=created_at,
            whisper_model=whisper_model,
            source_video=video_path,
            source_url=source_url,
            language=result.language or (language or ""),
        )
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass

    return out_dir


def analyze_url_google_meet(
    url: str,
    transcriber: Transcriber,
    output_base_dir: Path,
    *,
    language: str | None = None,
) -> Path:
    """
    Modo Google Meet a partir de link (Google Drive, YouTube, …).
    Download automático; saída em transcricao.txt com timestamps.
    """
    if not yt_dlp_available():
        raise RuntimeError(
            "yt-dlp não está disponível. Instale: pip install -e ."
        )

    url = ensure_media_download_url(url)
    with tempfile.TemporaryDirectory() as tmpdir:
        media_path, title = download_media_from_url(url, Path(tmpdir))
        return analyze_video_google_meet(
            media_path,
            transcriber,
            output_base_dir,
            language=language,
            source_url=url,
            folder_stem=title,
        )


def analyze_video_to_topics(
    video_path: Path,
    transcriber: Transcriber,
    output_base_dir: Path,
    *,
    language: str | None = None,
    gap_seconds: float = 10.0,
    source_url: str | None = None,
    ai_summary_options: MeetingAISummaryOptions | None = None,
    google_meet_mode: bool = False,
) -> Path:
    """
    Extrai áudio, transcreve com segmentos, agrupa tópicos e salva .txt.
    Retorna o diretório criado.
    """
    video_path = video_path.expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(str(video_path))

    if google_meet_mode:
        return analyze_video_google_meet(
            video_path,
            transcriber,
            output_base_dir,
            language=language,
            source_url=source_url,
        )

    return analyze_media_to_topics(
        video_path,
        transcriber,
        output_base_dir,
        folder_stem=video_path.stem,
        language=language,
        gap_seconds=gap_seconds,
        source_video=video_path,
        source_url=source_url,
        ai_summary_options=ai_summary_options,
    )


def analyze_youtube_to_topics(
    url: str,
    transcriber: Transcriber,
    output_base_dir: Path,
    *,
    language: str | None = None,
    gap_seconds: float = 10.0,
    ai_summary_options: MeetingAISummaryOptions | None = None,
    google_meet_mode: bool = False,
) -> Path:
    """
    Baixa o vídeo com yt-dlp para pasta temporária e reutiliza o fluxo de tópicos.
    """
    if not yt_dlp_available():
        raise RuntimeError(
            "yt-dlp não está disponível neste ambiente Python. "
            "Reinstale o Listen com dependências completas: pip install -e ."
        )

    url = ensure_youtube_download_url(url)
    if google_meet_mode:
        return analyze_url_google_meet(
            url, transcriber, output_base_dir, language=language
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        media_path, title = download_youtube_video(url, Path(tmpdir))
        return analyze_media_to_topics(
            media_path,
            transcriber,
            output_base_dir,
            folder_stem=title,
            language=language,
            gap_seconds=gap_seconds,
            source_video=media_path,
            source_url=url.strip(),
            ai_summary_options=ai_summary_options,
        )
