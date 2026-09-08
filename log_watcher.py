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

def detect_claude_question_or_choice(screen_text: str) -> tuple[bool, str]:
    """
    Detect if Claude Code is paused asking the user a question, presenting choices,
    or requesting tool execution permissions.
    Returns (is_asking, question_summary).
    """
    if not screen_text:
        return False, ""
    
    lines = [l.strip() for l in screen_text.splitlines() if l.strip()]
    if not lines:
        return False, ""
        
    bottom_lines = lines[-25:]
    bottom_text = "\n".join(bottom_lines)
    
    # Check for interactive menu / selection controls (excluding settings/usage dialogs)
    has_choice_nav = any(m in bottom_text.lower() for m in [
        "enter to select", "space to toggle", "space to select", 
        "arrows to move", "use arrow keys", "type something", 
        "type something else"
    ])
    
    # Check for numbered or bulleted options (e.g. 1) ... 2) ... or ❯ 1... or [1]... or - ...)
    numbered_options = []
    for line in bottom_lines:
        m = re.match(r'^(?:❯\s*)?(?:\(\d+\)|\[\d+\]|\d+[\.\)]|[a-zA-Z][\.\)]|\(?\s*[xX\s]\s*\)|[-*•]\s+)\s*(.+)', line)
        if m:
            numbered_options.append(line)
        elif line.lower().startswith("other") or "type something" in line.lower():
            numbered_options.append(line)

    has_multiple_options = len(numbered_options) >= 2

    # Check for question indicators
    question_keywords = [
        "would you like", "which option", "choose an option", 
        "select one", "pick 1 of", "pick one of", "please choose",
        "which approach", "how would you like", "do you prefer", "should i"
    ]
    question_lines = []
    for line in bottom_lines:
        lower = line.lower()
        if line.startswith("?") or line.endswith("?") or any(kw in lower for kw in question_keywords):
            if not line.startswith("root@") and not line.startswith("cd ") and not line.startswith("echo "):
                question_lines.append(line)

    has_question_prompt = len(question_lines) > 0

    # Check for permission prompts
    has_permission = any(q in bottom_text.lower() for q in [
        "allow?", "[y/n]", "(y/n)", "yes/no", "[y/n/always]", "allow tool", "allow command", "do you want to proceed"
    ])

    # Check the last meaningful line before prompt (if prompt ❯ is visible)
    meaningful_lines_before_prompt = []
    for l in bottom_lines:
        if l.startswith("❯") or l == "❯":
            break
        if not all(c in "─-=_ " for c in l) and not l.startswith("root@"):
            meaningful_lines_before_prompt.append(l)

    last_meaningful = meaningful_lines_before_prompt[-1] if meaningful_lines_before_prompt else ""
    last_is_question = (last_meaningful.endswith("?") or any(kw in last_meaningful.lower() for kw in question_keywords))

    is_asking = False
    details = []

    if has_permission:
        is_asking = True
        details.append("Permission requested ([y/n])")
        for line in bottom_lines[-6:]:
            if any(q in line.lower() for q in ["allow", "y/n", "yes/no", "proceed"]):
                details.append(line)
                break
    elif has_choice_nav or (has_multiple_options and (has_question_prompt or any("❯" in opt for opt in numbered_options))):
        is_asking = True
        if question_lines:
            details.append(question_lines[-1])
        details.extend(numbered_options[:5])
    elif has_question_prompt and has_multiple_options:
        is_asking = True
        details.append(question_lines[-1])
        details.extend(numbered_options[:5])
    elif has_question_prompt and any(line.startswith("?") for line in question_lines):
        is_asking = True
        details.append(question_lines[-1])
        if numbered_options:
            details.extend(numbered_options[:5])
    elif last_is_question and (has_multiple_options or len(meaningful_lines_before_prompt) >= 1):
        is_asking = True
        details.append(last_meaningful)
        if numbered_options:
            details.extend(numbered_options[:5])

    summary = "\n".join(details) if details else ""
    return is_asking, summary

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

    def wait_for_claude_completion(self, claude_hwnd: int = None, idle_seconds: int = 10, max_timeout: int = 3600, poll_interval: int = None, progress_callback=None) -> bool:
        """
        Wait until Claude Code finishes thinking and tool execution.
        - Preserves user clipboard and checks screen every `poll_interval` seconds (default 120s / 2 minutes).
        - Detects if Claude asks a question / choices: alerts via Telegram every 2 minutes until answered!
        - Only completes when Claude has genuinely finished the task.
        """
        import putty_controller as putty
        import telegram_alert

        if poll_interval is None:
            poll_interval = getattr(config, "CLAUDE_POLL_INTERVAL", 120)

        print(f"[CLAUDE MONITOR] Task submitted. Waiting 10s for Claude to engage...")
        time.sleep(10.0)

        start_time = time.time()
        in_question_mode = False

        print(f"[CLAUDE MONITOR] Watching Claude output (checking every {poll_interval}s; your clipboard is safe)...")

        # Initial check at 10s: check if Claude immediately asked a question or permission
        if claude_hwnd:
            curr_screen = putty.capture_screen_text(claude_hwnd)
            is_asking, q_details = detect_claude_question_or_choice(curr_screen)
            if is_asking:
                print(f"\n[CLAUDE MONITOR] ❓ Claude asked a question/choice immediately! Alerting Telegram...")
                telegram_alert.send_question_alert(q_details)
                in_question_mode = True

        while time.time() - start_time < max_timeout:
            if not claude_hwnd:
                time.sleep(1.0)
                continue

            curr_screen = putty.capture_screen_text(claude_hwnd)
            if not curr_screen:
                time.sleep(1.0)
                continue

            lines = [l.strip() for l in curr_screen.splitlines() if l.strip()]
            bottom_lines = lines[-20:]
            is_busy = any("esc to interrupt" in l.lower() for l in bottom_lines)
            is_asking, q_details = detect_claude_question_or_choice(curr_screen)

            # 1. Did the user just answer a question Claude was waiting on?
            if in_question_mode:
                if not is_asking or is_busy:
                    print(f"\n[CLAUDE MONITOR] 🚀 Answer detected! Claude resumed task processing...")
                    telegram_alert.send_answered_notification()
                    in_question_mode = False

            # 2. Is Claude asking a question or waiting for user choice/permission?
            if is_asking:
                elapsed_mins = int((time.time() - start_time) / 60)
                print(f"\n[CLAUDE MONITOR] ❓ Claude is waiting for user decision in PuTTY ({elapsed_mins}m elapsed)!")
                in_question_mode = True
                # Alert every 2 minutes
                telegram_alert.send_question_alert(q_details)
            elif is_busy:
                # 3. Is Claude actively working?
                elapsed_mins = int((time.time() - start_time) / 60)
                print(f"[CLAUDE MONITOR] ⏳ Claude is actively working... ({elapsed_mins}m elapsed)")
            else:
                # 4. Is Claude genuinely done?
                # Claude is NOT busy and NOT asking a question.
                has_done = any("· done" in l.lower() for l in bottom_lines)
                has_prompt = any(l.startswith("❯") or l == "❯" for l in bottom_lines)

                if has_done or has_prompt:
                    print(f"\n[CLAUDE MONITOR] ✅ Claude has genuinely completed the task! (Prompt returned)")
                    return True

            # Sleep poll_interval seconds in 1s increments for responsive interruption
            sleep_needed = poll_interval
            while sleep_needed > 0 and (time.time() - start_time < max_timeout):
                time.sleep(1.0)
                sleep_needed -= 1

        print(f"\n[CLAUDE MONITOR] ⚠️ Timeout reached while waiting for Claude to finish!")
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
