# Página: Vídeo (`video`)

## Propósito

Escolher ficheiro de vídeo local, transcrever com Whisper, agrupar tópicos e gerar pasta com `.txt` + `indice.txt`.

## Regras de UI

- Campo de caminho (clique abre seletor) + **Escolher vídeo** + **Gerar tópicos**.
- Seletor: `Gtk.FileChooserNative` via `_present_native_file_chooser` (referência viva até fechar).
- **Escolher vídeo** pode ser usado antes do modelo Whisper terminar de carregar; **Gerar tópicos** exige modelo pronto.
- `video_result_label` mostra progresso/resultado.

## Regras de negócio

1. Na thread **principal**, antes do worker: `_sync_openrouter_prefs_to_settings()` → `ai_opts = _meeting_ai_options()`.
2. Worker chama `analyze_video_to_topics(..., ai_summary_options=ai_opts)` — **não** ler widgets GTK no worker.
3. Requer `ffmpeg` e pasta de salvamento configurada.
4. Pasta de saída: `reuniao_{nome}_{AAAA-MM-DD}_{HH-MM-SS}` (data/hora locais).
5. `indice.txt` começa com data/hora local, modelos Whisper e OpenRouter, secção **Dados salvos** (ficheiros `.txt`), depois resumo/índice (IA ou automático).
6. Se IA activa: após transcrição chama OpenRouter com modelo de `openrouter_model` em config (ver `openrouter-ai.md`).

## Ficheiros

- `gui.py`: `_build_page_video`, `_open_video_file_chooser`, `_start_video_analysis`
- `meeting_analysis.py`: `analyze_video_to_topics`, `analyze_media_to_topics`
