import os
import sys
import json
import threading
import urllib.request
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import numpy as np
import sounddevice as sd
from vosk import Model, KaldiRecognizer


MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_DIR = "vosk-model-small-en-us-0.15"


class MicTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Mic Test")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.stream = None
        self.model = None
        self.recognizer = None
        self.running = False
        self.closing = False
        self.current_level = 0.0
        self.partial_text = ""
        self.final_text = ""
        self.input_devices = []

        self.setup_ui()
        self.populate_devices()
        self.root.after(100, self.check_model)

    def setup_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(row, text="Microphone:", width=10).pack(side=tk.LEFT)
        self.device_combo = ttk.Combobox(row, width=55, state="readonly")
        self.device_combo.pack(side=tk.LEFT, padx=(5, 0))

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=5)
        ttk.Label(row, text="Level:", width=10).pack(side=tk.LEFT)
        self.level_bar = ttk.Progressbar(row, length=400, maximum=100)
        self.level_bar.pack(side=tk.LEFT, padx=(5, 0))

        self.start_btn = ttk.Button(main, text="\u25b6 Start", command=self.toggle)
        self.start_btn.pack(pady=5)

        self.text_frame = ttk.Frame(main, borderwidth=1, relief=tk.SUNKEN)
        self.text_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self.text_area = tk.Text(
            self.text_frame, height=8, width=70, wrap=tk.WORD,
            state=tk.DISABLED, font=("Segoe UI", 10)
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)

        self.partial_label = ttk.Label(main, text="", foreground="gray")
        self.partial_label.pack(fill=tk.X, pady=(2, 0))

    def populate_devices(self):
        try:
            devices = sd.query_devices()
            self.input_devices = []
            for i, d in enumerate(devices):
                if d["max_input_channels"] > 0:
                    self.input_devices.append((i, d["name"]))
            self.device_combo["values"] = [name for _, name in self.input_devices]
            if self.input_devices:
                self.device_combo.current(0)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to query audio devices:\n{e}")

    def check_model(self):
        model_path = Path(os.path.dirname(os.path.abspath(__file__))) / MODEL_DIR
        if not model_path.is_dir():
            zip_path = model_path.parent / f"{MODEL_DIR}.zip"
            if not zip_path.is_file():
                ok = messagebox.askyesno(
                    "Download Model",
                    "Vosk speech recognition model not found.\n\n"
                    f"Download ~40MB from:\n{MODEL_URL}",
                )
                if not ok:
                    return
                self.download_model(zip_path)
            self.extract_model(zip_path)

        try:
            self.model = Model(str(model_path))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load Vosk model:\n{e}")

    def download_model(self, zip_path):
        dl = tk.Toplevel(self.root)
        dl.title("Downloading")
        dl.resizable(False, False)
        dl.transient(self.root)
        dl.grab_set()
        ttk.Label(dl, text="Downloading Vosk model (~40 MB)...").pack(padx=20, pady=10)
        bar = ttk.Progressbar(dl, length=300, mode="indeterminate")
        bar.pack(padx=20, pady=5)
        bar.start()
        dl.update()

        try:
            urllib.request.urlretrieve(MODEL_URL, str(zip_path))
        except Exception as e:
            dl.destroy()
            messagebox.showerror("Download Failed", str(e))
            raise
        dl.destroy()

    def extract_model(self, zip_path):
        ex = tk.Toplevel(self.root)
        ex.title("Extracting")
        ex.resizable(False, False)
        ex.transient(self.root)
        ex.grab_set()
        ttk.Label(ex, text="Extracting model...").pack(padx=20, pady=10)
        bar = ttk.Progressbar(ex, length=300, mode="indeterminate")
        bar.pack(padx=20, pady=5)
        bar.start()
        ex.update()

        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                zf.extractall(str(zip_path.parent))
            zip_path.unlink()
        except Exception as e:
            ex.destroy()
            messagebox.showerror("Extraction Failed", str(e))
            raise
        ex.destroy()

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.model is None:
            messagebox.showwarning("No Model", "Vosk model not loaded yet.")
            return

        idx = self.device_combo.current()
        if idx < 0 or idx >= len(self.input_devices):
            messagebox.showwarning("No Device", "Select a microphone first.")
            return
        device_id, device_name = self.input_devices[idx]

        try:
            info = sd.query_devices(device_id)
            if info["max_input_channels"] == 0:
                messagebox.showerror("Error", "Selected device is not an input device.")
                return
        except Exception as e:
            messagebox.showerror("Error", f"Device error:\n{e}")
            return

        self.running = True
        self.start_btn.config(text="\u25a0 Stop")
        self.device_combo.config(state=tk.DISABLED)

        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete(1.0, tk.END)
        self.text_area.config(state=tk.DISABLED)
        self.partial_label.config(text="")
        self.final_text = ""
        self.partial_text = ""

        self.recognizer = KaldiRecognizer(self.model, 16000)
        self.current_level = 0.0

        def audio_callback(indata, frames, time_info, status):
            if status:
                print(status, file=sys.stderr)

            rms = np.sqrt(np.mean(indata.astype(np.float64) ** 2))
            self.current_level = min(100.0, rms * 100)

            if self.recognizer.AcceptWaveform(indata.tobytes()):
                result = json.loads(self.recognizer.Result())
                text = result.get("text", "").strip()
                if text:
                    self.final_text += text + " "
                    self.root.after(0, self.append_final, text + " ")
            else:
                partial = json.loads(self.recognizer.PartialResult())
                t = partial.get("partial", "").strip()
                if t and t != self.partial_text:
                    self.partial_text = t
                    self.root.after(0, self.update_partial, t)

        try:
            self.stream = sd.InputStream(
                device=device_id,
                samplerate=16000,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=audio_callback,
            )
            self.stream.start()
            self.poll_level()
        except Exception as e:
            self.running = False
            self.start_btn.config(text="\u25b6 Start")
            self.device_combo.config(state="readonly")
            messagebox.showerror("Error", f"Failed to start audio stream:\n{e}")

    def stop(self):
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        if self.recognizer:
            result = json.loads(self.recognizer.FinalResult())
            text = result.get("text", "").strip()
            if text:
                self.append_final(text + " ")
        self.recognizer = None

        self.start_btn.config(text="\u25b6 Start")
        self.device_combo.config(state="readonly")
        self.level_bar["value"] = 0
        self.partial_label.config(text="")

    def poll_level(self):
        if self.running and not self.closing:
            self.level_bar["value"] = self.current_level
            self.root.after(100, self.poll_level)

    def append_final(self, text):
        if self.closing:
            return
        self.text_area.config(state=tk.NORMAL)
        self.text_area.insert(tk.END, text)
        self.text_area.see(tk.END)
        self.text_area.config(state=tk.DISABLED)

    def update_partial(self, text):
        if self.closing:
            return
        self.partial_label.config(text=f"[ {text} ... ]")

    def on_close(self):
        self.closing = True
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = MicTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
