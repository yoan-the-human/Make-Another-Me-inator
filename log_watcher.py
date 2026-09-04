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

    def wait_for_trust_prompt(self, timeout: int = 15) -> bool:
        """Wait for Claude Code directory trust prompt."""
        patterns = [
            "This session hasn't worked here before",
            "Is this a directory you created",
            "No, stay put",
            "Yes, move here"
        ]
        return self.wait_for_pattern(patterns, timeout=timeout)

    def wait_for_claude_completion(self, idle_seconds: int = 7, max_timeout: int = 1800, progress_callback=None) -> bool:
        """
        Wait until Claude Code finishes thinking and tool execution.
        Strategy:
        1. Tracks incoming tokens/stream.
        2. Detects idle period where no new bytes arrive for `idle_seconds`.
        3. Verifies prompt/completion signature in recent output.
        """
        start_time = time.time()
        last_change_time = time.time()
        had_output = False
        recent_buffer = ""
        
        print(f"[CLAUDE MONITOR] Watching Claude output (will complete after {idle_seconds}s of silence)...")
        
        while time.time() - start_time < max_timeout:
            chunk = self.get_new_text()
            current_time = time.time()
            
            if chunk:
                had_output = True
                last_change_time = current_time
                recent_buffer = (recent_buffer + chunk)[-4000:] # Keep last 4KB
                if progress_callback:
                    progress_callback(chunk)
                else:
                    # Print brief dot indicator for live activity
                    sys.stdout.write(".")
                    sys.stdout.flush()
            else:
                # No new bytes
                idle_duration = current_time - last_change_time
                
                # Check for permission prompt waiting for input
                if "yes/no" in recent_buffer.lower() or "allow" in recent_buffer.lower() and "[y/n]" in recent_buffer.lower():
                    print("\n[WARNING] Claude may be waiting for a permission confirmation! (Check Claude PuTTY window)")
                
                # If we had activity and now it's quiet for idle_seconds
                if had_output and idle_duration >= idle_seconds:
                    # Look for Claude prompt symbols
                    indicators = ["❯", "? for", "Cost:", "duration", "╭─", "╰─", "bypass permissions"]
                    has_prompt = any(ind in recent_buffer for ind in indicators)
                    
                    if has_prompt or idle_duration >= (idle_seconds + 5):
                        print(f"\n[CLAUDE MONITOR] Claude has completed the task! (Quiet for {int(idle_duration)}s)")
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
