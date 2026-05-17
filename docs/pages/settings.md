# Página: Configurações (`settings`)

## Propósito

Configuração visual equivalente a `listen --configure` + OpenRouter.

## Campos

| Campo | Chave em `config.json` |
|-------|-------------------------|
| Atalho global | `hotkey` |
| Pasta .txt | `save_directory` |
| Canto inferior direito | `corner_mode` |
| Gravar ao abrir | `auto_record` |
| Salvar transcrições | `save_transcriptions` |
| Copiar automaticamente | `auto_copy` |
| Idioma Whisper (forçar) | `language` (vazio = detectar) |
| Traduzir para | `translation_target` (ex.: `pt`; vazio = sem tradução) |
| IA gera indice | `meeting_ai_summary` |
| Modo Cursor (local) | `cursor_mode` |
| Modelo Cursor (opcional) | `cursor_model` |
| CLI Cursor | `cursor_cli` (default `cursor`) |
| Chave OpenRouter | `openrouter_api_key` |
| Modelo OpenRouter | `openrouter_model` |
| URL API | `openai_base_url` (default OpenRouter) |

## Regras de UI

- **Guardar configurações** grava `config.json` e aplica hotkey/canto se aplicável.
- **Modo Cursor**: quando activo, campos OpenRouter ficam insensíveis; usa Cursor Agent local (ver `cursor-mode.md`).
- **Testar Cursor** / **Testar OpenRouter** com valores actuais dos campos (sem precisar guardar antes).
- Botão **Pasta…** usa `FileChooserNative` com referência viva.

## Regras de negócio

- Modelo OpenRouter: usar exactamente o texto do campo (ex. `anthropic/claude-3.5-sonnet`, `google/gemini-flash-1.5`).
- Vídeo/YouTube leem prefs via `_sync_openrouter_prefs_to_settings()` mesmo sem clicar Guardar (sincroniza widgets → `_settings` na thread principal).
- Teste CLI: `listen --test-openrouter`

## Ficheiros

- `gui.py`: `_build_page_settings`, `_on_save_settings_clicked`, `_sync_openrouter_prefs_to_settings`
- `settings.py`: `ListenSettings`
