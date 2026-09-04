import sys
import time
import ctypes
from ctypes import wintypes

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import win32gui
import win32con
import win32clipboard
import config
import putty_controller as putty

user32 = ctypes.windll.user32

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

def paste_via_sendinput(hwnd: int, text: str):
    """Set clipboard and send Shift + Insert using atomic SendInput with EXTENDEDKEY and scan codes."""
    putty.set_clipboard(text)
    putty.activate_window(hwnd)
    time.sleep(0.3)
    
    # 4 events: Shift Down, Insert Down, Insert Up, Shift Up
    inputs = (INPUT * 4)(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0x10, wScan=0x2A, dwFlags=0, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0x2D, wScan=0x52, dwFlags=KEYEVENTF_EXTENDEDKEY, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0x2D, wScan=0x52, dwFlags=KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0x10, wScan=0x2A, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)),
    )
    user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))
    time.sleep(0.2)

def paste_via_mouse_click(hwnd: int, text: str, press_enter: bool = True):
    """
    Set clipboard and send right click to PuTTY client area.
    If press_enter=True, appends newline to clipboard so PuTTY executes it automatically!
    Also sends WM_KEYDOWN VK_RETURN as backup.
    """
    to_paste = text.rstrip("\r\n") + ("\n" if press_enter else "")
    putty.set_clipboard(to_paste)
    putty.activate_window(hwnd)
    time.sleep(0.2)
    
    rect = win32gui.GetClientRect(hwnd)
    x = max(20, (rect[2] - rect[0]) // 2)
    y = max(20, (rect[3] - rect[1]) // 2)
    lparam = (y << 16) | (x & 0xFFFF)
    
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lparam)
    time.sleep(0.05)
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONUP, 0, lparam)
    time.sleep(0.2)

def main():
    git_h, claude_h = putty.select_putty_windows()
    print("\nSelect paste test:")
    print("1. Paste '# echo test' and EXECUTE (auto Enter via trailing newline)")
    print("2. Paste only without Enter")
    choice = input("Enter choice (1 or 2, default=1): ").strip() or "1"
    
    test_text = "# echo 'Test successful!'"
    
    if choice == "1":
        print(f"\nPasting with auto-execute on Git window (HWND {git_h})...")
        paste_via_mouse_click(git_h, test_text, press_enter=True)
    else:
        print(f"\nPasting without Enter on Git window (HWND {git_h})...")
        paste_via_mouse_click(git_h, test_text, press_enter=False)
        
    print("\nCheck your Git PuTTY window! Did it execute?")

if __name__ == "__main__":
    main()
