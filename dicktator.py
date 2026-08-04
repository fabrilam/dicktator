"""
Voice-command dictation app. Always-on mic, voice-driven.

Commands (filtered from output):
  "start/begin/record"       - Begin capturing into buffer
  "stop/end/finish"          - Stop capturing
  "send/send prompt/submit"  - Type buffer + Enter into active window
"""

import ctypes
import ctypes.wintypes as w
import json


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("flags", w.DWORD),
        ("hwndActive", w.HWND),
        ("hwndFocus", w.HWND),
        ("hwndCapture", w.HWND),
        ("hwndMenuOwner", w.HWND),
        ("hwndMoveSize", w.HWND),
        ("hwndCaret", w.HWND),
        ("rcCaret", w.RECT),
    ]


import base64
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import winsound
from pathlib import Path

from PIL import Image, ImageTk
import keyboard
import sounddevice as sd
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox


SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

LOCALE = {
    "en": {
        "dictation_title": "Dicktator",
        "status_idle": "Status: \u25cb Idle",
        "status_recording": "Status: \u25cf Recording",
        "device": "Device:", "target": "Target:", "style": "Style:", "lang": "Lang:",
        "buffer": "Buffer:", "history": "History",
        "start": "\u25b6 Start", "stop": "\u25a0 Stop", "send": "\u23ce Send",
        "clear": "\u2715 Clear", "clear_history": "\u2630 Clear History",
        "return_to_prev": "Return to previous app",
        "write_to_file": "Write to file",
        "append": "Append:", "new_line": "New line", "same_line": "Same line",
        "include_timestamp": "Include timestamp [HH:MM]",
        "browse": "Browse",
        "commands_hint": "",
        "sent_console": "Sent via console helper.", "sent_edit": "Sent via edit child.",
        "sent_focus": "Sent via focus + paste.",
        "written_to_file": "Written to", "file_error": "File error:",
        "no_file_path": "No file path set.", "nothing_to_send": "Nothing to send.",
        "model": "Model:",
        "server_starting": "Starting...",
        "server_connecting": "Connecting to server...",
        "server_starting_app": "Starting server...",
        "server_failed": "Server failed to start. Restart the app.",
        "no_target": "No target selected.", "target_closed": "Target window closed.",
        "no_devices": "No input device available.",
    },
    "es": {
        "dictation_title": "Dicktator",
        "status_idle": "Estado: \u25cb Inactivo",
        "status_recording": "Estado: \u25cf Grabando",
        "device": "Dispositivo:", "target": "Destino:", "style": "Estilo:", "lang": "Idioma:",
        "buffer": "B\u00fafer:", "history": "Historial",
        "start": "\u25b6 Iniciar", "stop": "\u25a0 Detener", "send": "\u23ce Enviar",
        "clear": "\u2715 Limpiar", "clear_history": "\u2630 Limpiar Hist.",
        "return_to_prev": "Volver a la app anterior",
        "write_to_file": "Escribir a archivo",
        "append": "A\u00f1adir:", "new_line": "Nueva l\u00ednea", "same_line": "Misma l\u00ednea",
        "include_timestamp": "Incluir marca [HH:MM]",
        "browse": "Examinar",
        "commands_hint": "",
        "sent_console": "Enviado v\u00eda consola.", "sent_edit": "Enviado v\u00eda editor.",
        "sent_focus": "Enviado v\u00eda foco + pegar.",
        "written_to_file": "Escrito a", "file_error": "Error de archivo:",
        "no_file_path": "No hay ruta de archivo.", "nothing_to_send": "Nada que enviar.",
        "model": "Modelo:",
        "server_starting": "Iniciando...",
        "server_connecting": "Conectando al servidor...",
        "server_starting_app": "Iniciando servidor...",
        "server_failed": "Error al iniciar servidor. Reinicie la app.",
        "no_target": "Ning\u00fan destino seleccionado.", "target_closed": "Ventana destino cerrada.",
        "no_devices": "No hay dispositivo de entrada disponible.",
    },
}

COMMAND_MAP_EN = [
    ("start transcription", "start"), ("start trans", "start"),
    ("start recording", "start"), ("begin transcription", "start"),
    ("begin recording", "start"), ("start", "start"),
    ("begin", "start"), ("record", "start"),
    ("stop transcription", "stop"), ("stop trans", "stop"),
    ("stop recording", "stop"), ("end transcription", "stop"),
    ("end recording", "stop"), ("stop", "stop"),
    ("end", "stop"), ("finish", "stop"),
    ("send the prompt", "send"), ("send prompt", "send"),
    ("send it", "send"), ("sent the prompt", "send"),
    ("sent prompt", "send"), ("sent it", "send"),
    ("sand the prompt", "send"), ("sand prompt", "send"),
    ("san prompt", "send"), ("sun prompt", "send"),
    ("send this", "send"), ("submit prompt", "send"),
    ("send", "send"), ("sent", "send"), ("sen", "send"), ("sand", "send"), ("submit", "send"),
]

COMMAND_MAP_ES = [
    ("iniciar transcripci\u00f3n", "start"), ("iniciar trans", "start"),
    ("comenzar transcripci\u00f3n", "start"), ("comenzar grabaci\u00f3n", "start"),
    ("iniciar grabaci\u00f3n", "start"), ("comenzar", "start"),
    ("iniciar", "start"), ("grabar", "start"),
    ("detener transcripci\u00f3n", "stop"), ("detener trans", "stop"),
    ("detener grabaci\u00f3n", "stop"), ("terminar transcripci\u00f3n", "stop"),
    ("terminar grabaci\u00f3n", "stop"), ("detener", "stop"),
    ("terminar", "stop"), ("parar", "stop"), ("finalizar", "stop"),
    ("enviar el mensaje", "send"), ("enviar mensaje", "send"),
    ("enviar", "send"), ("mandar", "send"),
    ("manda", "send"), ("mandar mensaje", "send"),
    ("per\u00edodo", "period"), ("punto", "period"),
]

# Unified bilingual command map — works regardless of which voice model is active
COMMAND_MAP_BI = COMMAND_MAP_EN + [
    p for p in COMMAND_MAP_ES if p[0] not in {e[0] for e in COMMAND_MAP_EN}
]

_curr_lang = "en"
_curr_map = COMMAND_MAP_BI

def _(key):
    return LOCALE.get(_curr_lang, LOCALE["en"]).get(key, key)

def _set_lang(lang):
    global _curr_lang, _curr_map
    _curr_lang = lang
    _curr_map = COMMAND_MAP_BI


SERVER_PORT = 9876


class DictationApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Dicktator")
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        icon_path = SCRIPT_DIR / "icon_pixellated.png"
        if icon_path.is_file():
            self.root.iconphoto(True, ImageTk.PhotoImage(Image.open(icon_path)))
        elif (SCRIPT_DIR / "icon.png").is_file():
            self.root.iconphoto(True, ImageTk.PhotoImage(Image.open(str(SCRIPT_DIR / "icon.png"))))

        self.stream = None
        self.capturing = False
        self.buffer = ""
        self._server_sock = None
        self._device_id = None
        self._input_devices = []
        self._running = True
        self._pending_send = False
        self._send_text = ""
        self._dirty = False
        self._model_label_text = tk.StringVar(value="")
        self._partial_text = ""
        self._history = []
        self._max_history = 50
        self._do_cap = False
        self._target_hwnd = None
        self._window_list = []
        self._buffer_lock = threading.Lock()
        self._return_to_prev = tk.BooleanVar(value=False)
        self._write_to_file = tk.BooleanVar(value=False)
        self._file_path = tk.StringVar(value=str(SCRIPT_DIR / "dictation_output.txt"))
        self._file_newline = tk.StringVar(value="newline")
        self._file_timestamp = tk.BooleanVar(value=True)
        self._audio_queue = queue.Queue()
        self._last_send_time = 0
        self._last_rms = 0
        self._worker = threading.Thread(target=self._audio_worker, daemon=True)
        self._worker.start()

        self._splash_start = time.time()
        self._show_splash()

        threading.Thread(target=self._connect_server, daemon=True).start()
        self.root.mainloop()

    def _center_window(self, win, w, h):
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _schedule_build_ui(self):
        elapsed = time.time() - self._splash_start
        delay = max(0, 1.5 - elapsed)
        self.root.after(int(delay * 1000), self._build_ui)

    def _show_splash(self):
        splash_path = SCRIPT_DIR / "splash_pixellated.png"
        if not splash_path.is_file():
            splash_path = SCRIPT_DIR / "icon.png"
        if not splash_path.is_file():
            self._splash = None
            return

        self._splash = tk.Toplevel(self.root)
        self._splash.overrideredirect(True)
        self._splash.attributes("-topmost", True)
        img = ImageTk.PhotoImage(Image.open(splash_path).resize((256, 256)))
        self._splash_img = img
        ttk.Label(self._splash, image=img).pack()
        self._splash_text = tk.StringVar(self._splash, "")
        self._splash_text_label = ttk.Label(self._splash, textvariable=self._splash_text,
                  font=("Segoe UI", 9), foreground="#555",
                  wraplength=260, justify=tk.CENTER,
                  anchor=tk.CENTER)
        self._splash_text_label.pack(pady=(4, 0), padx=10)
        ttk.Label(self._splash, text="(Esc to close)",
                  foreground="#aaa", font=("Segoe UI", 7)).pack(pady=(2, 0))
        self._splash.bind("<Escape>", lambda e: self._close_and_exit())
        self._splash.bind("<Button-1>", lambda e: self._close_and_exit())
        self._center_splash()
        self.root.update()

    def _center_splash(self):
        """Size the splash to its content and center it on screen."""
        if not hasattr(self, '_splash') or not self._splash:
            return
        try:
            self._splash.update_idletasks()
            w = self._splash.winfo_reqwidth()
            h = self._splash.winfo_reqheight()
            sw = self._splash.winfo_screenwidth()
            sh = self._splash.winfo_screenheight()
            self._splash.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        except tk.TclError:
            pass

    def _set_splash(self, text):
        try:
            print(f"[Splash] {text}")
        except Exception:
            pass
        if hasattr(self, '_splash_text') and self._splash_text:
            self._splash_text.set(text)
            self._center_splash()

    def _splash_ask_model(self):
        """Show an explicit red request for a voice model on the splash."""
        self._set_splash(
            "\u26a0 NO VOICE MODEL ACTIVE \u26a0\n\n"
            "Open the Dicktator Server\n"
            "and click ACTIVATE on a model.\n\n"
            "This window opens automatically\n"
            "once a model is running.")
        if hasattr(self, '_splash_text_label'):
            try:
                self._splash_text_label.config(foreground="#cc0000")
            except tk.TclError:
                pass

    def _stop_splash_blink(self):
        # Blink was removed; kept as a no-op for compatibility.
        pass

    def _update_model_labels(self, label):
        if hasattr(self, 'model_label'):
            try:
                self.model_label.config(text=label)
            except tk.TclError:
                pass
        if hasattr(self, 'stream_engine_label'):
            try:
                self.stream_engine_label.config(text=label)
            except tk.TclError:
                pass

    def _splash_model_loop(self):
        """Poll the server for an active model while the splash is showing."""
        if getattr(self, '_ui_built', False) or not self._running:
            return
        self._query_active_model()
        self.root.after(2000, self._splash_model_loop)

    def _handle_disconnect(self):
        """Server connection lost (closed or deactivated) — ask for a model."""
        print("[Disconnect] server connection lost", flush=True)
        self._set_no_model(True)
        if not getattr(self, '_ui_built', False):
            self._splash_ask_model()
            self._splash_model_loop()

    def _server_unavailable(self):
        """Server could not be reached — tell the user and exit cleanly."""
        try:
            self._set_splash("No server found. Close and launch the Dicktator Server.")
            from tkinter import messagebox
            messagebox.showerror(
                "No Server",
                "Could not connect to the Dicktator Server.\n\n"
                "Launch the server, enable a voice model (click Activate),\n"
                "then open Dicktator again.")
        except tk.TclError:
            pass
        self._close_and_exit()

    def _close_and_exit(self):
        self._running = False
        if hasattr(self, '_splash') and self._splash:
            try:
                self._splash.destroy()
            except tk.TclError:
                pass
            self._splash = None
        if hasattr(self, '_server_sock') and self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _build_ui(self):
        self._ui_built = True
        self._stop_splash_blink()
        if self._splash:
            try:
                self._splash.destroy()
            except tk.TclError:
                pass
            self._splash = None
        self.style = ttk.Style()
        try:
            self.style.theme_use("vista")
        except tk.TclError:
            pass
        self.root.resizable(True, True)
        self.root.minsize(700, 550)
        self._setup_ui()
        self._center_window(self.root, 700, 560)
        self.root.deiconify()
        self._populate_devices()
        self._refresh_windows()
        self._start_audio()
        self._query_active_model()
        self._poll()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text=_("device")).pack(side=tk.LEFT)
        self.device_combo = ttk.Combobox(row, width=40, state="readonly")
        self.device_combo.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_change)

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=2)
        self.status_label = ttk.Label(row, text=_("status_idle"), font=("Segoe UI", 10), foreground="gray")
        self.status_label.pack(side=tk.LEFT)
        ttk.Label(row, text=_("model")).pack(side=tk.RIGHT, padx=(0, 4))
        self.model_label = ttk.Label(row, text="", foreground="gray", font=("Segoe UI", 8))
        self.model_label.pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Label(row, text=_("lang")).pack(side=tk.RIGHT, padx=(0, 4))
        self.lang_combo = ttk.Combobox(row, values=("English", "Espa\u00f1ol"), width=9, state="readonly")
        self.lang_combo.current(0)
        self.lang_combo.pack(side=tk.RIGHT)
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language_change)
        ttk.Label(row, text="  "+_("style")).pack(side=tk.RIGHT, padx=(0, 4))
        self.style_combo = ttk.Combobox(row, values=("vista", "clam", "alt", "default", "xpnative"), width=8, state="readonly")
        self.style_combo.current(0)
        self.style_combo.pack(side=tk.RIGHT)
        self.style_combo.bind("<<ComboboxSelected>>", self._on_style_change)

        self._no_model_label = ttk.Label(
            main, text="\u26a0 No model active \u2014 open the Dicktator Server and click Activate on a model",
            foreground="#b00000", font=("Segoe UI", 9, "bold"))
        self._no_model_label.pack(anchor=tk.W, pady=(2, 0))

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=_("target")).pack(side=tk.LEFT)
        self.target_combo = ttk.Combobox(row, width=35, state="readonly")
        self.target_combo.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)
        self.target_combo.bind("<<ComboboxSelected>>", self._on_target_change)
        self._refresh_btn = ttk.Button(row, text="\u21bb", width=3, command=self._refresh_windows)
        self._refresh_btn.pack(side=tk.LEFT, padx=(4, 0))

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(2, 0))
        ttk.Checkbutton(row, text=_("return_to_prev"), variable=self._return_to_prev).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(row, text=_("write_to_file"), variable=self._write_to_file,
                        command=self._on_mode_change).pack(side=tk.LEFT)

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(0, 2))
        self._file_entry = ttk.Entry(row, textvariable=self._file_path, width=40)
        self._file_entry.pack(side=tk.LEFT, padx=(0, 4), fill=tk.X, expand=True)
        self._browse_btn = ttk.Button(row, text=_("browse"), command=self._browse_file)
        self._browse_btn.pack(side=tk.LEFT)

        self._file_opts_row = ttk.Frame(main)
        self._file_opts_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(self._file_opts_row, text=_("append")).pack(side=tk.LEFT, padx=(0, 4))
        self._file_newline_combo = ttk.Combobox(self._file_opts_row,
            values=(_("new_line"), _("same_line")), width=12, state="readonly")
        self._file_newline_combo.current(0)
        self._file_newline_combo.pack(side=tk.LEFT, padx=(0, 12))
        self._file_newline_combo.bind("<<ComboboxSelected>>", lambda e: self._file_newline.set(
            "newline" if self._file_newline_combo.get() == LOCALE["en"]["new_line"]
                      else "inline"))
        ttk.Checkbutton(self._file_opts_row, text=_("include_timestamp"),
                        variable=self._file_timestamp).pack(side=tk.LEFT)

        body = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=4)

        left = ttk.Frame(body)
        right = ttk.Frame(body, width=220)
        body.add(left, weight=1)
        body.add(right, weight=0)

        # Live Stream panel (shows model output in real-time)
        ttk.Label(left, text="Live Stream", font=("Segoe UI", 10)).pack(anchor=tk.W, pady=(0, 2))
        stream_frame = ttk.Frame(left)
        stream_frame.pack(fill=tk.X, pady=(0, 4))
        self.stream_text = tk.Text(
            stream_frame, height=4, wrap=tk.WORD, state=tk.DISABLED,
            font=("Segoe UI", 9), relief=tk.SUNKEN, borderwidth=1,
            foreground="#555")
        self.stream_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        stream_scroll = ttk.Scrollbar(stream_frame, orient=tk.VERTICAL, command=self.stream_text.yview)
        self.stream_text.configure(yscrollcommand=stream_scroll.set)
        stream_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.stream_engine_label = ttk.Label(left, text="", foreground="gray",
                                             font=("Segoe UI", 8))
        self.stream_engine_label.pack(anchor=tk.W)

        # Buffer panel
        ttk.Label(left, text=_("buffer"), font=("Segoe UI", 10)).pack(anchor=tk.W, pady=(4, 2))
        buf_frame = ttk.Frame(left)
        buf_frame.pack(fill=tk.BOTH, expand=True)
        self.buffer_text = tk.Text(
            buf_frame, height=6, wrap=tk.WORD, state=tk.NORMAL,
            font=("Segoe UI", 10), relief=tk.SUNKEN, borderwidth=1,
        )
        self.buffer_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        buf_scroll = ttk.Scrollbar(buf_frame, orient=tk.VERTICAL, command=self.buffer_text.yview)
        self.buffer_text.configure(yscrollcommand=buf_scroll.set)
        buf_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.rms_bar = ttk.Progressbar(left, mode="determinate", maximum=100, length=100)
        self.rms_bar.pack(fill=tk.X, pady=(2, 0))

        ttk.Label(right, text=_("history"), font=("Segoe UI", 10)).pack(anchor=tk.W)
        self.history_canvas = tk.Canvas(right, highlightthickness=0, bg="#f0f0f0")
        self.history_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.history_canvas.yview)
        self.history_canvas.configure(yscrollcommand=self.history_scroll.set)
        self.history_inner = ttk.Frame(self.history_canvas)
        self.history_inner.bind("<Configure>",
            lambda e: self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all")))
        self.history_canvas.create_window((0, 0), window=self.history_inner, anchor="n", width=208)
        self.history_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.history_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        def _on_mousewheel(event):
            self.history_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.history_canvas.bind("<Enter>", lambda e: self.history_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self.history_canvas.bind("<Leave>", lambda e: self.history_canvas.unbind_all("<MouseWheel>"))

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=2)
        self.start_btn = ttk.Button(row, text=_("start"), command=self._cmd_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.stop_btn = ttk.Button(row, text=_("stop"), command=self._cmd_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=4)
        self.send_btn = ttk.Button(row, text=_("send"), command=self._cmd_send, state=tk.DISABLED)
        self.send_btn.pack(side=tk.LEFT, padx=4)
        self.clear_btn = ttk.Button(row, text=_("clear"), command=self._cmd_clear)
        self.clear_btn.pack(side=tk.LEFT, padx=4)
        self.clrhist_btn = ttk.Button(row, text=_("clear_history"), command=self._cmd_clear_history)
        self.clrhist_btn.pack(side=tk.LEFT, padx=4)

        self._commands_hint_label = ttk.Label(main, text="",
                                               foreground="gray", font=("Segoe UI", 9), wraplength=650)
        self._commands_hint_label.pack(anchor=tk.W)

        self._on_mode_change()
        self._commands_hint_label.config(text=self._build_commands_hint())

        # Dark overlay shown when no model is active
        self._model_overlay = tk.Frame(self.root, bg="#1e1e1e")
        self._overlay_label = ttk.Label(
            self._model_overlay,
            text="\u26a0  No voice model active\n\n"
                 "Open the Dicktator Server and click ACTIVATE\n"
                 "on a model before using Dicktator.\n\n"
                 "Having the server open is not enough \u2014\na model must be loaded.",
            foreground="#ffd76a", font=("Segoe UI", 13, "bold"),
            background="#1e1e1e", justify=tk.CENTER)
        self._overlay_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        self._model_overlay.place_forget()

    def _populate_devices(self):
        try:
            devices = sd.query_devices()
            self._input_devices = []
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    name = d["name"].lower()
                    self._input_devices.append((i, d["name"]))
                    if self._device_id is None and (
                        "havit" in name or "gk56" in name
                    ):
                        self._device_id = i
            if self._device_id is None:
                for did, dname in self._input_devices:
                    dn = dname.lower()
                    if ("microphone" in dn or "mic" in dn or "havit" in dn or "gk56" in dn) \
                       and "sound mapper" not in dn and "primary" not in dn:
                        self._device_id = did
                        break
            if self._device_id is None and self._input_devices:
                self._device_id = self._input_devices[0][0]

            self.device_combo["values"] = [n for _, n in self._input_devices]
            for idx, (did, _) in enumerate(self._input_devices):
                if did == self._device_id:
                    self.device_combo.current(idx)
                    break
        except Exception as e:
            messagebox.showerror("Error", f"Failed to query audio devices:\n{e}")

    def _on_device_change(self, event):
        idx = self.device_combo.current()
        if idx < 0 or idx >= len(self._input_devices):
            return
        new_id = self._input_devices[idx][0]
        if new_id == self._device_id:
            return
        self._device_id = new_id
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self._drain_queue()
        self._partial_text = ""
        self._dirty = True
        self._start_audio()
        print(f"Switched to: {sd.query_devices(new_id)['name']}")

    def _on_style_change(self, event):
        theme = self.style_combo.get()
        try:
            self.style.theme_use(theme)
        except tk.TclError:
            pass

    def _on_mode_change(self):
        file_mode = self._write_to_file.get()
        state_f = "disabled" if file_mode else "readonly"
        state_e = "disabled" if file_mode else "normal"
        self.target_combo.configure(state=state_f)
        self._refresh_btn.configure(state=state_e)
        state_f2 = "disabled" if not file_mode else "readonly"
        state_e2 = "disabled" if not file_mode else "normal"
        self._file_entry.configure(state=state_f2)
        self._browse_btn.configure(state=state_e2)
        for c in self._file_opts_row.winfo_children():
            c.configure(state=state_e2)

    def _on_language_change(self, event):
        lang = "es" if self.lang_combo.get() == "Espa\u00f1ol" else "en"
        _set_lang(lang)
        self._refresh_ui_text()

    def _build_commands_hint(self):
        """Build a dynamic command hint from the active command map."""
        grouped = {"start": [], "stop": [], "send": [], "period": []}
        for phrase, action in _curr_map:
            if action in grouped:
                if phrase not in grouped[action]:
                    grouped[action].append(phrase)
        parts = []
        for a, l in [("start", _("start")), ("stop", _("stop")), ("send", _("send"))]:
            if grouped[a]:
                parts.append(f"{l}: \u201c{'/'.join(grouped[a][:3])}\u201d")
        if grouped["period"]:
            parts.append(f"\u201c{'/'.join(grouped['period'][:3])}\u201d")
        return "  |  ".join(parts)

    def _refresh_ui_text(self):
        self.root.title(_("dictation_title"))
        self.status_label.config(text=_("status_idle") if not self.capturing else _("status_recording"))
        self.start_btn.config(text=_("start"))
        self.stop_btn.config(text=_("stop"))
        self.send_btn.config(text=_("send"))
        self.clear_btn.config(text=_("clear"))
        self.clrhist_btn.config(text=_("clear_history"))
        self._file_newline_combo.configure(values=(_("new_line"), _("same_line")))
        self._file_newline_combo.current(0 if self._file_newline.get() == "newline" else 1)
        self._commands_hint_label.config(text=self._build_commands_hint())

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def _start_audio(self):
        if self._device_id is None:
            messagebox.showerror(_("dictation_title"), _("no_devices"))
            self._quit()
            return

        name = sd.query_devices(self._device_id)["name"]
        print(f"Using: {name}")

        def callback(indata, frames, time_info, status):
            if status:
                print(status, file=sys.stderr)
            self._last_rms = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2))) * 2000
            self._audio_queue.put(indata.copy())

        self.stream = sd.InputStream(
            device=self._device_id,
            samplerate=16000,
            blocksize=8000,
            dtype="int16",
            channels=1,
            callback=callback,
        )
        self.stream.start()

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _drain_queue(self):
        try:
            while True:
                self._audio_queue.get_nowait()
        except queue.Empty:
            pass

    def _connect_server(self):
        self.root.after(0, lambda m=_("server_connecting"): self._set_splash(m))

        # Quick retries — server might have just started
        for _retry in range(5):
            try:
                s = socket.socket()
                s.settimeout(0.5)
                s.connect(("127.0.0.1", SERVER_PORT))
                s.settimeout(None)  # blocking mode for reader
                self._server_sock = s
                threading.Thread(target=self._response_reader, daemon=True).start()
                self.root.after(200, self._splash_model_loop)
                self._query_active_model()
                return
            except (ConnectionRefusedError, OSError):
                try: s.close()
                except: pass
                time.sleep(0.2)

        # Check if a server is already on this port before spawning
        def _port_open():
            try:
                t = socket.socket()
                t.settimeout(0.3)
                t.connect(("127.0.0.1", SERVER_PORT))
                t.close()
                return True
            except: return False

        if not _port_open():
            self.root.after(0, lambda m=_("server_starting_app"): self._set_splash(m))
            subprocess.Popen(
                [sys.executable, str(SCRIPT_DIR / "dicktator_server.py")],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        # Wait up to ~10s for the server, then give up gracefully.
        for attempt in range(10):
            time.sleep(1)
            try:
                s2 = socket.socket()
                s2.settimeout(1)
                s2.connect(("127.0.0.1", SERVER_PORT))
                s2.settimeout(None)  # blocking mode for reader
                self._server_sock = s2
                threading.Thread(target=self._response_reader, daemon=True).start()
                self.root.after(200, self._splash_model_loop)
                self._query_active_model()
                return
            except (ConnectionRefusedError, OSError):
                try: s2.close()
                except: pass
        self.root.after(0, self._server_unavailable)

    def _response_reader(self):
        sock = self._server_sock
        if sock is None:
            return
        try:
            f = sock.makefile("r", encoding="utf-8")
            for line in f:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = msg.get("t")
                if t == "final":
                    text = msg.get("d", "").strip()
                    print(f"[Response] final: {text}", flush=True)
                    self._stream_add_final(text)
                    self._show_partial("")
                    if text:
                        self._process(text)
                elif t == "partial":
                    ptext = msg.get("d", "").strip()
                    print(f"[Response] partial: {ptext}", flush=True)
                    self._stream_update_partial(ptext)
                    if ptext and ptext != self._partial_text:
                        self._partial_text = ptext
                        self._dirty = True
                elif t == "active_model":
                    info = msg.get("d", {})
                    if isinstance(info, dict):
                        eng = info.get("engine", "")
                        models = info.get("models", [])
                        label = f"{eng}: {', '.join(models)}"
                    else:
                        label = str(info)
                        models = []
                    has_model = bool(models)
                    self.root.after(0, lambda: self._update_model_labels(label))
                    self.root.after(0, lambda: self._set_no_model(not has_model))
                    if has_model:
                        if not getattr(self, '_ui_built', False):
                            self._schedule_build_ui()
                    else:
                        self._splash_ask_model()
                elif t == "no_model":
                    # Server has no model active — ask the user to enable one.
                    print("[Response] no_model - asking user to enable a model", flush=True)
                    self.root.after(0, lambda: self._set_no_model(True))
                    self._splash_ask_model()
                    if has_model:
                        if not getattr(self, '_ui_built', False):
                            self._schedule_build_ui()
                    elif not getattr(self, '_ui_built', False):
                        # Splash must explicitly ask for a model until one is active
                        self.root.after(0, lambda: self._splash_ask_model())
                        self.root.after(2500, self._query_active_model)
        except (OSError, ValueError) as e:
            print(f"[Response] reader error: {e}", flush=True)
            self.root.after(0, self._handle_disconnect)

    def _audio_worker(self):
        while self._running:
            try:
                indata = self._audio_queue.get(timeout=0.5)
                sock = self._server_sock
                if sock is None:
                    continue
                try:
                    payload = json.dumps({"t": "audio", "d": base64.b64encode(indata.tobytes()).decode()})
                    sock.sendall(payload.encode() + b"\n")
                except OSError:
                    self._server_sock = None
                    print("[Audio] send error, socket reset", flush=True)
                    self.root.after(0, self._handle_disconnect)
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Sound cues
    # ------------------------------------------------------------------

    @staticmethod
    def _beep(freq, dur):
        winsound.Beep(freq, dur)

    def _sound_start(self):
        threading.Thread(target=lambda: (
            self._beep(660, 100), self._beep(880, 120)
        ), daemon=True).start()

    def _sound_stop(self):
        threading.Thread(target=lambda: (
            self._beep(880, 100), self._beep(660, 120)
        ), daemon=True).start()

    def _sound_send(self):
        threading.Thread(target=lambda: (
            self._beep(660, 80), self._beep(880, 80)
        ), daemon=True).start()

    def _sound_tick(self):
        threading.Thread(target=lambda: self._beep(500, 60), daemon=True).start()

    # ------------------------------------------------------------------
    # Voice command processing (runs in worker thread)
    # ------------------------------------------------------------------

    def _execute_command(self, action):
        if action == "start" and not self.capturing:
            self.capturing = True
            self._dirty = True
            self._sound_start()
            print("[Start]")
        elif action == "stop" and self.capturing:
            self.capturing = False
            self._dirty = True
            self._sound_stop()
            print("[Stop]")
        elif action == "send":
            if time.time() - self._last_send_time < 1.0:
                return
            self._last_send_time = time.time()
            self._pending_send = True
            self._dirty = True
        elif action == "period":
            self.root.after(0, self._insert_period)

    def _process(self, text):
        raw = text.strip()
        stripped = raw.lower()
        # Strip trailing punctuation for command matching (Whisper adds periods)
        clean = stripped.rstrip(".,!?;:")
        for candidate in (clean, stripped):
            for phrase, action in _curr_map:
                if candidate == phrase:
                    print(f"[Process] command match: '{candidate}' -> {action}", flush=True)
                    self._execute_command(action)
                    return

        if self.capturing:
            print(f"[Process] buffer text: '{raw}'", flush=True)
            self._add_to_buffer(raw)
        else:
            print(f"[Process] not capturing, ignored: '{raw}'", flush=True)

    def _show_partial(self, text):
        self._partial_text = text
        self._dirty = True

    def _stream_update_partial(self, text):
        """Update the live stream with a partial transcription."""
        self.root.after(0, lambda: self._do_stream_update(text))

    def _do_stream_update(self, text):
        if not hasattr(self, 'stream_text'):
            return
        try:
            self.stream_text.configure(state=tk.NORMAL)
            self.stream_text.delete(1.0, tk.END)
            self.stream_text.insert(tk.END, text)
            self.stream_text.configure(state=tk.DISABLED)
            self.stream_text.see(tk.END)
        except tk.TclError:
            pass

    def _stream_add_final(self, text):
        """Append a complete transcription to the live stream."""
        self.root.after(0, lambda: self._do_stream_add(text))

    def _do_stream_add(self, text):
        if not hasattr(self, 'stream_text'):
            return
        try:
            self.stream_text.configure(state=tk.NORMAL)
            if self.stream_text.get(1.0, tk.END).strip():
                self.stream_text.insert(tk.END, "\n")
            self.stream_text.insert(tk.END, text)
            self.stream_text.configure(state=tk.DISABLED)
            self.stream_text.see(tk.END)
        except tk.TclError:
            pass

    def _capitalize_next(self):
        self._do_cap = True

    def _append_to_widget(self, text):
        if self._do_cap and text:
            text = text[0].upper() + text[1:]
            self._do_cap = False
        current = self.buffer_text.get(1.0, "end-1c").strip()
        with self._buffer_lock:
            if self.buffer:
                self.buffer += " " + text
            else:
                self.buffer = text
        if current:
            self.buffer_text.insert(tk.END, f" [...] {text}")
        else:
            self.buffer_text.insert(tk.END, text)
        self.buffer_text.see(tk.END)
        self._sound_tick()

    def _add_to_buffer(self, text):
        self.root.after(0, self._append_to_widget, text)

    def _insert_period(self):
        self.buffer_text.insert(tk.END, ".")
        self.buffer_text.see(tk.END)
        self._do_cap = True
        with self._buffer_lock:
            self.buffer = self.buffer_text.get(1.0, "end-1c").strip()

    # ------------------------------------------------------------------
    # Button commands
    # ------------------------------------------------------------------

    def _cmd_start(self):
        if not self.capturing:
            self._partial_text = ""
            self.capturing = True
            self._dirty = True
            self._sound_start()
            print("[Start]")

    def _cmd_stop(self):
        if self.capturing:
            self._partial_text = ""
            self.capturing = False
            self._dirty = True
            self._sound_stop()
            print("[Stop]")

    def _add_to_history(self, text):
        self._history.insert(0, text)
        if len(self._history) > self._max_history:
            self._history.pop()
        self.root.after(0, self._add_history_bubble, text)

    def _add_history_bubble(self, text):
        frame = tk.Frame(self.history_inner, bg="#dce8ff", bd=1, relief=tk.RIDGE)
        frame.pack(fill=tk.X, pady=2, padx=3)
        label = tk.Label(frame, text=text, bg="#dce8ff", font=("Segoe UI", 9),
                         wraplength=170, justify=tk.LEFT, anchor=tk.W)
        label.pack(padx=5, pady=3, fill=tk.X)
        self.history_inner.update_idletasks()
        self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all"))
        self.history_canvas.yview_moveto(1.0)

    def _log_send(self, text):
        datestr = time.strftime("%Y-%m-%d")
        timestr = time.strftime("%H:%M")
        path = SCRIPT_DIR / f"notes_{datestr}.log"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{timestr}] {text}\n")
        except Exception as e:
            print(f"[Log error] {e}")

    def _do_send(self, text):
        if self._write_to_file.get():
            self._write_to_file_func(text)
        elif self._target_hwnd:
            self._send_to_window(text)
        else:
            self._set_clipboard(text)
            keyboard.press_and_release('ctrl+v')
            time.sleep(0.15)
            keyboard.press_and_release("enter")

    def _write_to_file_func(self, text):
        try:
            path = self._file_path.get().strip()
            if not path:
                self.status_label.config(text=_("no_file_path"), foreground="red")
                return
            prefix = ""
            if self._file_timestamp.get():
                prefix = time.strftime("[%H:%M] ")
            if self._file_newline.get() == "newline":
                content = prefix + text + "\n"
            else:
                content = prefix + text
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            print(f"[File] {path}: {content.strip()}")
            self.status_label.config(text=f"{_('written_to_file')} {Path(path).name}", foreground="green")
        except Exception as e:
            self.status_label.config(text=f"{_('file_error')} {e}", foreground="red")

    def _browse_file(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=Path(self._file_path.get()).name,
        )
        if path:
            self._file_path.set(path)

    def _cmd_send(self):
        self._partial_text = ""
        text = self.buffer_text.get(1.0, "end-1c").strip()
        self.buffer_text.delete(1.0, tk.END)
        with self._buffer_lock:
            self.buffer = ""
        if text:
            text = text.replace("\r\n", " ").replace("\n", " ")
            print(f"[Send] {text}")
            self._add_to_history(text)
            self._log_send(text)
            self._sound_send()
            self._do_send(text)
        self._dirty = True

    def _cmd_clear(self):
        self._partial_text = ""
        self.buffer_text.delete(1.0, tk.END)
        with self._buffer_lock:
            self.buffer = ""
        self._dirty = True
        print("[Clear]")

    def _cmd_clear_history(self):
        self._history.clear()
        for w in self.history_inner.winfo_children():
            w.destroy()
        self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all"))
        print("[Clear History]")

    # ------------------------------------------------------------------
    # Target window
    # ------------------------------------------------------------------

    def _refresh_windows(self):
        entries = {}
        def enum_cb(hwnd, _):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.strip()
                    if title and title not in ("Program Manager", "Fullscreen Magnifier"):
                        entries[hwnd] = title
            return True
        WNDPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        ctypes.windll.user32.EnumWindows(WNDPROC(enum_cb), 0)
        self._window_list = entries
        titles = list(entries.values())
        self.target_combo["values"] = titles
        if self._target_hwnd and self._target_hwnd in entries:
            self.target_combo.set(entries[self._target_hwnd])
        elif titles:
            self.target_combo.set("")

    def _on_target_change(self, event):
        title = self.target_combo.get()
        for hwnd, t in self._window_list.items():
            if t == title:
                self._target_hwnd = hwnd
                print(f"[Target] {title}")
                return
        self._target_hwnd = None

    def _get_focused_control(self, parent_hwnd):
        tid = ctypes.windll.user32.GetWindowThreadProcessId(parent_hwnd, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            if info.hwndFocus and ctypes.windll.user32.IsWindow(info.hwndFocus):
                return info.hwndFocus
        return parent_hwnd

    def _find_edit_child(self, parent_hwnd):
        user32 = ctypes.windll.user32
        result = []
        EDIT_CLASSES = ("Edit", "RichEdit20W", "RichEdit50W", "RICHEDIT50W", "RICHEDIT")
        def enum_child(hwnd, _):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value in EDIT_CLASSES:
                result.append(hwnd)
                return False
            return True
        WNDPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user32.EnumChildWindows(parent_hwnd, WNDPROC(enum_child), 0)
        return result[0] if result else None

    def _set_clipboard(self, text):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.argtypes = [w.UINT, w.UINT]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = w.BOOL
        user32.SetClipboardData.argtypes = [w.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        data = text.encode("utf-16-le") + b"\x00\x00"
        if not user32.OpenClipboard(self._target_hwnd):
            return False
        user32.EmptyClipboard()
        h_mem = kernel32.GlobalAlloc(0x0042, len(data))
        if not h_mem:
            user32.CloseClipboard()
            return False
        mem = kernel32.GlobalLock(h_mem)
        if not mem:
            kernel32.GlobalFree(h_mem)
            user32.CloseClipboard()
            return False
        ctypes.memmove(mem, data, len(data))
        kernel32.GlobalUnlock(h_mem)
        user32.SetClipboardData(13, h_mem)
        user32.CloseClipboard()
        return True

    def _send_input_text(self, text):
        keyboard.press_and_release('ctrl+v')
        time.sleep(0.15)
        keyboard.press_and_release('enter')

    def _find_child_console_pids(self, parent_pid):
        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002
        INVALID = ctypes.c_void_p(-1).value

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", w.DWORD),
                ("cntUsage", w.DWORD),
                ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", w.DWORD),
                ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", w.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return []
        children = []
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if kernel32.Process32First(snap, ctypes.byref(pe)):
            while True:
                if pe.th32ParentProcessID == parent_pid:
                    name = pe.szExeFile.decode("utf-8", errors="replace").lower()
                    children.append((pe.th32ProcessID, name))
                if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(snap)
        return children

    def _find_console_pid(self, target_hwnd):
        user32 = ctypes.windll.user32
        target_title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(target_hwnd, target_title, 512)
        target_title = target_title.value.strip()
        if not target_title:
            return 0

        # Strategy 1: find console/terminal windows with matching title
        CONSOLE_CLASSES = ("ConsoleWindowClass", "CascadiaWindowClass",
                          "WindowsTerminalWindowClass",
                          "CASCADIA_HOSTING_WINDOW_CLASS")
        found = 0
        def enum_cb(hwnd, _):
            nonlocal found
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in CONSOLE_CLASSES:
                title_len = user32.GetWindowTextLengthW(hwnd)
                if title_len > 0:
                    tb = ctypes.create_unicode_buffer(title_len + 1)
                    user32.GetWindowTextW(hwnd, tb, title_len + 1)
                    if target_title in tb.value:
                        pid = w.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        found = pid.value
                        return False
            return True
        WNDPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        user32.EnumWindows(WNDPROC(enum_cb), 0)
        if found:
            return found

        # Strategy 2: target PID's own PID (works if target IS a console process)
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(pid))
        return pid.value

    def _run_console_helper(self, pid, text):
        helper = SCRIPT_DIR / "send_to_console.py"
        try:
            proc = subprocess.Popen(
                [sys.executable, str(helper), str(pid)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout, stderr = proc.communicate(text.encode("utf-8"), timeout=5)
            if proc.returncode != 0:
                err = stderr.decode().strip()
                print(f"[Console helper PID {pid}] exit {proc.returncode} {err}")
            return proc.returncode == 0
        except Exception as e:
            print(f"[Console helper PID {pid}] exception: {e}")
            return False

    def _try_console_helper(self, text):
        user32 = ctypes.windll.user32
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(self._target_hwnd, ctypes.byref(pid))
        tried = set()

        # Strategy 1: try the target window's PID directly
        if pid.value:
            tried.add(pid.value)
            if self._run_console_helper(pid.value, text):
                return True

        # Strategy 2: try console window PID matching
        console_pid = self._find_console_pid(self._target_hwnd)
        if console_pid and console_pid not in tried:
            tried.add(console_pid)
            if self._run_console_helper(console_pid, text):
                return True

        # Strategy 3: try child processes of the target PID (covers Windows Terminal)
        target_pid = pid.value or console_pid
        if target_pid:
            for child_pid, child_name in self._find_child_console_pids(target_pid):
                if child_pid not in tried:
                    tried.add(child_pid)
                    if self._run_console_helper(child_pid, text):
                        return True

        return False

    def _send_to_window(self, text):
        if not self._target_hwnd:
            return
        if self._try_console_helper(text):
            print("[Send] via console helper")
            self.status_label.config(text=_("sent_console"), foreground="green")
            return
        edit_hwnd = self._find_edit_child(self._target_hwnd)
        if edit_hwnd:
            print(f"[Send] via edit child 0x{edit_hwnd:x}")
            self._set_clipboard(text)
            user32 = ctypes.windll.user32
            user32.SendMessageW(edit_hwnd, 0x0302, 0, 0)
            user32.SendMessageW(edit_hwnd, 0x0102, 13, 0)
            self.status_label.config(text=_("sent_edit"), foreground="green")
            return
        self._set_clipboard(text)
        user32 = ctypes.windll.user32
        hwnd = self._target_hwnd
        our_hwnd = self.root.winfo_id()
        prev = user32.GetForegroundWindow()

        # Minimize the calling app to break its foreground lock.
        if prev and prev != hwnd and prev != our_hwnd:
            user32.ShowWindow(prev, 6)
            time.sleep(0.15)
            # Break any remaining foreground lock via thread attachment
            prev_tid = user32.GetWindowThreadProcessId(prev, None)
            our_tid = user32.GetWindowThreadProcessId(our_hwnd, None)
            if prev_tid and prev_tid != our_tid:
                user32.AttachThreadInput(prev_tid, our_tid, True)
                user32.AttachThreadInput(prev_tid, our_tid, False)

        # SwitchToThisWindow (simulates Alt+Tab) to activate each window.
        # Flash our own window first to claim foreground ownership, then
        # the target.  This is gentler than SetForegroundWindow + SetFocus.
        user32.SwitchToThisWindow(our_hwnd, True)
        time.sleep(0.05)
        user32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.6)

        # Type on a background thread so the UI doesn't freeze.
        # If return-to-prev is on, restore the previous window after a delay.
        def _type_and_report():
            self._send_input_text(text)
            time.sleep(0.2)
            if self._return_to_prev.get() and prev and prev != hwnd and prev != our_hwnd:
                try:
                    user32.ShowWindow(prev, 9)
                    time.sleep(0.05)
                    user32.SwitchToThisWindow(prev, True)
                except Exception:
                    pass
            self.root.after(0, lambda: self.status_label.config(
                text=_("sent_focus"), foreground="green"))

        threading.Thread(target=_type_and_report, daemon=True).start()

    def _set_no_model(self, no_model):
        if hasattr(self, '_no_model_label'):
            if no_model:
                self._no_model_label.pack(anchor=tk.W, pady=(2, 0))
            else:
                self._no_model_label.pack_forget()
        if hasattr(self, '_model_overlay'):
            if no_model:
                self._model_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
                self._model_overlay.lift()
            else:
                self._model_overlay.place_forget()
        if not no_model:
            self._stop_splash_blink()
        elif not getattr(self, '_ui_built', False):
            # Splash still visible — show the explicit blinking request.
            self._splash_ask_model()

    def _query_active_model(self):
        if not self._server_sock:
            return
        try:
            payload = json.dumps({"t": "get_active_model"})
            self._server_sock.sendall(payload.encode() + b"\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Main-thread polling (runs ~10x/sec)
    # ------------------------------------------------------------------

    def _poll(self):
        if not self._running:
            return

        if self._pending_send:
            self._pending_send = False
            text = self.buffer_text.get(1.0, "end-1c").strip()
            self.buffer_text.delete(1.0, tk.END)
            with self._buffer_lock:
                self.buffer = ""
            if text:
                text = text.replace("\r\n", " ").replace("\n", " ")
                print(f"[Send voice] {text}")
                self._add_to_history(text)
                self._log_send(text)
                self._sound_send()
                self._do_send(text)
            self._dirty = True

        if self._dirty:
            self._dirty = False
            self._sync_gui()

        rms = min(self._last_rms, 100)
        self.rms_bar["value"] = rms if self.capturing else 0

        # Re-query model state every ~3s so the overlay updates when a model
        # is activated on the server while the app is running.
        self._poll_count = getattr(self, '_poll_count', 0) + 1
        if self._poll_count % 30 == 0:
            self._query_active_model()

        self.root.after(100, self._poll)

    def _sync_gui(self):
        if self.capturing:
            self.status_label.config(text=_("status_recording"), foreground="#cc3333")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
        else:
            self.status_label.config(text=_("status_idle"), foreground="gray")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

        with self._buffer_lock:
            buf = self.buffer

        if self._partial_text and not hasattr(self, '_stream_updated'):
            clean = " ".join(w for w in self._partial_text.split() if w.lower() != "the")
            self._stream_update_partial(clean)
        self.send_btn.config(state=tk.NORMAL if buf else tk.DISABLED)

    # ------------------------------------------------------------------

    def _quit(self):
        self._running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        self.root.destroy()


if __name__ == "__main__":
    # Single-instance enforcement so only one Dicktator client runs.
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        hmutex = kernel32.CreateMutexW(None, True, "DicktatorClientSingleton")
        if hmutex and kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            res = kernel32.WaitForSingleObject(hmutex, 0)
            if res == 0x00000102:  # WAIT_TIMEOUT — a live instance holds it
                print("Dicktator already running", flush=True)
                sys.exit(0)
            # WAIT_ABANDONED — previous owner died; we now own it
        elif not hmutex:
            print("Failed to acquire single-instance mutex", flush=True)
            sys.exit(1)
    except Exception as e:
        print(f"Mutex check skipped: {e}", flush=True)
    DictationApp()
