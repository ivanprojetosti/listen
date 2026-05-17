# Modo Cursor — índice local

## Propósito

Gerar o corpo do `indice.txt` (## Resumo executivo + ## Índice detalhado) com o **Cursor Agent** na máquina, sem OpenRouter.

## Configuração (`config.json`)

| Chave | Descrição |
|-------|-----------|
| `meeting_ai_summary` | Activar geração do índice por IA (OpenRouter) |
| `cursor_mode` | `true` → Cursor local em **vídeo, YouTube e CLI**; implica IA no índice |
| `cursor_model` | Modelo opcional passado a `cursor agent --model` |
| `cursor_cli` | Comando CLI (default `cursor`) |

## Fluxo

1. Transcrição Whisper e `.txt` por tópico (igual ao fluxo normal).
2. `meeting_summary_cursor.generate_meeting_indice_cursor()` grava `_listen_cursor_prompt.txt` na pasta da reunião e executa:
   `cursor agent --print --trust --workspace <pasta> --mode ask --output-format text`
   (`--trust` é obrigatório em modo headless; evita «Workspace Trust Required».)
3. `meeting_analysis.save_meeting_topics()` junta cabeçalho, «Dados salvos» e corpo IA.

## Pré-requisitos

- `cursor` no PATH (`which cursor`)
- Sessão autenticada: `cursor agent login`

## Testes

- GUI: **Testar Cursor** em Configurações
- CLI: `listen --test-cursor`

## Ficheiros

- `meeting_summary_cursor.py`
- `meeting_analysis.py` (ramo `provider == "cursor"`)
- `gui.py` (switch «Modo Cursor»)
