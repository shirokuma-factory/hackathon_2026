"""Keep Windows from sleeping while long GPU jobs run. Stop with Ctrl+C.

Usage: python scripts/keep_awake.py [--hours 8]
Uses SetThreadExecutionState, so it needs no admin rights and changes no power settings.
"""
import argparse
import ctypes
import sys
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

ap = argparse.ArgumentParser()
ap.add_argument("--hours", type=float, default=8.0)
a = ap.parse_args()

if sys.platform != "win32":
    sys.exit("Windows 専用です。")

kernel32 = ctypes.windll.kernel32
deadline = time.time() + a.hours * 3600
print(f"スリープを {a.hours} 時間止めます。Ctrl+C で終了します。")
try:
    while time.time() < deadline:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        time.sleep(60)
except KeyboardInterrupt:
    pass
finally:
    kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    print("スリープの抑止を解除しました。")
