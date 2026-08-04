# Dicktator

Voice-controlled dictation for Windows. Talk, and your words land anywhere — a terminal, a game, a text file — without breaking focus.

Use it to dictate notes to an AI while playing a game, or narrate your screen in real time. The whole point: never stop what you're doing to type.

## Features

- **Always-on microphone** with voice-driven commands (start/stop transcription, send prompt, period)
- **Two speech engines**, switchable at runtime:
  - **Vosk** — fast streaming, low latency, 4 models (EN/ES, small/large)
  - **Whisper** (faster-whisper) — bilingual, understands mixed-language speech
- **Send text to any window** without touching the keyboard — minimize + paste, works even from fullscreen games
- **Write-to-file mode** — append transcriptions to a `.txt` instead of stealing focus
- **Live Stream panel** — watch the model transcribe in real time
- **Bilingual UI** — English and Spanish interface + voice commands
- **Model manager** — download, delete, and switch models from the server window
- **Return-to-previous-app** — after sending, jump back to what you were doing

## Architecture

Two processes talk over a local socket (port 9876):

- **`dicktator_server.py`** — the engine. Loads the Vosk/Whisper model, exposes a GUI to manage models, and streams transcriptions.
- **`dicktator.py`** — the client. Captures microphone audio, sends it to the server, shows the buffer/history/stream, and sends text to your target window.

The server keeps the model loaded in memory, so restarting the client is instant.

## Setup

```bash
pip install -r requirements.txt
```

Then start the server (it downloads models on first use through **Manage Models**):

```bash
python dicktator_server.py
```

Click **Open Dicktator** (or run `dicktator.py`) to launch the client.

> Vosk models download automatically into the project folder via the **Manage Models** dialog. Whisper models use the HuggingFace cache. `run.bat` starts both apps.

## Voice Commands

Say these into the mic (English and Spanish both work):

| Action | English | Español |
|--------|---------|---------|
| Start transcribing | "start transcription" / "start" / "begin" | "iniciar transcripción" / "iniciar" / "comenzar" |
| Stop transcribing | "stop transcription" / "stop" / "end" | "detener transcripción" / "detener" / "parar" |
| Send buffer | "send prompt" / "send" / "sent" / "sand" | "enviar mensaje" / "enviar" / "mandar" |
| Add a period | "period" | "punto" / "período" |

Say "send prompt" while playing a game and the text lands in your target window — then it returns you to the game.

## Options

- **Return to previous app** — restore focus to the calling window after sending
- **Write to file** — append transcriptions to a text file instead of sending to a window (with newline mode and `[HH:MM]` timestamps)
- **Style / Language** — change the UI theme and interface language on the fly

## Files

| File | Purpose |
|------|---------|
| `dicktator.py` | Client app (mic, buffer, send) |
| `dicktator_server.py` | Engine + model manager GUI |
| `send_to_console.py` | Writes text directly to another process's console |
| `target_sender.py` | Standalone target-send tester |
| `mic_test.py` / `sound_test.py` | Audio debug scripts |
| `run.bat` | Launch server + client together |

## License

Free to use, modify, and share.
