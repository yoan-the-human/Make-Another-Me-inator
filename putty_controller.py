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
    """Set text to Windows clipboard with retry mechanism and verification."""
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            if get_clipboard() == text:
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
    """Capture all visible text from a PuTTY window using native IDM_COPYALL while preserving user clipboard."""
    ensure_desktop()
    orig_text = get_clipboard()
    result = ""
    try:
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        win32gui.SendMessage(hwnd, win32con.WM_SYSCOMMAND, 0x0170, 0)
        for _ in range(5):
            time.sleep(0.05)
            text = get_clipboard()
            if text:
                result = text
                break
    except Exception as e:
        result = ""
    finally:
        # Immediately restore user's original clipboard content
        if orig_text:
            set_clipboard(orig_text)
        else:
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.CloseClipboard()
            except Exception:
                pass
    return result

def wait_for_screen_text(hwnd: int, patterns: list, timeout: int = 60, poll_interval: float = 1.0) -> bool:
    """
    Wait until ANY of the specified patterns appear on the PuTTY window screen as output.
    Returns True when found, False on timeout.
    Only checks recent terminal lines (bottom 30 lines) to avoid matching historical scrollback!
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        text = capture_screen_text(hwnd)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        recent_lines = lines[-30:]
        for pat in patterns:
            # Match exact line or isolated output, ignoring the command input echo line
            if any((l == pat or l == f"'{pat}'" or l == f'"{pat}"') for l in recent_lines if not l.startswith("echo ") and " && echo " not in l and not l.startswith("root@")):
                return True
        time.sleep(poll_interval)
    return False

def wait_for_git_worktree(hwnd: int, worktree_dir: str, timeout: int = 60, marker: str = None) -> bool:
    """
    Waits for git worktree checkout (e.g. 43,000 files) to reach 100% and return to shell prompt.
    Does NOT trigger on the pasted command line!
    Only checks recent terminal lines to avoid false triggers from prior tasks.
    """
    if not marker:
        marker = "==WORKTREE_READY_100=="
    start_time = time.time()
    last_pct = ""
    
    while time.time() - start_time < timeout:
        screen = capture_screen_text(hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-30:]
        
        # Display checkout progress if visible
        for line in recent_lines:
            m = re.search(r'Updating files:\s+(\d+%)', line)
            if m and m.group(1) != last_pct:
                last_pct = m.group(1)
                print(f"[GIT PUTTY] Unpacking files: {last_pct}...", flush=True)
                
        # The marker MUST appear as its own isolated line from echo, NOT the command line
        has_marker_line = any((l == marker or l == f"'{marker}'") for l in recent_lines if not l.startswith("echo ") and " && echo " not in l)
        has_head = any("HEAD is now at" in l or ", done." in l for l in recent_lines)
        
        if has_marker_line and has_head:
            print(f"[GIT PUTTY] ✅ All files updated! Worktree checkout 100% complete.")
            return True
            
        time.sleep(1.0)
        
    print("[WARNING] Timed out waiting for worktree checkout!")
    return False

def wait_for_git_branch(hwnd: int, branch_name: str, marker: str, timeout: int = 60) -> bool:
    """
    Waits for git checkout/branch creation in Git PuTTY to complete and echo marker.
    - Dynamically monitors for credentials prompts (Username/Password) and fills them automatically.
    - Checks recent terminal lines for isolated marker and branch switch confirmation.
    """
    start_time = time.time()
    user_sent = False
    pass_sent = False

    while time.time() - start_time < timeout:
        screen = capture_screen_text(hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-30:]
        bottom_lines = lines[-5:] if len(lines) >= 5 else lines
        bottom_text = "\n".join(bottom_lines).lower()

        # 1. Check success marker
        has_marker = any(
            (l == marker or l == f"'{marker}'" or l == f'"{marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and " && echo " not in l and not l.startswith("root@")
        )
        if has_marker:
            print(f"[GIT PUTTY] ✅ Switched to branch '{branch_name}' successfully!")
            return True

        # 2. Handle Username prompt (Check before error checks!)
        if not user_sent and any("username for" in l.lower() for l in bottom_lines):
            print("[GIT PUTTY] Detected Username prompt! Entering username...")
            time.sleep(0.5)
            paste_text(hwnd, config.GIT_USERNAME, press_enter=True)
            user_sent = True
            time.sleep(1.0)
            continue

        # 3. Handle Password prompt (Check before error checks!)
        if not pass_sent and any("password for" in l.lower() for l in bottom_lines):
            print("[GIT PUTTY] Detected Password prompt! Entering password...")
            time.sleep(0.5)
            paste_text(hwnd, config.GIT_PASSWORD, press_enter=True)
            pass_sent = True
            time.sleep(2.0)
            continue

        # 4. Check for authentication errors ONLY after password has been submitted!
        if pass_sent:
            has_auth_err = any(err in bottom_text for err in [
                "authentication failed",
                "access denied",
                "invalid username or password",
                "fatal: authentication"
            ])
            if has_auth_err:
                print(f"\n[GIT PUTTY] 🚨 Authentication failed after entering password for branch '{branch_name}'!")
                return False

        time.sleep(1.0)

    print(f"[WARNING] Timed out waiting for branch switch confirmation for '{branch_name}'!")
    return False




def flash_window(hwnd: int):
    """Flash window caption bar to visually draw user attention."""
    try:
        win32gui.FlashWindow(hwnd, True)
    except Exception:
        pass

def send_enter(hwnd: int):
    """Send a single Enter key to PuTTY via WM_CHAR 13 (\r)."""
    win32gui.PostMessage(hwnd, win32con.WM_CHAR, 13, 0)
    time.sleep(0.15)



def send_down_arrow(hwnd: int):
    """Send Down Arrow key to PuTTY window procedure."""
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_DOWN, 0x01500001)
    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_DOWN, 0xC1500001)
    # Terminal escape sequence for down arrow (\x1b[B)
    for code in [27, 91, 66]:
        win32gui.PostMessage(hwnd, win32con.WM_CHAR, code, 0)
    time.sleep(0.1)

def send_escape(hwnd: int):
    """Send Escape key directly to PuTTY window procedure to dismiss dialogs."""
    win32gui.PostMessage(hwnd, win32con.WM_CHAR, 27, 0)
    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0x00010001)
    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0xC0010001)
    time.sleep(0.1)

def parse_current_session_usage(screen_text: str) -> tuple[float | None, str]:
    """
    Parse current session usage percentage and reset time from Claude Code terminal output.
    Returns (percentage, reset_time_str).
    """
    matches = list(re.finditer(
        r'Current\s+session(?:(?!Current\s+week|Weekly|Total\s+cost).)*?(\d+(?:\.\d+)?)\s*%',
        screen_text,
        re.IGNORECASE | re.DOTALL
    ))
    pct = float(matches[-1].group(1)) if matches else None
    
    # Fallback line-by-line search from bottom up
    if pct is None:
        lines = screen_text.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if "current session" in lines[i].lower():
                for j in range(i, min(i + 4, len(lines))):
                    m = re.search(r'(\d+(?:\.\d+)?)\s*%', lines[j])
                    if m:
                        pct = float(m.group(1))
                        break
                if pct is not None:
                    break

    # Extract reset time if available
    reset_time = ""
    reset_matches = list(re.finditer(
        r'Current\s+session.*?Resets\s+([^\n\r]+)',
        screen_text,
        re.IGNORECASE | re.DOTALL
    ))
    if reset_matches:
        reset_time = reset_matches[-1].group(1).strip()

    return pct, reset_time

def check_claude_usage(claude_hwnd: int, threshold: float = None) -> tuple[float | None, str, bool]:
    """
    Execute /usage command in Claude PuTTY, capture screen, parse Current Session usage,
    dismiss the dialog with Escape, and return (percentage, reset_time, is_over_threshold).
    """
    if threshold is None:
        threshold = config.USAGE_THRESHOLD

    print("\n[CLAUDE USAGE] 📊 Checking current session usage via /usage...")
    paste_text(claude_hwnd, "/usage", press_enter=True)
    time.sleep(2.0)

    screen = capture_screen_text(claude_hwnd)
    send_escape(claude_hwnd)
    time.sleep(0.5)

    pct, reset_time = parse_current_session_usage(screen)

    if pct is not None:
        reset_info = f" (Resets: {reset_time})" if reset_time else ""
        print(f"[CLAUDE USAGE] -> Current session usage: {pct}%{reset_info} | Threshold: {threshold}%")
        is_over = pct >= threshold
        if is_over:
            print(f"[CLAUDE USAGE] ⚠️ ALERT: Session usage {pct}% has reached or exceeded {threshold}% limit!")
        return pct, reset_time, is_over
    else:
        print("[CLAUDE USAGE] ⚠️ Could not determine Current Session percentage from terminal screen.")
        return None, "", False


def clear_claude_context(claude_hwnd: int, session_name: str = "", timeout: int = 15) -> bool:
    """
    Safely execute /clear [session_name] in Claude PuTTY and actively verify that it executes,
    clearing previous context and waiting for a clean prompt ('❯ ').
    Prevents prompt concatenation errors (e.g. '/clearcheck...').
    """
    cmd = f"/clear {session_name}".strip() if session_name else "/clear"
    print(f"\n[CLAUDE PUTTY] 🧹 Clearing previous context ({cmd})...")
    activate_window(claude_hwnd)
    time.sleep(0.2)

    # If prompt line currently has unsubmitted characters, dismiss them first
    screen = capture_screen_text(claude_hwnd)
    lines = [l.strip() for l in screen.splitlines() if l.strip()]
    prompt_line = next((l for l in reversed(lines) if "❯" in l), "")
    clean_p = prompt_line.replace("\xa0", " ").strip()
    if clean_p and clean_p != "❯":
        print(f"[CLAUDE PUTTY] Detected unsubmitted text on prompt: '{clean_p}'. Clearing line...")
        send_escape(claude_hwnd)
        time.sleep(0.2)

    # Paste /clear and submit Enter
    paste_text(claude_hwnd, cmd, press_enter=True)
    time.sleep(0.5)

    start_time = time.time()
    resend_count = 0
    while time.time() - start_time < timeout:
        screen = capture_screen_text(claude_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-15:]
        prompt_line = next((l for l in reversed(recent_lines) if "❯" in l), "")
        clean_prompt = prompt_line.replace("\xa0", " ").strip()

        # If /clear is still sitting unsubmitted on prompt line, resend Enter
        if "/clear" in clean_prompt:
            resend_count += 1
            if resend_count <= 3:
                print(f"[CLAUDE PUTTY] ⏳ {cmd} still on prompt line ('{clean_prompt}'). Resending Enter...")
                activate_window(claude_hwnd)
                send_enter(claude_hwnd)
                time.sleep(0.8)
                continue

        # If prompt is cleanly reset to '❯' without /clear
        if clean_prompt == "❯":
            print("[CLAUDE PUTTY] ✅ Context successfully cleared! Clean prompt '❯' confirmed.")
            time.sleep(1.0)
            return True

        time.sleep(0.5)

    print(f"[CLAUDE PUTTY] ⚠️ Timed out waiting for clean prompt after {cmd}, proceeding with caution.")
    return False


def paste_text(hwnd: int, text: str, press_enter: bool = True, use_mouse: bool = True):
    """
    Bring target window to foreground, set clipboard, right-click paste into PuTTY,
    wait for terminal to register, and send Enter.
    """
    activate_window(hwnd)
    time.sleep(0.15)

    clean_text = text.rstrip("\r\n")
    if not set_clipboard(clean_text):
        print(f"[ERROR] Failed to set clipboard with text ({len(clean_text)} chars)!")
        return False
    time.sleep(0.15)

    rect = win32gui.GetClientRect(hwnd)
    cx = max(10, (rect[2] - rect[0]) // 2)
    cy = max(10, (rect[3] - rect[1]) // 2)
    lp = (cy << 16) | (cx & 0xFFFF)

    # Native PuTTY right-click paste
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, lp)
    time.sleep(0.05)
    win32gui.SendMessage(hwnd, win32con.WM_RBUTTONUP, 0, lp)

    settle = max(0.4, min(1.5, len(clean_text) * 0.0015))
    time.sleep(settle)

    if press_enter:
        send_enter(hwnd)
    return True


def detect_git_working_dir(git_hwnd: int, timeout: int = 10) -> str:
    """
    Query Git PuTTY for current working directory using `pwd` wrapped in unique markers.
    Returns cleaned path (e.g. '/data/development' or '/data/exo-android').
    """
    uid = f"{int(time.time() * 1000)}"
    start_marker = f"==DIR_START_{uid}=="
    end_marker = f"==DIR_END_{uid}=="
    cmd = f"echo '{start_marker}' ; pwd ; echo '{end_marker}'"

    activate_window(git_hwnd)
    time.sleep(0.2)
    paste_text(git_hwnd, cmd, press_enter=True)

    start_time = time.time()
    while time.time() - start_time < timeout:
        screen = capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]

        start_idx = -1
        end_idx = -1
        for idx, line in enumerate(lines):
            # Ignore echo command itself or compound lines
            if line.startswith("echo ") or " ; echo " in line or "pwd ;" in line:
                continue
            if start_marker in line:
                start_idx = idx
            elif end_marker in line and start_idx != -1:
                end_idx = idx
                break

        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            for i in range(start_idx + 1, end_idx):
                cand = lines[i].strip().strip("'\"")
                if cand.startswith("/") and " " not in cand and "@" not in cand:
                    clean_cand = cand.rstrip("/")
                    return clean_cand
        time.sleep(0.3)

    return ""


def ensure_claude_directory(claude_hwnd: int, target_dir: str, watcher=None, timeout: int = 20) -> bool:
    """
    Ensure Claude Code is working in target_dir using `/cd <target_dir>`.
    Handles directory trust prompt if it appears.
    If Claude is not running yet (at shell prompt), starts Claude in target_dir via `cd <target_dir> && claude`.
    """
    if not target_dir:
        return False

    print(f"\n[CLAUDE PUTTY] 🧭 Positioning Claude in working directory: {target_dir}...")
    activate_window(claude_hwnd)
    time.sleep(0.2)

    screen = capture_screen_text(claude_hwnd)
    lines = [l.strip() for l in screen.splitlines() if l.strip()]
    recent = lines[-10:] if lines else []
    is_claude_running = any("❯" in l for l in recent)

    if not is_claude_running:
        # Claude is at bash prompt! Send cd <target_dir> && claude
        print(f"[CLAUDE PUTTY] Shell prompt detected (Claude not running). Navigating and starting Claude...")
        cmd = f"cd {target_dir} && claude"
        paste_text(claude_hwnd, cmd, press_enter=True)
        start_time = time.time()
        while time.time() - start_time < timeout:
            s = capture_screen_text(claude_hwnd)
            s_lines = [l.strip() for l in s.splitlines() if l.strip()]
            if any("❯" in l for l in s_lines[-10:]):
                print(f"[CLAUDE PUTTY] ✅ Claude started and positioned in '{target_dir}'!")
                return True
            time.sleep(0.5)
        return True

    # Claude Code CLI is running! Clear line if unsubmitted text
    prompt_line = next((l for l in reversed(recent) if "❯" in l), "")
    clean_p = prompt_line.replace("\xa0", " ").strip()
    if clean_p and clean_p != "❯":
        send_escape(claude_hwnd)
        time.sleep(0.2)

    # Paste /cd <target_dir>
    cmd = f"/cd {target_dir}"
    paste_text(claude_hwnd, cmd, press_enter=True)
    time.sleep(1.0)

    # Check if trust prompt appears
    print("[CLAUDE PUTTY] 🔍 Checking for directory trust prompt...")
    has_trust = False
    if watcher and hasattr(watcher, "wait_for_trust_prompt"):
        has_trust = watcher.wait_for_trust_prompt(timeout=6, claude_hwnd=claude_hwnd)
    else:
        trust_patterns = [
            "this session hasn't worked here before",
            "is this a directory you created",
            "no, stay put",
            "yes, move here"
        ]
        t_start = time.time()
        while time.time() - t_start < 6:
            s_text = capture_screen_text(claude_hwnd).lower()
            if any(p in s_text for p in trust_patterns):
                has_trust = True
                break
            time.sleep(0.5)

    if has_trust:
        print("[CLAUDE PUTTY] ⚠️ Detected directory trust prompt! Selecting 'Yes, move here' (Down Arrow + Enter)...")
        time.sleep(0.5)
        send_down_arrow(claude_hwnd)
        time.sleep(0.3)
        send_enter(claude_hwnd)
        time.sleep(1.5)
    else:
        print("[CLAUDE PUTTY] ✅ No trust prompt detected (or directory already trusted).")

    # Wait for clean prompt '❯'
    start_time = time.time()
    while time.time() - start_time < timeout:
        screen = capture_screen_text(claude_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent = lines[-10:]
        prompt_line = next((l for l in reversed(recent) if "❯" in l), "")
        clean_prompt = prompt_line.replace("\xa0", " ").strip()
        if clean_prompt == "❯":
            print(f"[CLAUDE PUTTY] ✅ Claude successfully positioned in '{target_dir}'!")
            time.sleep(0.5)
            return True
        time.sleep(0.5)

    print(f"[CLAUDE PUTTY] ⚠️ Proceeding after /cd {target_dir}.")
    return True


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
