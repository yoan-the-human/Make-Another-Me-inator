import os
import sys
import time
import re
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config

ANSI_ESCAPE_REGEX = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b\([a-zA-Z]')

def clean_ansi(text: str) -> str:
    """Remove ANSI escape sequences and non-printable control codes."""
    no_ansi = ANSI_ESCAPE_REGEX.sub('', text)
    # Strip non-printable chars except tab and newline
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', no_ansi)

class LogWatcher:
    def __init__(self, log_path: Path = None):
        self.log_path = Path(log_path or config.PUTTY_LOG_PATH)
        self.last_pos = 0
        self.ensure_file()

    def ensure_file(self):
        """Ensure the log file exists. If not, prompt user or wait for it."""
        if not self.log_path.exists():
            print(f"[LOG] PuTTY log file not found at: {self.log_path}")
            # Try to see if there is a putty*.log in project dir
            possible = list(config.BASE_DIR.glob("*.log"))
            if possible:
                print(f"[LOG] Found existing log file: {possible[0]}")
                self.log_path = possible[0]
            else:
                user_path = input(f"Enter the path to your PuTTY session log file (or press Enter for default '{self.log_path}'): ").strip()
                if user_path:
                    self.log_path = Path(user_path)
                    
        # If still does not exist, create empty so open won't crash
        if not self.log_path.exists():
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.touch()
            print(f"[LOG] Initialized empty log file at: {self.log_path}")
            
        self.last_pos = self.log_path.stat().st_size
        print(f"[LOG] Monitoring log: {self.log_path} (initial offset: {self.last_pos} bytes)")

    def mark_start(self):
        """Record current file offset before sending a command."""
        if self.log_path.exists():
            self.last_pos = self.log_path.stat().st_size
        else:
            self.last_pos = 0

    def get_new_text(self, clean: bool = True) -> str:
        """Read newly appended content since last check."""
        if not self.log_path.exists():
            return ""
            
        current_size = self.log_path.stat().st_size
        if current_size < self.last_pos:
            # File was truncated or restarted
            self.last_pos = 0
            
        if current_size == self.last_pos:
            return ""
            
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self.last_pos)
                new_data = f.read()
                self.last_pos = f.tell()
                return clean_ansi(new_data) if clean else new_data
        except Exception as e:
            return ""

    def wait_for_pattern(self, patterns: list, timeout: int = 30, check_interval: float = 0.5) -> bool:
        """
        Wait until ANY of the specified patterns appear in the new log output.
        Returns True if pattern detected, False if timeout.
        """
        start_time = time.time()
        buffer = ""
        
        while time.time() - start_time < timeout:
            chunk = self.get_new_text()
            if chunk:
                buffer += chunk
                for pat in patterns:
                    if pat.lower() in buffer.lower():
                        return True
            time.sleep(check_interval)
            
        return False

    def wait_for_trust_prompt(self, timeout: int = 15, claude_hwnd: int = None) -> bool:
        """Wait for Claude Code directory trust prompt (checks log file and live screen)."""
        patterns = [
            "This session hasn't worked here before",
            "Is this a directory you created",
            "No, stay put",
            "Yes, move here"
        ]
        start_time = time.time()
        while time.time() - start_time < timeout:
            chunk = self.get_new_text()
            if chunk and any(p.lower() in chunk.lower() for p in patterns):
                return True
            if claude_hwnd:
                try:
                    import putty_controller as putty
                    screen = putty.capture_screen_text(claude_hwnd)
                    if any(p.lower() in screen.lower() for p in patterns):
                        return True
                except Exception:
                    pass
            time.sleep(0.5)
        return False

    def wait_for_claude_completion(self, claude_hwnd: int = None, idle_seconds: int = 10, max_timeout: int = 2400, progress_callback=None) -> bool:
        """
        Wait until Claude Code finishes thinking and tool execution.
        Monitors both the PuTTY session log and live terminal screen text.
        """
        print(f"[CLAUDE MONITOR] Prompt submitted. Waiting 6s for Claude to engage engine...")
        time.sleep(6.0)
        
        start_time = time.time()
        last_change_time = time.time()
        had_output = False
        recent_buffer = ""
        last_screen = ""
        
        if claude_hwnd:
            try:
                import putty_controller as putty
                last_screen = putty.capture_screen_text(claude_hwnd)
            except Exception:
                pass
        
        print(f"[CLAUDE MONITOR] Watching Claude output (will complete when Claude returns prompt)...")
        stable_done_count = 0
        
        while time.time() - start_time < max_timeout:
            chunk = self.get_new_text()
            current_time = time.time()
            screen_changed = False
            curr_screen = ""
            
            if claude_hwnd:
                try:
                    import putty_controller as putty
                    curr_screen = putty.capture_screen_text(claude_hwnd)
                    if curr_screen and curr_screen != last_screen:
                        screen_changed = True
                        last_screen = curr_screen
                except Exception:
                    pass
            
            if chunk or screen_changed:
                had_output = True
                last_change_time = current_time
                stable_done_count = 0
                if chunk:
                    recent_buffer = (recent_buffer + chunk)[-4000:]
                if progress_callback and chunk:
                    progress_callback(chunk)
                else:
                    sys.stdout.write(".")
                    sys.stdout.flush()
            else:
                idle_duration = current_time - last_change_time
                
                # Check if Claude is still actively processing
                # Claude ONLY displays 'esc to interrupt' in bottom status lines while busy
                is_busy = False
                has_done = False
                has_prompt = False
                
                if curr_screen:
                    lines = [l.strip() for l in curr_screen.splitlines() if l.strip()]
                    bottom_lines = lines[-12:]
                    is_busy = any("esc to interrupt" in l.lower() for l in lines)
                    has_done = any("· done" in l for l in bottom_lines)
                    has_prompt = any(l.startswith("❯") or l == "❯" for l in bottom_lines)
                
                # Check for permission prompt
                if curr_screen:
                    bottom_text = " ".join(lines[-10:]).lower() if 'lines' in locals() else ""
                    if any(q in bottom_text for q in ["allow?", "[y/n]", "(y/n)", "yes/no"]):
                        print("\n[WARNING] Claude may be waiting for a permission confirmation! (Check Claude PuTTY window)")
                elif "yes/no" in recent_buffer.lower() or ("allow" in recent_buffer.lower() and "[y/n]" in recent_buffer.lower()):
                    print("\n[WARNING] Claude may be waiting for a permission confirmation! (Check Claude PuTTY window)")
                
                # Check completion:
                # 1) If we see "· done" and prompt "❯" with NO "esc to interrupt", Claude is done!
                # 2) Or if prompt "❯" is present and idle for >= idle_seconds with NO "esc to interrupt"
                if not is_busy and (has_done or has_prompt):
                    stable_done_count += 1
                    if stable_done_count >= 3:
                        print(f"\n[CLAUDE MONITOR] ✅ Claude has completed the task! (Prompt returned, idle for {int(idle_duration)}s)")
                        return True
                elif had_output and not is_busy and idle_duration >= idle_seconds:
                    indicators = ["❯", "? for", "Cost:", "duration", "╭─", "╰─", "bypass permissions"]
                    if any(ind in recent_buffer for ind in indicators) or has_prompt:
                        print(f"\n[CLAUDE MONITOR] ✅ Claude has completed the task! (Prompt returned, idle for {int(idle_duration)}s)")
                        return True

            time.sleep(1.0)
            
        print("\n[CLAUDE MONITOR] Timeout reached while waiting for Claude to finish!")
        return False

if __name__ == "__main__":
    watcher = LogWatcher()
    watcher.mark_start()
    print("Log watcher test initialized. Listening for changes for 10 seconds...")
    for _ in range(10):
        t = watcher.get_new_text()
        if t:
            print("NEW LOG DATA:", t)
        time.sleep(1)
