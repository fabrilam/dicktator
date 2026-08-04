"""Standalone helper: writes text to another process's console input.
Usage: python send_to_console.py <PID>
Reads text from stdin, writes it to the target process's console.
Spawning this as a subprocess avoids console-manipulation crashes in the GUI app."""
import ctypes
import ctypes.wintypes as w
import sys

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def main():
    if len(sys.argv) < 2:
        print("send_to_console: missing PID argument", file=sys.stderr)
        return 1

    pid = int(sys.argv[1])
    text = sys.stdin.read()
    if not text:
        print("send_to_console: no input text", file=sys.stderr)
        return 2
    kernel32 = ctypes.windll.kernel32

    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        last_err = kernel32.GetLastError()
        print(f"send_to_console: AttachConsole({pid}) failed with error {last_err}", file=sys.stderr)
        kernel32.AttachConsole(-1)
        return 3

    try:
        h_stdin = kernel32.GetStdHandle(-10)
        if h_stdin in (None, INVALID_HANDLE_VALUE, 0):
            print("send_to_console: GetStdHandle(STD_INPUT_HANDLE) failed", file=sys.stderr)
            return 4

        data = (text + "\r").encode("utf-8")
        written = w.DWORD(0)
        if not kernel32.WriteFile(h_stdin, data, len(data), ctypes.byref(written), None):
            last_err = kernel32.GetLastError()
            print(f"send_to_console: WriteFile failed with error {last_err}", file=sys.stderr)
            return 5
        if written.value != len(data):
            print(f"send_to_console: wrote {written.value}/{len(data)} bytes", file=sys.stderr)
            return 6
        return 0
    finally:
        kernel32.FreeConsole()


if __name__ == "__main__":
    sys.exit(main())
