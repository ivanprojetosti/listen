"""Gerar indice.txt via Cursor Agent local (CLI)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from .meeting_summary_ai import (
    MeetingAISummaryOptions,
    TopicBrief,
    _topics_prompt_for_indice,
)

logger = logging.getLogger(__name__)


def cursor_cli_available(cli: str = "cursor") -> bool:
    return shutil.which(cli) is not None


def _cursor_agent_argv(
    cli: str,
    workspace: Path,
    *,
    model: str = "",
) -> list[str]:
    """
    Argumentos base para ``cursor agent`` em modo headless.

    ``--trust`` evita o erro «Workspace Trust Required» ao correr a partir do Listen.
    """
    workspace = workspace.expanduser().resolve()
    cmd: list[str] = [
        cli,
        "agent",
        "--print",
        "--trust",
        "--workspace",
        str(workspace),
        "--mode",
        "ask",
        "--output-format",
        "text",
    ]
    if model:
        cmd.extend(["--model", model])
    return cmd


def _build_cursor_prompt(topics: list[TopicBrief], language: str) -> str:
    lang = (language or "").lower()
    if lang.startswith("pt"):
        intro = (
            "Com base APENAS na transcricao abaixo, redige o corpo do ficheiro indice.txt "
            "com EXACTAMENTE estas duas seccoes (titulos literais):\n\n"
            "## Resumo executivo\n"
            "(2 a 5 paragrafos: objectivo, pontos principais, decisoes e proximos passos)\n\n"
            "## Indice detalhado\n"
            "Para cada topico:\n"
            "N. HH:MM – HH:MM | Titulo claro\n"
            "   Arquivo: nome_exacto_do_ficheiro.txt\n"
            "   Trecho: frase curta do que foi dito\n\n"
            "Use os horarios e nomes de arquivo indicados. Nao inventes factos.\n"
            "Responda so com markdown, sem texto antes ou depois das seccoes.\n\n"
        )
    else:
        intro = (
            "Using ONLY the transcript below, write indice.txt with EXACTLY:\n\n"
            "## Executive summary\n"
            "(2-5 short paragraphs)\n\n"
            "## Detailed index\n"
            "N. HH:MM – HH:MM | Title\n"
            "   File: exact_filename.txt\n"
            "   Excerpt: one short sentence\n\n"
            "Do not invent facts. Output markdown only.\n\n"
        )
    return intro + _topics_prompt_for_indice(topics)


def generate_meeting_indice_cursor(
    topics: list[TopicBrief],
    *,
    language: str = "",
    options: MeetingAISummaryOptions,
    work_dir: Path,
) -> tuple[str | None, str | None]:
    """
    Invoca ``cursor agent --print --mode ask`` na pasta da analise.
    Devolve (corpo do indice, erro).
    """
    if not options.enabled:
        return None, None

    if (options.provider or "").strip().lower() not in ("", "cursor"):
        return None, f"Provider inesperado para Cursor: {options.provider!r}"

    if not topics:
        return None, "Sem topicos para resumir."

    cli = (options.cursor_cli or "cursor").strip() or "cursor"
    if not cursor_cli_available(cli):
        return (
            None,
            f"Comando '{cli}' nao encontrado no PATH. Instale o Cursor CLI ou defina cursor_cli na config.",
        )

    work_dir = work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = work_dir / "_listen_cursor_prompt.txt"
    prompt_path.write_text(_build_cursor_prompt(topics, language), encoding="utf-8")

    instruction = (
        f"Leia o ficheiro {prompt_path.name} nesta pasta e siga as instrucoes "
        "para gerar apenas as seccoes ## Resumo executivo e ## Indice detalhado."
    )

    model = (options.cursor_model or "").strip()
    cmd = _cursor_agent_argv(cli, work_dir, model=model)
    cmd.append(instruction)

    logger.info(
        "Cursor Agent: cwd=%s model=%s",
        work_dir,
        model or "(default)",
    )

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=options.timeout_sec,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return None, f"Cursor Agent excedeu o tempo ({options.timeout_sec:.0f}s)."
    except OSError as exc:
        return None, f"Cursor Agent: {exc}"

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        if not err:
            err = f"exit code {completed.returncode}"
        return None, f"Cursor Agent falhou: {err[:1200]}"

    content = (completed.stdout or "").strip()
    if not content:
        return None, "Cursor Agent devolveu resposta vazia."
    if "##" not in content:
        return None, "Cursor Agent nao devolveu seccoes de indice reconheciveis."
    logger.info("Cursor Agent: resposta OK (%d caracteres)", len(content))
    return content, None


def test_cursor_connection(options: MeetingAISummaryOptions) -> tuple[bool, str]:
    cli = (options.cursor_cli or "cursor").strip() or "cursor"
    if not cursor_cli_available(cli):
        return False, f"Comando '{cli}' nao encontrado no PATH."
    workspace = Path.home() / ".config" / "listen"
    workspace.mkdir(parents=True, exist_ok=True)
    model = (options.cursor_model or "").strip()
    cmd = _cursor_agent_argv(cli, workspace, model=model)
    cmd.append("Responda apenas: OK")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=min(options.timeout_sec, 90.0),
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return False, "Cursor Agent: tempo esgotado (verifique login: cursor agent login)."
    except OSError as exc:
        return False, f"Cursor Agent: {exc}"
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip() or f"code {completed.returncode}"
        return False, f"Cursor Agent: {err[:800]}"
    text = (completed.stdout or "").strip()
    return True, f"Cursor Agent respondeu (modelo {model or 'predefinido'}): {text[:80]}"
