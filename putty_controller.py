import os
import sys
import time
import json
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

VK_SHIFT = 0x10
VK_INSERT = 0x2D
VK_RETURN = 0x0D
VK_DOWN = 0x28
VK_MENU = 0x12  # Alt key

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

        # Alt key trick to bypass Windows SetForegroundWindow restrictions
        win32api.keybd_event(VK_MENU, 0, 0, 0)
        win32api.keybd_event(VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return True
    except Exception as e:
        print(f"[WINDOW] Error activating HWND {hwnd}: {e}")
        return False

def flash_window(hwnd: int):
    """Flash window caption bar to visually draw user attention."""
    try:
        win32gui.FlashWindow(hwnd, True)
    except Exception:
        pass

def send_enter(hwnd: int):
    """Focus window and send Enter key."""
    activate_window(hwnd)
    time.sleep(0.1)
    win32api.keybd_event(VK_RETURN, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)

def send_down_arrow(hwnd: int):
    """Focus window and send Down Arrow key."""
    activate_window(hwnd)
    time.sleep(0.1)
    win32api.keybd_event(VK_DOWN, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(VK_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)

def paste_text(hwnd: int, text: str, press_enter: bool = True):
    """
    Copy text to clipboard, focus PuTTY window, and send Shift + Insert (PuTTY paste).
    Optionally presses Enter after pasting.
    """
    set_clipboard(text)
    activate_window(hwnd)
    time.sleep(0.2)
    
    # Send Shift + Insert
    win32api.keybd_event(VK_SHIFT, 0, 0, 0)
    win32api.keybd_event(VK_INSERT, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(VK_INSERT, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.2)
    
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
        
        # Pre-suggestion based on Claude title ("✳ empty" or contains "claude")
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
    git_h, claude_h = select_putty_windows(force_prompt=True)
    print(f"\nDone! Git HWND: {git_h}, Claude HWND: {claude_h}")
