import os
import sys
import time
import json
import re
import ctypes
from ctypes import wintypes
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import win32gui
import win32con
import win32process
import win32api
import win32clipboard
import win32service

import config

CACHE_FILE = config.BASE_DIR / ".putty_cache.json"

user32 = ctypes.windll.user32

def ensure_desktop():
    """Ensure thread is attached to the interactive Default desktop."""
    try:
        hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
        ctypes.windll.user32.SetThreadDesktop(int(hdesk))
    except Exception:
        pass

ensure_desktop()

# Win32 Input structures
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_ulong)
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [('ki', KEYBDINPUT)]
    _anonymous_ = ('_input',)
    _fields_ = [
        ('type', wintypes.DWORD),
        ('_input', _INPUT)
    ]

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_SHIFT = 0x10
VK_INSERT = 0x2D
VK_RETURN = 0x0D
VK_DOWN = 0x28
VK_MENU = 0x12  # Alt key

SCAN_SHIFT = 0x2A
SCAN_INSERT = 0x52
SCAN_RETURN = 0x1C
SCAN_DOWN = 0x50

def set_clipboard(text: str) -> bool:
    """Set text to Windows clipboard with retry mechanism."""
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            time.sleep(0.1)
    return False

def get_clipboard() -> str:
    """Get text from Windows clipboard."""
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT).decode("utf-8", errors="ignore")
            else:
                data = ""
            win32clipboard.CloseClipboard()
            return data
        except Exception:
            time.sleep(0.1)
    return ""

def find_all_putty_windows():
    """Find all top-level PuTTY windows across Default desktop."""
    windows = []
    
    def enum_cb(hwnd, extra):
        try:
            cls = win32gui.GetClassName(hwnd)
            if cls == "PuTTY":
                t = win32gui.GetWindowText(hwnd)
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                windows.append({"hwnd": hwnd, "pid": pid, "title": t})
        except Exception:
            pass

    # Try Default desktop first
    try:
        hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
        win32gui.EnumDesktopWindows(hdesk, enum_cb, None)
    except Exception:
        # Fallback to current desktop
        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass
            
    return windows

def activate_window(hwnd: int) -> bool:
    """Bring window to foreground using bulletproof Win32 focus techniques."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # Alt key tap to bypass Windows SetForegroundWindow lock
        user32.keybd_event(VK_MENU, 0x38, 0, 0)
        user32.keybd_event(VK_MENU, 0x38, KEYEVENTF_KEYUP, 0)
        
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.25)
        return True
    except Exception as e:
        print(f"[WINDOW] Error activating HWND {hwnd}: {e}")
        return False

def capture_screen_text(hwnd: int) -> str:
    """Capture all visible text from a PuTTY window using native IDM_COPYALL."""
    try:
        ensure_desktop()
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, 0x0170, 0)
        for _ in range(5):
            time.sleep(0.1)
            text = get_clipboard()
            if text:
                return text
        return ""
    except Exception as e:
        return ""

def wait_for_screen_text(hwnd: int, patterns: list, timeout: int = 120, poll_interval: float = 1.0) -> bool:
    """
    Wait until ANY of the specified patterns appear on the PuTTY window screen as output.
    Returns True when found, False on timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        text = capture_screen_text(hwnd)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for pat in patterns:
            # Match exact line or isolated output, ignoring the command input echo line
            if any((l == pat or l == f"'{pat}'" or l == f'"{pat}"') for l in lines if not l.startswith("echo ") and " && echo " not in l and not l.startswith("root@")):
                return True
        time.sleep(poll_interval)
    return False

def wait_for_git_worktree(hwnd: int, worktree_dir: str, timeout: int = 180) -> bool:
    """
    Waits for git worktree checkout (e.g. 43,000 files) to reach 100% and return to shell prompt.
    Does NOT trigger on the pasted command line!
    """
    marker = "==WORKTREE_READY_100=="
    start_time = time.time()
    last_pct = ""
    
    while time.time() - start_time < timeout:
        screen = capture_screen_text(hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        
        # Display checkout progress if visible
        for line in lines:
            m = re.search(r'Updating files:\s+(\d+%)', line)
            if m and m.group(1) != last_pct:
                last_pct = m.group(1)
                print(f"[GIT PUITY] Unpacking files: {last_pct}...", flush=True)
                
        # The marker MUST appear as its own isolated line from echo, NOT the command line
        has_marker_line = any(l == marker or l == f"'{marker}'" for l in lines)
        has_head = any("HEAD is now at" in l or ", done." in l for l in lines)
        
        if has_marker_line and has_head:
            print(f"[GIT PUITY] ✅ All files updated! Worktree checkout 100% complete.")
            return True
            
        time.sleep(1.0)
        
    print("[WARNING] Timed out waiting for worktree checkout!")
    return False

def flash_window(hwnd: int):
    """Flash window caption bar to visually draw user attention."""
    try:
        win32gui.FlashWindow(hwnd, True)
    except Exception:
        pass

def send_enter(hwnd: int):
    """Send Enter key directly to PuTTY window procedure via WM_CHAR 13 (\r)."""
    win32gui.PostMessage(hwnd, win32con.WM_CHAR, 13, 0)
    time.sleep(0.1)

def send_down_arrow(hwnd: int):
    """Send Down Arrow key to PuTTY window procedure."""
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_DOWN, 0x01500001)
    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_DOWN, 0xC1500001)
    # Terminal escape sequence for down arrow (\x1b[B)
    for code in [27, 91, 66]:
        win32gui.PostMessage(hwnd, win32con.WM_CHAR, code, 0)
    time.sleep(0.1)

def paste_text(hwnd: int, text: str, press_enter: bool = True, use_mouse: bool = True):
    """
    Set clipboard, right-click paste into PuTTY, wait for terminal to register, and send Enter.
    """
    clean_text = text.rstrip("\r\n")
    set_clipboard(clean_text)
    time.sleep(0.1)
    
    rect = win32gui.GetClientRect(hwnd)
    cx = max(10, (rect[2] - rect[0]) // 2)
    cy = max(10, (rect[3] - rect[1]) // 2)
    lp = (cy << 16) | (cx & 0xFFFF)
    
    # Native PuTTY right-click paste
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lp)
    time.sleep(0.05)
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONUP, 0, lp)
    
    # Wait for terminal to process clipboard text
    time.sleep(0.35)
    
    if press_enter:
        send_enter(hwnd)

def get_window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""

def is_window_valid(hwnd: int) -> bool:
    try:
        return win32gui.IsWindow(hwnd) and win32gui.GetClassName(hwnd) == "PuTTY"
    except Exception:
        return False

def select_putty_windows(force_prompt: bool = False):
    """
    Interactively ask user to identify Git PuTTY vs Claude PuTTY, with [3] None/Skip option.
    Saves and reuses cache if valid and force_prompt is False.
    """
    # Check cache first
    if not force_prompt and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            git_hwnd = data.get("git_hwnd")
            claude_hwnd = data.get("claude_hwnd")
            if git_hwnd and claude_hwnd and is_window_valid(git_hwnd) and is_window_valid(claude_hwnd):
                print(f"[WINDOW] Reusing memorized PuTTY windows from cache:")
                print(f"  -> Git PuTTY: HWND {git_hwnd} ('{get_window_title(git_hwnd)}')")
                print(f"  -> Claude PuTTY: HWND {claude_hwnd} ('{get_window_title(claude_hwnd)}')")
                return git_hwnd, claude_hwnd
        except Exception:
            pass

    windows = find_all_putty_windows()
    if not windows:
        print("[ERROR] No open PuTTY windows found! Please open your PuTTY sessions first.")
        sys.exit(1)

    print(f"\n=======================================================")
    print(f"  PuTTY Window Identification ({len(windows)} window(s) detected)")
    print(f"=======================================================")
    print("I will activate each window in turn. Please assign its role:\n")

    git_hwnd = None
    claude_hwnd = None

    for idx, w in enumerate(windows, 1):
        hwnd = w["hwnd"]
        pid = w["pid"]
        title = w["title"]
        
        # Bring window to front and flash
        activate_window(hwnd)
        flash_window(hwnd)
        
        print(f"\n--- Window [{idx}/{len(windows)}] ---")
        print(f"HWND: {hwnd} | PID: {pid} | Current Title: '{title}'")
        
        # Pre-suggestion based on Claude title ("✳ Claude Code" or "✳ empty")
        suggestion = "3"
        if "✳" in title or "claude" in title.lower():
            suggestion = "2"
        
        while True:
            choice = input(f"Assign role for this window? [1] Git, [2] Claude, [3] None/Skip (default={suggestion}): ").strip()
            if not choice:
                choice = suggestion
            if choice in ["1", "2", "3"]:
                break
            print("Invalid input! Please enter 1, 2, or 3.")

        if choice == "1":
            git_hwnd = hwnd
            print(f"-> Assigned as GIT window! (HWND {hwnd})")
        elif choice == "2":
            claude_hwnd = hwnd
            print(f"-> Assigned as CLAUDE window! (HWND {hwnd})")
        else:
            print("-> Skipped window.")

    if not git_hwnd or not claude_hwnd:
        print("\n[ERROR] Both Git and Claude PuTTY windows must be selected to proceed!")
        retry = input("Do you want to retry selection? (y/n): ").strip().lower()
        if retry == "y":
            return select_putty_windows(force_prompt=True)
        sys.exit(1)

    # Save cache
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"git_hwnd": git_hwnd, "claude_hwnd": claude_hwnd}, f, indent=2)
    except Exception:
        pass

    print("\n[SUCCESS] Windows assigned and cached successfully!")
    return git_hwnd, claude_hwnd

if __name__ == "__main__":
    git_h, claude_h = select_putty_windows(force_prompt=False)
    print(f"\nDone! Git HWND: {git_h}, Claude HWND: {claude_h}")
