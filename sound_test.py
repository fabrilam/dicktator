import winsound
import time

sounds = [
    ("1: Soft thud",        200, 15),
    ("2: Wood knock",       400, 20),
    ("3: Deep thump",       150, 30),
    ("4: Gentle bump",      300, 25),
    ("5: Soft pop",         800, 8),
    ("6: Quick blip",       600, 50),
    ("7: Water drop",      1200, 10),
    ("8: Light tick",       900, 10),
    ("9: Tiny ding",       1500, 30),
    ("10: Mellow tone",     500, 60),
]

print("Sound test — 10 buffer tick options")
print("Press ENTER to play each, Ctrl+C to quit\n")

for label, freq, dur in sounds:
    input(f"  {label} ({freq}Hz, {dur}ms) → ")
    winsound.Beep(freq, dur)
    time.sleep(0.2)

print("\nDone. Pick your favorite and I'll set it.")
