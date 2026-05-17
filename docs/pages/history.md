# Página: Histórico (`history`)

## Propósito

Listar transcrições `.txt` já guardadas na pasta de salvamento.

## Regras de UI

- Lista em `saved_list_box` com data, pré-visualização e idioma.
- `history_preview_label` mostra texto ao activar uma linha.
- Lista vazia: mensagem «Nenhuma transcrição salva ainda».

## Regras de negócio

- Origem: `list_saved_transcriptions(save_directory)`.
- Ao activar linha: lê ficheiro; se `auto_copy`, copia para clipboard.
- Actualizar lista após nova gravação com salvamento (`_refresh_saved_list`).

## Ficheiros

- `gui.py`: `_build_page_history`, `_on_saved_row_activated`, `_populate_saved_list`
