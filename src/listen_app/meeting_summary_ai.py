"""Resumo executivo da reunião via API compatível com OpenAI (nuvem ou proxy local)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class TopicBrief:
    """Dados mínimos de um tópico para o prompt (evita import circular com meeting_analysis)."""

    index: int
    start_sec: float
    end_sec: float
    title_display: str
    text: str
    filename: str = ""


def openai_sdk_available() -> bool:
    try:
        import openai  # noqa: F401

        return True
    except ImportError:
        return False


def ai_extra_install_hint() -> str:
    """Comando para instalar o cliente OpenRouter/OpenAI no ambiente actual."""
    import sys

    exe = Path(sys.executable).name if hasattr(sys, "executable") else "python3"
    return (
        f'{exe} -m pip install -e ".[ai]" openai>=1.40.0\n'
        "Ou, na pasta do projeto Listen:\n"
        "  source .venv/bin/activate && pip install -e ."
    )


@dataclass
class MeetingAISummaryOptions:
    """Opções para gerar o indice.txt (OpenRouter na nuvem ou Cursor Agent local)."""

    enabled: bool = False
    provider: str = "openrouter"  # "openrouter" | "cursor"
    model: str = "openai/gpt-4o-mini"
    api_key: str | None = None
    base_url: str | None = "https://openrouter.ai/api/v1"
    cursor_cli: str = "cursor"
    cursor_model: str | None = None
    timeout_sec: float = 180.0


def _fmt_hms(seconds: float) -> str:
    s = max(0.0, float(seconds))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(round(s % 60))
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _is_openrouter_base(base_url: str | None) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def _normalize_model_for_base(model: str, base_url: str | None) -> str:
    """
    Usa o modelo exactamente como está em config (ex.: anthropic/claude-3.5-sonnet).
    Só acrescenta openai/ em nomes curtos legados (gpt-4o-mini sem prefixo).
    """
    m = (model or "").strip()
    if not m:
        return "openai/gpt-4o-mini"
    if not _is_openrouter_base(base_url) or "/" in m:
        return m
    if m.startswith("gpt-"):
        return f"openai/{m}"
    return m


def openrouter_curl_example(
    *,
    model: str = "openai/gpt-4o-mini",
    system: str = "Responde em uma frase.",
    user: str = "Diz olá.",
) -> str:
    """Equivalente curl ao POST que o Listen faz via SDK openai (chave nunca impressa)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.25,
        "max_tokens": 4000,
    }
    body = json.dumps(payload, ensure_ascii=True)
    return (
        f"curl -sS '{OPENROUTER_CHAT_URL}' \\\n"
        "  -H 'Authorization: Bearer $OPENROUTER_API_KEY' \\\n"
        "  -H 'Content-Type: application/json' \\\n"
        "  -H 'HTTP-Referer: https://github.com/listen-app' \\\n"
        "  -H 'X-Title: Listen' \\\n"
        f"  -d '{body}'"
    )


def test_openrouter_connection(
    options: MeetingAISummaryOptions,
) -> tuple[bool, str]:
    """
    Teste rápido da API. Devolve (ok, mensagem).
  Útil para confirmar chave, modelo e rede antes de processar vídeo.
    """
    if not openai_sdk_available():
        return False, f"Pacote openai em falta.\n{ai_extra_install_hint()}"

    api_key = _resolve_api_key(options.api_key)
    if not api_key:
        if options.api_key and str(options.api_key).strip():
            return (
                False,
                "Chave OpenRouter invalida em config (texto corrompido?). "
                "Abra Configuracoes, apague o campo da chave, cole sk-or-v1-... de novo e guarde.",
            )
        return False, "Chave em falta (Configuracoes ou LISTEN_OPENROUTER_API_KEY)."

    import openai

    base_url = (options.base_url or "https://openrouter.ai/api/v1").strip().rstrip("/")
    model = _normalize_model_for_base(options.model, base_url)
    client_kwargs: dict = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": min(options.timeout_sec, 60.0),
    }
    if _is_openrouter_base(base_url):
        client_kwargs["default_headers"] = {
            "HTTP-Referer": "https://github.com/listen-app",
            "X-Title": "Listen",
        }

    try:
        client = openai.OpenAI(**client_kwargs)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Responde só: OK"}],
            max_tokens=16,
            temperature=0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return True, f"OpenRouter respondeu (modelo {model}): {text[:80]}"
    except Exception as exc:
        msg = str(exc).encode("utf-8", errors="replace").decode("utf-8")
        return False, f"Falha OpenRouter: {msg}"


def sanitize_api_key(raw: str | None) -> str | None:
    """Remove chaves inválidas (tracebacks colados, multilinha, etc.)."""
    if raw is None:
        return None
    key = str(raw).strip()
    if not key or len(key) > 256:
        return None
    lowered = key.lower()
    if any(
        bad in lowered
        for bad in (
            "traceback",
            "attributeerror",
            "file \"",
            "recent call last",
            "error:",
        )
    ):
        return None
    if "\n" in key or "\r" in key:
        return None
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key or None


def _resolve_api_key(explicit: str | None) -> str | None:
    cleaned = sanitize_api_key(explicit)
    if cleaned:
        return cleaned
    for name in (
        "LISTEN_OPENROUTER_API_KEY",
        "OPENROUTER_API_KEY",
        "LISTEN_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ):
        cleaned = sanitize_api_key(os.environ.get(name))
        if cleaned:
            return cleaned
    return None


def _topics_prompt(topics: list[TopicBrief], max_chars_per_topic: int = 4000) -> str:
    blocks: list[str] = []
    for t in topics:
        start = _fmt_hms(t.start_sec)
        end = _fmt_hms(t.end_sec)
        body = t.text.strip()
        if len(body) > max_chars_per_topic:
            body = body[:max_chars_per_topic] + "…"
        blocks.append(
            f"### Tópico {t.index} ({start} – {end})\n"
            f"Título sugerido: {t.title_display}\n"
            f"Transcrição:\n{body}\n"
        )
    return "\n".join(blocks)


def _topics_prompt_for_indice(topics: list[TopicBrief], max_chars_per_topic: int = 4000) -> str:
    blocks: list[str] = []
    for t in topics:
        start = _fmt_hms(t.start_sec)
        end = _fmt_hms(t.end_sec)
        body = t.text.strip()
        if len(body) > max_chars_per_topic:
            body = body[:max_chars_per_topic] + "…"
        file_line = f"Arquivo de saída: {t.filename}\n" if t.filename else ""
        blocks.append(
            f"### Tópico {t.index} ({start} – {end})\n"
            f"{file_line}"
            f"Título automático: {t.title_display}\n"
            f"Transcrição:\n{body}\n"
        )
    return "\n".join(blocks)


def generate_meeting_indice_ai(
    topics: list[TopicBrief],
    *,
    language: str = "",
    options: MeetingAISummaryOptions,
) -> tuple[str | None, str | None]:
    """
    Chama o modelo (OpenRouter) e devolve o corpo do indice.txt (resumo + índice).
    Se ``err`` não for None, ``texto`` deve ser None.
    """
    if not options.enabled:
        logger.info("OpenRouter: ignorado (meeting_ai_summary desactivado)")
        return None, None

    if (options.provider or "openrouter").strip().lower() == "cursor":
        logger.warning("generate_meeting_indice_ai chamado com provider=cursor")
        return None, "Modo Cursor activo; use meeting_summary_cursor."

    if not topics:
        return None, "Sem tópicos para resumir."

    if not openai_sdk_available():
        return (
            None,
            f"Pacote openai não instalado neste Python.\n{ai_extra_install_hint()}",
        )

    api_key = _resolve_api_key(options.api_key)
    if not api_key:
        if options.api_key and str(options.api_key).strip():
            return (
                None,
                "Chave OpenRouter invalida na configuracao. "
                "Em Configuracoes, apague o campo e cole de novo apenas sk-or-v1-...",
            )
        return (
            None,
            "Defina a chave OpenRouter em Configuracoes ou LISTEN_OPENROUTER_API_KEY.",
        )

    import openai

    lang = (language or "").lower()
    if lang.startswith("pt"):
        system = (
            "És um assistente que redige o ficheiro indice.txt de uma reunião ou vídeo "
            "transcrito. Usa APENAS a transcrição fornecida. "
            "Escreve em português (PT ou BR, conforme o áudio). "
            "Não inventes factos, nomes ou decisões que não apareçam no texto. "
            "Responde só com markdown, sem texto antes ou depois das secções pedidas."
        )
        user = (
            "Com base nos tópicos abaixo, gera o conteúdo do indice.txt com "
            "EXACTAMENTE estas duas secções (títulos literais):\n\n"
            "## Resumo executivo\n"
            "(2 a 5 parágrafos: objectivo, pontos principais, decisões e próximos passos)\n\n"
            "## Índice detalhado\n"
            "Para cada tópico, use este formato (uma entrada numerada):\n"
            "N. HH:MM – HH:MM | Título claro e descritivo\n"
            "   Arquivo: nome_exacto_do_ficheiro.txt\n"
            "   Trecho: frase curta do que foi dito nesse bloco\n\n"
            "Use os horários e nomes de arquivo indicados em cada tópico.\n\n"
            + _topics_prompt_for_indice(topics)
        )
    else:
        system = (
            "You write the indice.txt index file for a transcribed meeting or video. "
            "Use ONLY the transcript provided. Match the transcript language. "
            "Do not invent facts. Output markdown only with the exact section headings requested."
        )
        user = (
            "From the topics below, write indice.txt with EXACTLY these sections:\n\n"
            "## Executive summary\n"
            "(2–5 short paragraphs)\n\n"
            "## Detailed index\n"
            "For each topic:\n"
            "N. HH:MM – HH:MM | Clear title\n"
            "   File: exact_filename.txt\n"
            "   Excerpt: one short sentence\n\n"
            + _topics_prompt_for_indice(topics)
        )

    base_url = (options.base_url or "https://openrouter.ai/api/v1").strip().rstrip("/")
    model = _normalize_model_for_base(options.model, base_url)

    client_kwargs: dict = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": options.timeout_sec,
    }
    if _is_openrouter_base(base_url):
        client_kwargs["default_headers"] = {
            "HTTP-Referer": "https://github.com/listen-app",
            "X-Title": "Listen",
        }

    logger.info(
        "OpenRouter: POST %s/chat/completions model=%s topics=%d",
        base_url,
        model,
        len(topics),
    )

    try:
        client = openai.OpenAI(**client_kwargs)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.25,
            max_tokens=4000,
        )
        choice = resp.choices[0].message
        content = (choice.content or "").strip()
        if not content:
            return None, "O modelo devolveu resposta vazia."
        if "##" not in content:
            return None, "A IA não devolveu secções de índice reconhecíveis."
        logger.info("OpenRouter: resposta OK (%d caracteres)", len(content))
        return content, None
    except Exception as exc:
        logger.exception("OpenRouter: erro na chamada")
        return None, f"API: {exc}"


def generate_meeting_summary_ai(
    topics: list[TopicBrief],
    *,
    language: str = "",
    options: MeetingAISummaryOptions,
) -> tuple[str | None, str | None]:
    """Alias retrocompatível — gera o indice.txt via IA."""
    return generate_meeting_indice_ai(topics, language=language, options=options)
