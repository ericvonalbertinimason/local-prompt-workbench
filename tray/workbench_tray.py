"""Prompt Workbench in the system tray, with a global hotkey (2026-10-31).

Select text anywhere and press Ctrl+Alt+P: the selection (or, if nothing is selected, what is
already on the clipboard) goes to the local workbench as "refine this prompt", and the improved
prompt replaces the clipboard, ready to paste. A notification gives the QA result. If the
workbench has questions instead, the page opens so you can answer them there.

Tray menu: Improve clipboard now, Open workbench, Start workbench, Start with Windows, Quit.

The hotkey uses Windows' RegisterHotKey (no keyboard hook); the clipboard and the Ctrl+C
keystroke use the Win32 API through ctypes. Only pystray and Pillow are installed.

  tray\\start_tray.cmd                       start it (no console window)
  python tray\\workbench_tray.py --check "text"   run one improvement and print it (clipboard untouched)
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path

URL = 'http://127.0.0.1:8789'
HEADERS = {'Content-Type': 'application/json', 'Host': '127.0.0.1:8789', 'Origin': URL, 'X-Harness-Client': 'workbench'}
ROOT = Path(__file__).resolve().parents[1]
STARTUP = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup' / 'Prompt Workbench tray.cmd'
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY, VK_P = 0x1, 0x2, 0x4000, 0x0312, 0x50
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x2

user32, kernel32 = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)


# --- clipboard -------------------------------------------------------------------------------
def _open_clipboard():
    for _ in range(20):                          # another app may hold it for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def get_clipboard():
    if not _open_clipboard():
        return ''
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ''
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p) if p else ''
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def set_clipboard(text):
    data = (text + '\0').encode('utf-16-le')
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not _open_clipboard():
        raise RuntimeError('The clipboard is busy; try again')
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, h)       # Windows owns the memory from here
    finally:
        user32.CloseClipboard()


def copy_selection():
    """Send Ctrl+C to the focused app once the hotkey's Ctrl and Alt are released."""
    deadline = time.time() + 1.5
    while time.time() < deadline and any(user32.GetAsyncKeyState(k) & 0x8000 for k in (0x11, 0x12, 0x50)):
        time.sleep(0.02)
    for vk, up in ((0x11, 0), (0x43, 0), (0x43, 2), (0x11, 2)):       # Ctrl down, C down, C up, Ctrl up
        user32.keybd_event(vk, 0, up, 0)
    time.sleep(0.25)


# --- workbench -------------------------------------------------------------------------------
def api(path, body=None, timeout=30):
    req = urllib.request.Request(URL + path, json.dumps(body).encode() if body is not None else None, HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def improve(text, wait=600):
    """Refine `text` on the workbench. Returns the finished run."""
    run = api('/api/runs', {'prompt': text, 'mode': 'iterate-prompt'})
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(2)
        run = api('/api/runs/' + run['id'])
        if run['status'] not in ('queued', 'running'):
            return run
    raise TimeoutError('The workbench is still working on it; the result will appear in its history')


def qa_line(run):
    qa = run.get('qa')
    qa = json.loads(qa) if isinstance(qa, str) and qa else qa
    if not isinstance(qa, dict):
        return ''
    return 'QA passed' if qa.get('passed') else 'QA flagged: %s' % '; '.join(qa.get('issues') or [])[:120]


# --- tray app --------------------------------------------------------------------------------
class Tray:
    def __init__(self):
        import pystray
        from PIL import Image
        self.pystray = pystray
        icon = Image.open(ROOT / 'harness' / 'static' / 'icon-192.png')
        self.busy = threading.Lock()
        menu = pystray.Menu(
            pystray.MenuItem('Improve clipboard now', lambda: self.start(False), default=True),
            pystray.MenuItem('Open workbench', lambda: webbrowser.open(URL)),
            pystray.MenuItem('Start workbench', self.start_stack),
            pystray.MenuItem('Start with Windows', self.toggle_startup, checked=lambda _: STARTUP.exists()),
            pystray.MenuItem('Quit', self.quit))
        self.icon = pystray.Icon('prompt-workbench', icon, 'Prompt Workbench (Ctrl+Alt+P)', menu)

    def notify(self, text, title='Prompt Workbench'):
        try:
            self.icon.notify(text[:250], title)
        except Exception:
            pass

    def start(self, from_selection):
        threading.Thread(target=self.run_once, args=(from_selection,), daemon=True).start()

    def run_once(self, from_selection):
        if not self.busy.acquire(blocking=False):
            self.notify('Still working on the previous prompt.')
            return
        try:
            before = get_clipboard()
            text = before
            if from_selection:
                copy_selection()
                text = get_clipboard() or before          # nothing selected: use the clipboard
            if not text.strip():
                self.notify('Select some text (or copy it) first, then press Ctrl+Alt+P.')
                return
            self.icon.title = 'Prompt Workbench: improving…'
            self.notify('Improving your prompt on the local model…')
            run = improve(text)
            if run['status'] != 'success':
                self.notify('It did not finish: %s' % (run.get('error') or run['status']))
            elif run.get('response_kind') == 'questions':
                self.notify('It has questions first. Opening the workbench so you can answer them.')
                webbrowser.open(URL)
            else:
                set_clipboard(run.get('result') or '')
                self.notify('Ready: paste with Ctrl+V. %s' % qa_line(run))
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            self.notify('The workbench is not answering (%s). Use "Start workbench" in this menu.' % getattr(e, 'reason', e))
        except Exception as e:
            self.notify('Something went wrong: %s' % e)
        finally:
            self.icon.title = 'Prompt Workbench (Ctrl+Alt+P)'
            self.busy.release()

    def start_stack(self):
        subprocess.Popen(['cmd', '/c', 'start', '', str(ROOT / 'start.cmd')], cwd=str(ROOT))
        self.notify('Starting the workbench (a window shows the steps).')

    def toggle_startup(self):
        if STARTUP.exists():
            STARTUP.unlink()
            self.notify('Will no longer start with Windows.')
        else:
            STARTUP.write_text('@start "" "%s"\r\n' % (ROOT / 'tray' / 'start_tray.cmd'), encoding='utf-8')
            self.notify('Will start with Windows.')

    def quit(self):
        self.icon.stop()
        os._exit(0)

    def hotkeys(self):
        """RegisterHotKey must be called on the thread that reads the messages."""
        if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_P):
            self.notify('Ctrl+Alt+P is already used by another app; use the tray menu instead.')
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                self.start(True)

    def run(self):
        threading.Thread(target=self.hotkeys, daemon=True).start()
        self.icon.run(setup=lambda icon: (setattr(icon, 'visible', True),
                                          self.notify('Ready. Select text anywhere and press Ctrl+Alt+P.')))


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--check':
        run = improve(' '.join(sys.argv[2:]))
        print(run['status'], run.get('response_kind'), '|', qa_line(run))
        print(run.get('result') or run.get('error'))
    else:
        Tray().run()
