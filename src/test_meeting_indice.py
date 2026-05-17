"""Teste manual do indice.txt (resumo + índice). Rode: python3 src/test_meeting_indice.py"""

from __future__ import annotations

import tempfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from listen_app.meeting_analysis import MeetingTopic, save_meeting_topics


def main() -> None:
    # Simula um único tópico longo parecido com reunião em PT (como no teu ficheiro)
    texto = (
        "Bom dia a todos, vamos começar a reunião. Hoje falamos de alinhamento da equipa "
        "e das próximas entregas. Eu sugeri dividir as tarefas por área. "
        "Vocês concordam com essa divisão? Precisamos fechar o cronograma até sexta."
    )
    topics = [
        MeetingTopic(
            index=1,
            start_sec=0.0,
            end_sec=7 * 60 + 29,
            title_slug="reuniao_eu_reunioes_voce",
            title_display="reunião eu reuniões você",
            text=texto,
            segment_count=3,
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        save_meeting_topics(topics, out, language="pt")
        indice = (out / "indice.txt").read_text(encoding="utf-8")
        assert "## Resumo executivo" in indice, indice[:500]
        assert "## Índice detalhado" in indice
        assert "Trecho analisado" in indice
        assert "Bom dia a todos" in indice or "alinhamento" in indice
        assert "01_" in indice and ".txt" in indice
        print("OK — indice.txt contém resumo e índice.")
        print("---")
        print(indice[:1200])


if __name__ == "__main__":
    main()
