# Página: Vídeo (`video`)

## Propósito

Escolher ficheiro de vídeo local, transcrever com Whisper e gerar pasta com `.txt` + `indice.txt`.

Dois modos:

- **Tópicos (predefinido):** agrupa por pausas longas entre falas.
- **Google Meet:** transcrição cronológica — uma linha por frase: `[início – fim] texto` em `transcricao.txt` (sem separar por participante).

## Regras de UI

- Campo de caminho (clique abre seletor) + **Escolher vídeo**.
- Switch **Modo Google Meet** (persistido em `config.json` → `video_google_meet_mode`).
- Com Meet activo: campo **link** (Google Drive, YouTube) — download automático via yt-dlp; ou ficheiro local.
- Botão **Gerar tópicos** ou **Analisar reunião (Meet)** conforme o switch.
- Seletor: `Gtk.FileChooserNative` via `_present_native_file_chooser` (referência viva até fechar).
- **Escolher vídeo** pode ser usado antes do modelo Whisper terminar de carregar; **Gerar tópicos** exige modelo pronto.
- `video_result_label` mostra progresso/resultado.

## Regras de negócio

1. Na thread **principal**, antes do worker: `_sync_openrouter_prefs_to_settings()` → `ai_opts = _meeting_ai_options()` (ignorado no modo Meet).
2. Worker chama `analyze_video_to_topics(..., google_meet_mode=...)` — **não** ler widgets GTK no worker.
3. Requer `ffmpeg` e pasta de salvamento configurada.
4. Pasta de saída:
   - Tópicos: `reuniao_{nome}_{AAAA-MM-DD}_{HH-MM-SS}`
   - Meet: `reuniao_meet_{nome}_{AAAA-MM-DD}_{HH-MM-SS}` com `transcricao.txt`
5. Modo tópicos: `indice.txt` com IA (OpenRouter/Cursor) ou índice automático (ver `openrouter-ai.md`).
6. Modo Meet — **link**: gravação no **Google Drive** (Meet → Gravações / e-mail do Google). Não use o link `meet.google.com` da reunião ao vivo.
7. Modo Meet: download temporário (yt-dlp) se for link; Whisper com segmentos; saída só `transcricao.txt`.

**CLI:** `listen --meeting-url 'https://drive.google.com/file/d/…' --google-meet-mode`

## Ficheiros

- `gui.py`: `_build_page_video`, `_open_video_file_chooser`, `_start_video_analysis`, `_start_video_url_analysis`
- `meeting_analysis.py`: `analyze_video_to_topics`, `analyze_video_google_meet`
- `google_meet_analysis.py`: `save_chronological_transcription`
