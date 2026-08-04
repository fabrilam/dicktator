"""Standalone target sender — test sending text to background windows."""

import ctypes
import ctypes.wintypes as w
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from PIL import Image, ImageTk
from tkinter import ttk


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


class TargetSender:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Dicktator")
        self.root.resizable(True, True)
        icon_path = Path(__file__).parent / "icon_pixellated.png"
        if icon_path.is_file():
            self.root.iconphoto(True, ImageTk.PhotoImage(Image.open(icon_path)))
        elif (Path(__file__).parent / "icon.png").is_file():
            self.root.iconphoto(True, ImageTk.PhotoImage(Image.open(str(Path(__file__).parent / "icon.png"))))

        self._target_hwnd = None
        self._window_list = {}

        self._setup_ui()
        self.root.update_idletasks()
        self._center_window(500, 300)
        self._refresh_windows()
        self.root.mainloop()

    def _center_window(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(row, text="Target:").pack(side=tk.LEFT)
        self.target_combo = ttk.Combobox(row, width=40, state="readonly")
        self.target_combo.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)
        self.target_combo.bind("<<ComboboxSelected>>", self._on_target_change)
        ttk.Button(row, text="\u21bb", width=3, command=self._refresh_windows).pack(side=tk.LEFT, padx=(4, 0))

        ttk.Label(main, text="Text to send:").pack(anchor=tk.W, pady=(4, 2))
        self.text_input = tk.Text(main, height=6, wrap=tk.WORD, font=("Segoe UI", 10))
        self.text_input.pack(fill=tk.BOTH, expand=True)

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=6)
        self.send_btn = ttk.Button(row, text="Send to Target", command=self._send)
        self.send_btn.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row, text="Clear", command=self._clear).pack(side=tk.LEFT, padx=4)

        self.status_label = ttk.Label(main, text="", foreground="gray")
        self.status_label.pack(fill=tk.X)

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
        kb = __import__("keyboard")
        kb.press_and_release('ctrl+v')
        time.sleep(0.15)
        kb.press_and_release("enter")

    def _try_console_helper(self, text):
        pid = w.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(self._target_hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        helper = Path(__file__).parent / "send_to_console.py"
        try:
            proc = subprocess.Popen(
                [sys.executable, str(helper), str(pid.value)],
                stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            proc.communicate(text.encode("utf-8"), timeout=5)
            return proc.returncode == 0
        except:
            return False

    def _send_to_window(self, text):
        if self._try_console_helper(text):
            print("[Send] via console helper")
            self.status_label.config(text="Sent via console helper.", foreground="green")
            self._set_clipboard(text)
            return

        edit_hwnd = self._find_edit_child(self._target_hwnd)
        if edit_hwnd:
            print(f"[Send] via edit child 0x{edit_hwnd:x}")
            self._set_clipboard(text)
            user32 = ctypes.windll.user32
            user32.SendMessageW(edit_hwnd, 0x0302, 0, 0)
            user32.SendMessageW(edit_hwnd, 0x0102, 13, 0)
            self.status_label.config(text="Sent via edit child paste.", foreground="green")
            return

        print("[Send] via aggressive focus + SendInput")
        self._set_clipboard(text)
        user32 = ctypes.windll.user32
        prev = user32.GetForegroundWindow()
        hwnd = self._target_hwnd
        current_tid = user32.GetWindowThreadProcessId(prev, None)
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        if current_tid != target_tid:
            user32.AttachThreadInput(current_tid, target_tid, True)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
        if current_tid != target_tid:
            user32.AttachThreadInput(current_tid, target_tid, False)
        time.sleep(0.2)
        self._send_input_text(text)
        if prev and user32.IsWindow(prev) and prev != hwnd:
            user32.SetForegroundWindow(prev)
        self.status_label.config(text="Sent via aggressive focus.", foreground="green")

    def _send(self):
        text = self.text_input.get(1.0, "end-1c").strip()
        if not text:
            self.status_label.config(text="Nothing to send.", foreground="red")
            return
        if not self._target_hwnd:
            self.status_label.config(text="No target selected.", foreground="red")
            return
        if not ctypes.windll.user32.IsWindow(self._target_hwnd):
            self.status_label.config(text="Target window closed.", foreground="red")
            self._target_hwnd = None
            return
        text = text.replace("\r\n", " ").replace("\n", " ")
        try:
            self._send_to_window(text)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.status_label.config(text=f"Error: {e}", foreground="red")
            print(f"[Send error] {e}")

    def _clear(self):
        self.text_input.delete(1.0, tk.END)
        self.status_label.config(text="", foreground="gray")


if __name__ == "__main__":
    TargetSender()
