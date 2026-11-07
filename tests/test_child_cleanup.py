"""親の Python が強制終了されたとき、Job Object に入れた子が一緒に止まることを確かめる（Windows のみ）。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Job Object は Windows のみ")

ROOT = Path(__file__).resolve().parent.parent

_PARENT = (
    "import subprocess, sys, time\n"
    "sys.path.insert(0, 'tools')\n"
    "import capture_screens as cs\n"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "cs.bind_to_job(c)\n"
    "print(c.pid, flush=True)\n"
    "time.sleep(600)\n"
)


def _alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: まだ動いている
    finally:
        k32.CloseHandle(h)


def test_child_dies_when_parent_is_killed():
    parent = subprocess.Popen([sys.executable, "-c", _PARENT], cwd=str(ROOT), stdout=subprocess.PIPE, text=True)
    child_pid = 0
    try:
        child_pid = int(parent.stdout.readline().strip())
        assert _alive(child_pid), "子が起動していません"
        parent.kill()  # TerminateProcess
        parent.wait(10)
        deadline = time.time() + 10
        while time.time() < deadline and _alive(child_pid):
            time.sleep(0.2)
        assert not _alive(child_pid), "親を強制終了しても子が残っています"
    finally:
        if parent.poll() is None:
            parent.kill()
        if child_pid and _alive(child_pid):
            subprocess.run(["taskkill", "/F", "/PID", str(child_pid)], capture_output=True)
