# OpenRouter — indice.txt por IA

## Quando a API é chamada

- Só após transcrição Whisper e agrupamento em tópicos.
- Só se `meeting_ai_summary` / `MeetingAISummaryOptions.enabled` for `true` **e** `cursor_mode` for `false` (`provider == "openrouter"`).
- Com **Modo Cursor** activo, usa `meeting_summary_cursor` em vez deste módulo.
- Ficheiro: `meeting_summary_ai.generate_meeting_indice_ai()`.

## Request (equivalente)

- `POST https://openrouter.ai/api/v1/chat/completions`
- Headers: `Authorization: Bearer <chave>`, `HTTP-Referer`, `X-Title: Listen`
- Body: `model` = `resolved_ai_model()`, `messages` system+user, `max_tokens` 4000

Implementação: SDK `openai` com `base_url` de config (não curl no app).

## Modelo

- Fonte: `openrouter_model` em `config.json` (campo **Modelo OpenRouter** na GUI).
- **Não** usar o dropdown Whisper do header.
- Normalização: só adiciona prefixo `openai/` se o nome começar por `gpt-` sem `/`.

## Chave API

Ordem: `openrouter_api_key` em config → `LISTEN_OPENROUTER_API_KEY` → `OPENROUTER_API_KEY`.

## Saída

- Cabeçalho do `indice.txt`: data/hora local, pasta, modelo Whisper, modelo OpenRouter.
- Secção **Dados salvos nesta pasta** (sempre, a partir dos `.txt` gravados).
- Sucesso IA: depois **Resumo executivo** e **Índice detalhado** gerados pelo modelo configurado.
- Erro API: secção **Aviso — IA** + índice automático de fallback.
- IA desactivada: nota no `indice.txt` + índice automático.

## Dependência

`openai` faz parte de `pip install -e .` (raiz do projeto). Se faltar: `pip install openai>=1.40.0` no mesmo Python que corre o `listen`.

## Ficheiros

- `meeting_summary_ai.py`
- `meeting_analysis.py` (`analyze_media_to_topics`, `_build_indice_content`)
