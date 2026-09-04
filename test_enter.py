import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import win32gui
import win32con
import putty_controller as putty

def paste_and_submit(hwnd: int, text: str):
    """
    1. Copies text to clipboard.
    2. Sends right-click to PuTTY to paste.
    3. Waits 0.25s for text to arrive.
    4. Sends WM_CHAR 13 (\\r) to execute!
    """
    # Clean text without trailing newline
    clean_text = text.rstrip("\r\n")
    putty.set_clipboard(clean_text)
    time.sleep(0.1)
    
    # Right-click paste
    rect = win32gui.GetClientRect(hwnd)
    cx = max(10, (rect[2] - rect[0]) // 2)
    cy = max(10, (rect[3] - rect[1]) // 2)
    lp = (cy << 16) | (cx & 0xFFFF)
    
    print(f"1. Pasting '{clean_text}' via right-click...")
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lp)
    time.sleep(0.05)
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONUP, 0, lp)
    
    # Wait for paste to settle in terminal
    time.sleep(0.3)
    
    # Send Enter
    print("2. Sending Enter (WM_CHAR 13)...")
    win32gui.PostMessage(hwnd, win32con.WM_CHAR, 13, 0)
    print("3. Done!")

def main():
    git_h, claude_h = putty.select_putty_windows()
    test_cmd = "echo 'TEST_SUCCESS'"
    
    print(f"\nTarget Window: Git PuTTY (HWND {git_h})")
    print(f"Command to run: {test_cmd}")
    input("Press Enter to fire the paste + execute test...")
    
    paste_and_submit(git_h, test_cmd)
    
    print(f"\nLook at your Git PuTTY window! You should see:")
    print(f"  echo 'TEST_SUCCESS'")
    print(f"  TEST_SUCCESS")

if __name__ == "__main__":
    main()
