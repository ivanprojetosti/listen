# Página: YouTube (`youtube`)

## Propósito

Analisar URL do YouTube: download (yt-dlp), transcrição, tópicos e `indice.txt`.

## Regras de UI

- Campo URL + botão **Analisar YouTube**.
- `youtube_result_label` para estado/resultado.

## Regras de negócio

1. Validar URL com `ensure_youtube_download_url`.
2. Requer `yt-dlp`, `ffmpeg` e pasta de salvamento.
3. Mesmo fluxo de IA que Vídeo: `_sync_openrouter_prefs_to_settings()` + `ai_opts` na thread principal (`resolved_ai_provider()` → Cursor ou OpenRouter).
4. Com **Modo Cursor** activo, `enabled` fica true mesmo sem o switch OpenRouter; o worker usa `generate_meeting_indice_cursor`.
5. Worker: `analyze_youtube_to_topics(..., ai_summary_options=ai_opts)`.

## Ficheiros

- `gui.py`: `_build_page_youtube`, `_on_youtube_analyze_clicked`
- `meeting_analysis.py`: `analyze_youtube_to_topics`
