"""Background server with GUI. Manages Vosk and Whisper models.
Start once, stays alive across dictation restarts."""
import base64
import json
import os
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk
from vosk import Model, KaldiRecognizer

WHISPER_AVAILABLE = False
try:
    from faster_whisper import WhisperModel as _WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    pass

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
PORT = 9876
CONFIG_FILE = SCRIPT_DIR / "server_config.json"
HF_CACHE = Path(os.path.expanduser("~/.cache/huggingface/hub"))

LANGUAGES = {
    "en-large": {"name": "English (Gigaspeech)", "dir": "vosk-model-en-us-0.42-gigaspeech",
        "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.42-gigaspeech.zip", "size": "1.5 GB"},
    "en-small": {"name": "English (Small)", "dir": "vosk-model-small-en-us-0.15",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip", "size": "40 MB"},
    "es-large": {"name": "Spanish (Full)", "dir": "vosk-model-es-0.42",
        "url": "https://alphacephei.com/vosk/models/vosk-model-es-0.42.zip", "size": "1.5 GB"},
    "es-small": {"name": "Spanish (Small)", "dir": "vosk-model-small-es-0.42",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip", "size": "42 MB"},
}

WHISPER_SIZES = {
    "tiny": {"name": "Whisper tiny", "size": "150 MB", "desc": "Fastest"},
    "base": {"name": "Whisper base", "size": "300 MB", "desc": "Recommended"},
    "small": {"name": "Whisper small", "size": "1.0 GB", "desc": "Best quality"},
}

VAD_PRESETS = {
    "Fast (2s)": {"silence_frames": 4, "beam_size": 1},
    "Normal (4s)": {"silence_frames": 8, "beam_size": 1},
    "Accurate (8s)": {"silence_frames": 16, "beam_size": 3},
}

# ── Config ──

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"engine": "vosk", "active_model": "en-small"}
def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ── Vosk model helpers ──

def model_path(key):
    return SCRIPT_DIR / LANGUAGES[key]["dir"]
def model_is_downloaded(key):
    return model_path(key).is_dir()
def download_model(key, progress_cb=None):
    info = LANGUAGES[key]
    zip_path = SCRIPT_DIR / f"{info['dir']}.zip"
    if not zip_path.is_file():
        if progress_cb:
            progress_cb("Downloading...", 0)
        def reporthook(block, block_size, total):
            if total > 0 and progress_cb:
                pct = min(100, int(block * block_size * 100 / total))
                progress_cb(f"Downloading... {pct}%", pct)
        urllib.request.urlretrieve(info["url"], str(zip_path), reporthook=reporthook)
    if progress_cb:
        progress_cb("Extracting...", 90)
    with zipfile.ZipFile(str(zip_path)) as zf:
        zf.extractall(str(SCRIPT_DIR))
    zip_path.unlink()
    if progress_cb:
        progress_cb("Done", 100)
    return model_path(key)

# ── Whisper model helpers ──

def whisper_model_dir(key):
    return HF_CACHE / f"models--Systran--faster-whisper-{key}"
def whisper_is_downloaded(key):
    d = whisper_model_dir(key)
    return d.is_dir() and any(d.rglob("*.bin"))
def download_whisper(key, progress_cb=None):
    from huggingface_hub import snapshot_download
    if progress_cb:
        progress_cb("Starting download...", 0)
    snapshot_download(f"Systran/faster-whisper-{key}", local_files_only=False)
    if progress_cb:
        progress_cb("Done", 100)
def delete_whisper(key):
    import shutil
    d = whisper_model_dir(key)
    if d.is_dir():
        shutil.rmtree(d)

# ── VAD and Whisper engine ──

def compute_rms(data):
    if len(data) < 2:
        return 0
    count = len(data) // 2
    samples = struct.unpack("<" + "h" * count, data[:count * 2])
    mean_sq = sum(s * s for s in samples) / count
    return (mean_sq ** 0.5) / 32768.0 * 100

class WhisperEngine:
    def __init__(self, model_size="base", language=None, preset="Normal (4s)"):
        self.model_size = model_size
        self.language = language
        self.model = _WhisperModel(model_size, device="cpu", compute_type="int8")
        self.settings = VAD_PRESETS.get(preset, VAD_PRESETS["Normal (4s)"])
        self.buffer = bytearray()
        self._lock = threading.Lock()
        self._speaking = False
        self._silence_count = 0
    def reset(self):
        with self._lock:
            self.buffer.clear()
            self._speaking = False
            self._silence_count = 0
    def update_settings(self, language, preset):
        self.language = language
        self.settings = VAD_PRESETS.get(preset, VAD_PRESETS["Normal (4s)"])
    def feed(self, data, out):
        rms = compute_rms(data)
        with self._lock:
            self.buffer.extend(data)
            if rms > 0.5:
                self._speaking = True
                self._silence_count = 0
                return None
            elif self._speaking:
                self._silence_count += 1
                if self._silence_count > self.settings["silence_frames"]:
                    buf = bytes(self.buffer)
                    self.buffer.clear()
                    self._speaking = False
                    self._silence_count = 0
                    if len(buf) > 8000:
                        return buf
                    return None
        return None
    def transcribe(self, audio_bytes):
        try:
            import numpy as np
            samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            lang = None if self.language == "Auto-detect" else self.language
            segments, _ = self.model.transcribe(
                samples, beam_size=self.settings["beam_size"],
                language=lang, vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text if text else None
        except Exception as e:
            print(f"[Whisper] error: {e}", flush=True)
            return None

# ── Socket server ──

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.client_count += 1
        if self.server.status_cb:
            self.server.status_cb(self.server.client_count)
        try:
            out = self.wfile
            out.write(json.dumps({"t": "ready"}).encode() + b"\n")
            out.flush()
            print(f"[Handler] connected, engine={self.server.engine}, models={list(self.server.models.keys())}", flush=True)
            whisper_eng = None
            for line in self.rfile:
                try:
                    msg = json.loads(line.decode().strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                t = msg.get("t")
                if t == "audio":
                    data = base64.b64decode(msg["d"])
                    if self.server.engine == "vosk":
                        if not hasattr(self, '_recs') or not self._recs:
                            self._recs = {k: KaldiRecognizer(m, 16000) for k, m in self.server.models.items()}
                        recs = self._recs
                        finals = []
                        partials = []
                        for key, rec in recs.items():
                            if rec.AcceptWaveform(data):
                                result = json.loads(rec.Result())
                                text = result.get("text", "").strip()
                                if text:
                                    print(f"[Vosk] final: {text}", flush=True)
                                    finals.append((len(text.split()), text, key))
                            else:
                                partial = json.loads(rec.PartialResult())
                                ptext = partial.get("partial", "").strip()
                                if ptext:
                                    partials.append((len(ptext.split()), ptext, key))
                        if finals:
                            finals.sort(key=lambda x: x[0], reverse=True)
                            out.write(json.dumps({"t": "final", "d": finals[0][1]}).encode() + b"\n")
                            out.flush()
                        elif partials:
                            partials.sort(key=lambda x: x[0], reverse=True)
                            out.write(json.dumps({"t": "partial", "d": partials[0][1]}).encode() + b"\n")
                            out.flush()
                    else:
                        if whisper_eng is None:
                            whisper_eng = WhisperEngine(
                                self.server.whisper_size, self.server.whisper_lang,
                                self.server.whisper_preset)
                        buf = whisper_eng.feed(data, out)
                        if buf:
                            text = whisper_eng.transcribe(buf)
                            if text:
                                out.write(json.dumps({"t": "final", "d": text}).encode() + b"\n")
                                out.flush()
                elif t == "list_models":
                    models = []
                    for key in LANGUAGES:
                        models.append({"key": key, "name": LANGUAGES[key]["name"],
                            "size": LANGUAGES[key]["size"],
                            "downloaded": model_is_downloaded(key),
                            "active": key in self.server.active_keys})
                    out.write(json.dumps({"t": "models", "d": models}).encode() + b"\n")
                    out.flush()
                elif t == "get_active_model":
                    out.write(json.dumps({"t": "active_model", "d": {
                        "engine": self.server.engine, "models": self.server.active_keys,
                    }}).encode() + b"\n")
                    out.flush()
                elif t == "reset":
                    if whisper_eng:
                        whisper_eng.reset()
        finally:
            self.server.client_count -= 1
            if self.server.status_cb:
                self.server.status_cb(self.server.client_count)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self, models, active_keys, engine, addr, status_cb=None,
                 whisper_size="base", whisper_lang=None, whisper_preset="Normal (4s)"):
        self.models = models
        self.active_keys = active_keys
        self.engine = engine
        self.whisper_size = whisper_size
        self.whisper_lang = whisper_lang
        self.whisper_preset = whisper_preset
        self.client_count = 0
        self.status_cb = status_cb
        self._model_lock = threading.Lock()
        super().__init__(addr, Handler)

# ── Main GUI ──

class ServerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Dicktator Server")
        self.root.resizable(False, False)
        icon_path = SCRIPT_DIR / "icon_pixellated.png"
        if icon_path.is_file():
            self.root.iconphoto(True, ImageTk.PhotoImage(Image.open(icon_path)))
        elif (SCRIPT_DIR / "icon.png").is_file():
            self.root.iconphoto(True, ImageTk.PhotoImage(Image.open(str(SCRIPT_DIR / "icon.png"))))
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

        self._active_keys = []
        self._engine = "vosk"
        self._whisper_size = "base"
        self._whisper_lang = "Auto-detect"
        self._whisper_preset = "Normal (4s)"
        self._lang_var = tk.StringVar(value="Auto-detect")
        self._preset_var = tk.StringVar(value="Normal (4s)")

        self._setup_ui()
        self.root.update_idletasks()
        self._center_window(560, 600)
        threading.Thread(target=self._start, daemon=True).start()
        self.root.mainloop()

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # Status area
        self.status_label = ttk.Label(main, text="No model selected", font=("Segoe UI", 11))
        self.status_label.pack(anchor=tk.W)
        self.detail_label = ttk.Label(main, text="", foreground="gray", font=("Segoe UI", 9))
        self.detail_label.pack(anchor=tk.W)
        self._progress = ttk.Progressbar(main, mode="determinate", length=520)

        # ── Vosk Models ──
        self._vosk_frame = ttk.LabelFrame(main, text="Vosk Models", padding=6)
        self._vosk_frame.pack(fill=tk.X, pady=(8, 4))
        self._vosk_rows = []

        def make_btn(parent, text, fg, cmd, w=10):
            b = tk.Button(parent, text=text, fg=fg, bd=1, relief=tk.RAISED,
                          font=("Segoe UI", 8), width=w, cursor="hand2", command=cmd)
            return b

        for key in LANGUAGES:
            info = LANGUAGES[key]
            f = tk.Frame(self._vosk_frame, bg="#f8f8f8", highlightbackground="#ddd",
                         highlightthickness=1)
            f.pack(fill=tk.X, pady=2, ipady=2)

            tk.Label(f, text=info["name"], anchor=tk.W, bg="#f8f8f8",
                     font=("Segoe UI", 9), width=32).pack(side=tk.LEFT, padx=(8, 4))
            tk.Label(f, text=info["size"], fg="#888", bg="#f8f8f8",
                     font=("Segoe UI", 8), width=8).pack(side=tk.LEFT)

            del_btn = make_btn(f, "\u2716 Delete", "#cc0000",
                lambda k=key: self._delete_model("vosk", k), w=8)
            dl_btn = make_btn(f, "\u2b07 Download", "#0066cc",
                lambda k=key: self._download_model("vosk", k), w=10)
            deact_btn = make_btn(f, "\u23f9 Deactivate", "#b85c00",
                lambda k=key: self._deactivate_model(k), w=11)
            act_btn = make_btn(f, "\u25b6 Activate", "#1a7a1a",
                lambda k=key: self._activate_model(k), w=10)
            self._vosk_rows.append((key, act_btn, deact_btn, dl_btn, del_btn))

        # ── Whisper Models ──
        self._whisper_frame = ttk.LabelFrame(main, text="Whisper Models", padding=6)
        self._whisper_frame.pack(fill=tk.X, pady=(0, 4))
        self._whisper_rows = []

        for key in WHISPER_SIZES:
            info = WHISPER_SIZES[key]
            f = tk.Frame(self._whisper_frame, bg="#f0f4ff", highlightbackground="#cdf",
                         highlightthickness=1)
            f.pack(fill=tk.X, pady=2, ipady=2)

            tk.Label(f, text=info["name"], anchor=tk.W, bg="#f0f4ff",
                     font=("Segoe UI", 9), width=16).pack(side=tk.LEFT, padx=(8, 4))
            tk.Label(f, text=info["size"], fg="#888", bg="#f0f4ff",
                     font=("Segoe UI", 8), width=8).pack(side=tk.LEFT)
            tk.Label(f, text=info["desc"], fg="#888", bg="#f0f4ff",
                     font=("Segoe UI", 8), width=16).pack(side=tk.LEFT)

            del_btn = make_btn(f, "\u2716 Delete", "#cc0000",
                lambda k=key: self._delete_model("whisper", k), w=8)
            dl_btn = make_btn(f, "\u2b07 Download", "#0066cc",
                lambda k=key: self._download_model("whisper", k), w=10)
            deact_btn = make_btn(f, "\u23f9 Deactivate", "#b85c00",
                lambda k=key: self._deactivate_model(f"whisper-{k}"), w=11)
            act_btn = make_btn(f, "\u25b6 Activate", "#1a7a1a",
                lambda k=key: self._activate_model(f"whisper-{k}"), w=10)
            self._whisper_rows.append((key, act_btn, deact_btn, dl_btn, del_btn))

        # Whisper settings
        set_f = tk.Frame(self._whisper_frame, bg="#f0f4ff")
        set_f.pack(fill=tk.X, pady=(6, 2))
        tk.Label(set_f, text="Language:", bg="#f0f4ff").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Combobox(set_f, textvariable=self._lang_var,
            values=("Auto-detect", "en", "es"), width=12, state="readonly").pack(side=tk.LEFT, padx=(0, 16))
        tk.Label(set_f, text="Speed:", bg="#f0f4ff").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Combobox(set_f, textvariable=self._preset_var,
            values=("Fast (2s)", "Normal (4s)", "Accurate (8s)"), width=12, state="readonly").pack(side=tk.LEFT)

        # Bottom buttons
        btn = ttk.Frame(main)
        btn.pack(fill=tk.X, pady=(10, 0))
        self._open_btn_text = tk.StringVar(value="Open Dicktator")
        ttk.Button(btn, textvariable=self._open_btn_text,
                   command=self._open_dictation).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn, text="Quit", command=self._quit).pack(side=tk.RIGHT)

    def _refresh_all_ui(self):
        for key, act, deact, dl_btn, del_btn in self._vosk_rows:
            is_dl = model_is_downloaded(key)
            is_active = key in self._active_keys
            self._pack_row_buttons(is_active, is_dl, act, deact, dl_btn, del_btn)
        for key, act, deact, dl_btn, del_btn in self._whisper_rows:
            is_dl = whisper_is_downloaded(key)
            wkey = f"whisper-{key}"
            is_active = wkey in self._active_keys
            self._pack_row_buttons(is_active, is_dl, act, deact, dl_btn, del_btn)

    def _pack_row_buttons(self, is_active, is_downloaded, act, deact, dl_btn, del_btn):
        for b in (act, deact, dl_btn, del_btn):
            b.pack_forget()
        if is_active:
            act.config(state="disabled")
            act.pack(side=tk.RIGHT, padx=(0, 6))
            deact.config(state="normal")
            deact.pack(side=tk.RIGHT, padx=(0, 4))
            del_btn.config(state="normal")
            del_btn.pack(side=tk.RIGHT, padx=(0, 6))
        elif is_downloaded:
            act.config(state="normal")
            act.pack(side=tk.RIGHT, padx=(0, 6))
            del_btn.config(state="normal")
            del_btn.pack(side=tk.RIGHT, padx=(0, 6))
        else:
            dl_btn.config(state="normal")
            dl_btn.pack(side=tk.RIGHT, padx=(0, 6))

    def _download_model(self, key, model_key):
        self._progress.pack(fill=tk.X, pady=(4, 0))
        is_whisper = key == "whisper"
        if is_whisper and not WHISPER_AVAILABLE:
            self._set_detail("Install: pip install faster-whisper")
            return
        self._set_detail(f"Downloading...")
        self._progress["value"] = 0
        self.root.update()
        try:
            fn = download_whisper if is_whisper else download_model
            fn(model_key, progress_cb=lambda t, p: (self._set_detail(t),
                setattr(self._progress, "value", p) or self.root.update()))
            self._set_detail("Downloaded")
            self._progress["value"] = 100
        except Exception as e:
            self._set_detail(f"Error: {e}")
        self._refresh_all_ui()

    def _delete_model(self, key, model_key):
        if key == "whisper":
            delete_whisper(model_key)
        else:
            if not messagebox.askyesno("Delete", f"Delete {LANGUAGES[model_key]['name']}?\nCannot be undone."):
                return
            import shutil
            p = model_path(model_key)
            if p.is_dir():
                shutil.rmtree(p)
            zip_p = SCRIPT_DIR / f"{LANGUAGES[model_key]['dir']}.zip"
            if zip_p.is_file():
                zip_p.unlink()
        self._refresh_all_ui()

    def _deactivate_model(self, key):
        if self._engine == "vosk" and key in self._active_keys:
            self._active_keys = []
            self._set_status("Deactivated")
            self._set_detail("No active model")
        elif self._engine == "whisper" and key == f"whisper-{self._whisper_size}":
            self._active_keys = []
            self._set_status("Deactivated")
            self._set_detail("No active model")
        self._update_open_btn_text()
        self._refresh_all_ui()

    def _update_open_btn_text(self):
        if self._engine == "vosk" and self._active_keys:
            key = self._active_keys[0]
            self._open_btn_text.set(f"Open Dicktator with Vosk: {LANGUAGES[key]['name']}")
        elif self._engine == "whisper" and self._active_keys:
            self._open_btn_text.set(f"Open Dicktator with Whisper {self._whisper_size} ({self._whisper_lang})")
        else:
            self._open_btn_text.set("Open Dicktator")

    def _center_window(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_label.config(text=text))
    def _set_detail(self, text):
        self.root.after(0, lambda: self.detail_label.config(text=text))

    def _on_client_change(self, count):
        self.root.after(0, lambda: self.detail_label.config(
            text=f"Port {PORT} | {count} client{'s' if count != 1 else ''} connected"))

    def _start(self):
        config = load_config()
        self._engine = config.get("engine", "vosk")
        self._whisper_size = config.get("whisper_size", "base")
        self._whisper_lang = config.get("whisper_lang", "Auto-detect")
        self._whisper_preset = config.get("whisper_preset", "Normal (4s)")
        self.root.after(0, self._apply_start_config)

    def _apply_start_config(self):
        self._lang_var.set(self._whisper_lang)
        self._preset_var.set(self._whisper_preset)
        if self._engine == "vosk":
            target = load_config().get("active_model", "")
            if target in LANGUAGES and model_is_downloaded(target):
                self.detail_label.config(text=f"Previously: {LANGUAGES[target]['name']} — click Activate")
        else:
            self.detail_label.config(text=f"Previously: Whisper {self._whisper_size} — click Activate")
        self._refresh_all_ui()

    def _activate_model(self, key=None):
        if not key:
            return
        if key.startswith("whisper-"):
            wsize = key.replace("whisper-", "")
            if not WHISPER_AVAILABLE:
                self._set_detail("Install: pip install faster-whisper")
                return
            if not whisper_is_downloaded(wsize):
                self._set_detail(f"Download Whisper {wsize} first")
                return
            self._engine = "whisper"
            self._whisper_size = wsize
            self._whisper_lang = self._lang_var.get()
            self._whisper_preset = self._preset_var.get()
            self._set_status("Loading Whisper (background)...")
            self._set_detail("UI stays responsive while loading")
            save_config({"engine": "whisper", "whisper_size": wsize,
                "whisper_lang": self._whisper_lang, "whisper_preset": self._whisper_preset})
            self._active_keys = [key]
            self._finish_activation({}, key)
        else:
            if not model_is_downloaded(key):
                self._set_detail(f"Download {LANGUAGES[key]['name']} first")
                return
            self._engine = "vosk"
            self._set_status("Loading Vosk model (background)...")
            self._set_detail("UI stays responsive while loading")
            save_config({"engine": "vosk", "active_model": key})
            self._active_keys = [key]
            # Load model in background so the UI doesn't freeze
            threading.Thread(target=self._load_vosk_and_finish, args=(key,), daemon=True).start()

    def _load_vosk_and_finish(self, key):
        """Load Vosk model in a background thread, then finish activation on main thread."""
        try:
            self._set_status("Loading Vosk model (background)...")
            model = Model(str(model_path(key)))
            models = {key: model}
            self.root.after(0, lambda: self._finish_activation(models, key))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.root.after(0, lambda: self._set_detail(
                f"Model load error: {type(e).__name__}: {e}"))

    def _finish_activation(self, models, key):
        """Create/update server and set UI to Ready (runs on main thread)."""
        try:
            if hasattr(self, "_server"):
                self._server.models = models
                self._server.active_keys = self._active_keys
                self._server.engine = self._engine
                self._server.whisper_size = self._whisper_size
                self._server.whisper_lang = self._whisper_lang
                self._server.whisper_preset = self._whisper_preset
            else:
                addr = ("127.0.0.1", PORT)
                self._server = Server(models, self._active_keys, self._engine, addr,
                    status_cb=self._on_client_change,
                    whisper_size=self._whisper_size, whisper_lang=self._whisper_lang,
                    whisper_preset=self._whisper_preset)
                self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
                self._thread.start()

            if self._engine == "whisper":
                threading.Thread(target=self._preload_whisper, daemon=True).start()

            self._set_status(f"Ready - {key}")
            self._set_detail(f"Port {PORT} | 0 clients")
            self._update_open_btn_text()
            self._refresh_all_ui()
            print(f"[Activate] {key} ready on port {PORT}", flush=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_status("Activation Error!")
            self._set_detail(f"{type(e).__name__}: {e}")

    def _preload_whisper(self):
        try:
            WhisperEngine(self._whisper_size, self._whisper_lang, self._whisper_preset)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._set_detail(f"Whisper preload error: {e}")

    def _open_dictation(self):
        subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / "dicktator.py")],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _quit(self):
        if hasattr(self, "_server"):
            self._server.shutdown()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        os._exit(0)

if __name__ == "__main__":
    # Single-instance enforcement via a held named mutex.
    # First instance owns it (bInitialOwner=True). A second instance detects
    # it is already owned (WAIT_TIMEOUT) and exits. If the previous owner
    # crashed, the mutex is abandoned (WAIT_ABANDONED) and we take over.
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hmutex = kernel32.CreateMutexW(None, True, "DicktatorServerSingleton")
        if hmutex and kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            res = kernel32.WaitForSingleObject(hmutex, 0)
            if res == 0x00000102:  # WAIT_TIMEOUT — a live instance holds it
                print("Server already running", flush=True)
                sys.exit(0)
            # WAIT_ABANDONED (0x80) — previous owner died; we now own it
        elif not hmutex:
            print("Failed to acquire single-instance mutex", flush=True)
            sys.exit(1)
    except Exception as e:
        print(f"Mutex check skipped: {e}", flush=True)
    ServerApp()
