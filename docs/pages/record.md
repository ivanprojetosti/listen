# Página: Gravar (`record`)

## Propósito

Gravação de voz (ou reunião mic+sistema), transcrição Whisper e resultado na hora.

## Regras de UI

- Mostra: info do dispositivo/GPU, interruptor **Modo reunião**, forma de onda, botão principal, `result_label`.
- Dropdown no **header** = modelo **Whisper** (`tiny` … `large-v3`), não OpenRouter.
- Estados do botão: Gravar → Transcrever → Copiar e nova gravação.

## Regras de negócio

- `meeting_mode` persiste em `config.json` ao alternar o switch.
- Transcrição **detecta o idioma** falado (original no ecrã e no `.txt`).
- Se `translation_target` estiver definido (ex.: `pt`) e for diferente do detectado, mostra **tradução abaixo** do original.
- `ListenSettings.language` só **força** o idioma do Whisper se preencher o campo (evite `pt` fixo se fala noutra língua).
- Com `save_transcriptions`, grava `.txt` na pasta configurada.
- Com `auto_copy`, copia para clipboard ao concluir.

## Não inclui

- Selecção de vídeo, YouTube nem chave OpenRouter (ver outras páginas).

## Ficheiros

- `gui.py`: `_build_page_record`, `_start_recording`, `_on_transcription_complete`
