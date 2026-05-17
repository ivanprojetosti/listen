# Regras por página — Listen GUI

Cada ficheiro descreve o comportamento **actual** de um ecrã do menu lateral.

## Obrigatório para agentes e developers

**Sempre que alterares** comportamento, textos, validações ou integrações de uma página:

1. Actualiza o `.md` correspondente nesta pasta.
2. Se a mudança afectar OpenRouter ou config global, actualiza também `openrouter-ai.md` e `settings.md`.
3. Mantém as regras `.cursor/rules/*.mdc` alinhadas se mudar convenções transversais.

## Índice

| Página | Ficheiro |
|--------|----------|
| Gravar | [record.md](record.md) |
| Histórico | [history.md](history.md) |
| Vídeo | [video.md](video.md) |
| YouTube | [youtube.md](youtube.md) |
| Configurações | [settings.md](settings.md) |
| OpenRouter / indice IA | [openrouter-ai.md](openrouter-ai.md) |

## Código fonte

- UI: `src/listen_app/gui.py` (`_build_page_*`, `_NAV_ITEMS`)
- Análise: `src/listen_app/meeting_analysis.py`
- IA: `src/listen_app/meeting_summary_ai.py`
- Config: `src/listen_app/settings.py`
