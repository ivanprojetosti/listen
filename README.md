<p align="center">
  <img src="appimage/listen.png" alt="Listen Logo" width="128" height="128">
</p>

<h1 align="center">Listen</h1>

<p align="center">
  <strong>Transcrição de voz para texto no Linux</strong><br>
  Rápido, privado e offline — powered by Whisper AI
</p>

<p align="center">
  <a href="https://github.com/abubakerKhaled/listen/releases"><img src="https://img.shields.io/github/v/release/abubakerKhaled/listen?style=flat-square" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
  <a href="#"><img src="https://img.shields.io/badge/platform-Linux-orange?style=flat-square" alt="Platform"></a>
</p>

---

## O que é

O **Listen** grava áudio do microfone (ou microfone + sistema no modo reunião), transcreve localmente com [faster-whisper](https://github.com/SYSTRAN/faster-whisper) e pode copiar o texto para a área de transferência e salvar em `.txt`.

Funciona **sem internet** após o primeiro download do modelo de IA.

---

## Início rápido (desenvolvimento / código-fonte)

Use este fluxo se você clonou o repositório e quer rodar a partir do código.

### 1. Dependências do sistema (Ubuntu / Debian / Pop!_OS)

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  libportaudio2 portaudio19-dev \
  ffmpeg \
  python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  libgtk-4-1 libadwaita-1-0
```

### 2. Criar ambiente virtual e instalar

```bash
cd listen
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate

pip install --upgrade pip wheel packaging hatchling
pip install -e .
```

> O `--system-site-packages` é necessário para o GTK (`gi`) funcionar com a interface gráfica.

### 3. Configurar atalho e pasta de salvamento

```bash
listen --configure
```

Você define:

- **Atalho global** (ex.: `ctrl+shift+l`, `ctrl+alt+space`)
- **Pasta** onde os `.txt` serão salvos
- Se a janela abre no **canto inferior direito**

A config fica em `~/.config/listen/config.json`.

### 4. Iniciar em segundo plano (recomendado)

```bash
listen --daemon
```

Deixe esse terminal aberto. O Listen fica em segundo plano e responde ao atalho.

### 5. Usar o atalho (dois passos)

| Passo | Ação |
|-------|------|
| **1º** atalho | Abre a janela e **começa a gravar** |
| **2º** atalho | **Para**, transcreve e **salva** o `.txt` |

Exemplo com atalho `ctrl+shift+l`:

1. Pressione `Ctrl+Shift+L` e fale por alguns segundos
2. Pressione `Ctrl+Shift+L` de novo
3. O texto aparece na janela e é salvo em `~/.local/share/listen/transcriptions/` (ou na pasta que você configurou)

### 6. Verificar configuração

```bash
listen --show-config
```

---

## Modos de execução

| Comando | O que faz |
|---------|-----------|
| `listen` | Interface gráfica normal (janela central) |
| `listen --daemon` | Segundo plano + atalho global + salvar `.txt` |
| `listen --quick` | Janela no canto, grava ao abrir, salva `.txt` |
| `listen --configure` | Assistente de configuração |
| `listen --show-config` | Mostra a config atual |
| `listen --cli` | Modo terminal (sem GUI) |

### Opções úteis

| Opção | Descrição |
|-------|-----------|
| `--model, -m` | Modelo: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `--hotkey` | Define atalho e salva na config |
| `--save-dir` | Define pasta de salvamento |
| `--no-copy` | Não copia automaticamente para a área de transferência |
| `--no-save` | Não salva arquivos `.txt` |

### Exemplos

```bash
listen --daemon
listen --configure
listen --model small --daemon
listen --cli --toggle
```

---

## Instalação para usuário final (AppImage)

Se você **não** vai desenvolver, pode instalar o binário pronto:

```bash
git clone https://github.com/abubakerKhaled/listen.git
cd listen
./setup.sh
```

Ou via PPA (Ubuntu/Debian):

```bash
sudo add-apt-repository ppa:abubakrkhaled1/listen
sudo apt update
sudo apt install listen
```

Na primeira execução o modelo de IA é baixado (~40–150 MB). Depois disso fica instantâneo.

---

## Configuração (`~/.config/listen/config.json`)

Exemplo:

```json
{
  "hotkey": "ctrl+shift+l",
  "save_directory": "/home/SEU_USUARIO/.local/share/listen/transcriptions",
  "corner_mode": true,
  "auto_record": true,
  "auto_copy": true,
  "save_transcriptions": true,
  "meeting_mode": false,
  "language": "pt"
}
```

| Campo | Descrição |
|-------|-----------|
| `hotkey` | Atalho global (modo `--daemon`) |
| `save_directory` | Pasta dos arquivos `.txt` |
| `corner_mode` | Janela no canto inferior direito |
| `meeting_mode` | Grava microfone **e** áudio do sistema |
| `language` | Idioma forçado na transcrição (`pt`, `en`, etc.) ou omita para detecção automática |

---

## Recursos

| Recurso | Descrição |
|---------|-----------|
| Interface GTK4 | Visualização de forma de onda em tempo real |
| Gravação flexível | Botão na GUI ou atalho global |
| IA local | faster-whisper, sem enviar áudio para a nuvem |
| Área de transferência | Texto copiado automaticamente após transcrever |
| Modelo automático | Escolhe modelo conforme GPU/CPU |
| Multilíngue | Detecção de idioma; suporte reforçado a árabe |

### Seleção automática de modelo

| Hardware | Modelo padrão |
|----------|---------------|
| GPU 4 GB+ VRAM | `medium` |
| GPU 2 GB+ VRAM | `small` |
| GPU &lt; 2 GB VRAM | `base` |
| Só CPU | `tiny` |

---

## Build AppImage (opcional)

```bash
sudo apt install libportaudio2 portaudio19-dev python3-venv \
                 libgtk-4-1 libadwaita-1-0 gir1.2-gtk-4.0 gir1.2-adw-1
./build-appimage.sh
```

Gera `listen-1.0.0-x86_64.AppImage` na pasta do projeto.

---

## Solução de problemas

| Problema | O que fazer |
|----------|-------------|
| Atalho não funciona | Use `listen --daemon` (não basta `listen --configure`) |
| Grava mas não salva | Pressione o atalho **duas vezes** (iniciar → parar) |
| `pip install -e .` falha | Rode `pip install --upgrade pip packaging hatchling` antes |
| Erro de Unicode / `0xc3` | O Listen já força UTF-8 ao iniciar; reinicie o processo após atualizar |
| "Nenhum áudio capturado" | Verifique o microfone: `pactl get-default-source` e `arecord -l` |
| Microfone suspenso | O Listen tenta ativar a entrada padrão antes de gravar |
| Transcrição vazia | Fale mais tempo, mais perto do mic; teste com `"language": "pt"` na config |
| Modo reunião estranho | Desative `meeting_mode` se só quiser o microfone |
| Primeira execução lenta | Download do modelo na primeira vez — normal |
| CPU lenta | `listen --model tiny --daemon` |
| Atalho no Wayland | Pode exigir permissões extras; no X11 costuma funcionar direto |

### Reset completo do ambiente de desenvolvimento

```bash
cd listen
pkill -f 'listen --daemon' 2>/dev/null || true
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip wheel packaging hatchling
pip install -e .
listen --configure
listen --daemon
```

---

## Desinstalar

```bash
./uninstall.sh
```

Ou manualmente:

```bash
rm ~/.local/bin/listen
rm ~/.local/share/applications/listen.desktop
rm -f ~/.local/share/icons/hicolor/*/apps/listen.png
sudo rm -f /usr/local/bin/listen
```

---

## Contribuindo

Pull requests são bem-vindos. Veja também `docs/contributing.md`.

---

## Licença

[Apache License 2.0](LICENSE)
