import sys
import time
import requests
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config

def send_telegram_message(text: str) -> bool:
    """Send message to Telegram via Bot API."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Error: Missing Bot Token or Chat ID in .env!")
        return False
    
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[TELEGRAM] Message sent successfully to Telegram!")
            return True
        else:
            # Try without markdown in case of formatting error
            payload.pop("parse_mode", None)
            res2 = requests.post(url, json=payload, timeout=10)
            if res2.status_code == 200:
                print("[TELEGRAM] Message sent successfully (plain text)!")
                return True
            print(f"[TELEGRAM] Failed to send message: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[TELEGRAM] Connection error: {e}")
        return False

def send_quota_alert(usage_pct: float, reset_time: str = "") -> bool:
    """
    Alert user via Telegram that Claude session quota reached or exceeded threshold.
    """
    msg = f"⚠️ *Vnimaniye, Comrade Yoan!*\n\nClaude session usage has reached *{usage_pct:.1f}%* (threshold is {config.USAGE_THRESHOLD:.0f}%)!"
    if reset_time:
        msg += f"\n⏳ *Quota resets at:* `{reset_time}`"
    msg += "\n\n🛑 Automated pipeline is halting all tasks to protect your quota from hitting the wall.\nRest by the samovar, drink vodka, and wait for reset, tovarisch! 🪆🐻"
    return send_telegram_message(msg)

def send_question_alert(question_text: str) -> bool:
    """
    Alert user via Telegram that Claude is asking a question or waiting for user choice.
    """
    preview = question_text.strip() if question_text else "Claude is asking you to pick an option or give permission."
    if len(preview) > 800:
        preview = preview[:800] + "..."

    msg = (
        f"❓ *Vnimaniye, Comrade Yoan! Claude has a question!*\n\n"
        f"Claude is waiting for your choice in PuTTY:\n"
        f"```\n{preview}\n```\n"
        f"⚠️ Please switch to Claude PuTTY window and make your selection!\n"
        f"Machine will check every 2 minutes if you answered, tovarisch! 🪆🐻"
    )
    return send_telegram_message(msg)

def send_answered_notification() -> bool:
    """
    Notify user that answer was detected in PuTTY and Claude resumed work.
    """
    msg = "🚀 *Orders received!* Detected your answer to Claude in PuTTY. Task execution resumed! ☭"
    return send_telegram_message(msg)

def send_git_auth_failed_alert(branch_name: str, worktree_dir: str) -> bool:
    """
    Alert user via Telegram that git push authentication failed and pipeline is halted.
    """
    msg = (
        f"🚨 *Vnimaniye, Comrade Yoan! Git Push Failed!*\n\n"
        f"Authentication failed while pushing branch `{branch_name}` in worktree `{worktree_dir}`!\n\n"
        f"🛑 *Pipeline is HALTED.* Please switch to Git PuTTY window, provide correct credentials or push manually.\n"
        f"Machine is standing guard and waiting to detect Git's confirmation message, tovarisch! 🪆🐻"
    )
    return send_telegram_message(msg)

def send_git_push_confirmed_notification(branch_name: str) -> bool:
    """
    Notify user that git push confirmation was detected and pipeline resumed.
    """
    msg = f"✅ *Spasibo, Comrade Yoan!* Git push confirmed for `{branch_name}`! Resuming pipeline! 🚀☭"
    return send_telegram_message(msg)

def wait_for_new_task_or_alert(interval_seconds=120, stop_event=None):
    """
    Called when BOTH pending and working folders are completely empty.
    Alerts user every `interval_seconds`, but checks every second if a new file appeared.
    Returns True if a new task was detected, False if stopped.
    """
    pending_files = list(config.PENDING_DIR.glob("*.txt"))
    working_files = list(config.WORKING_DIR.glob("*.txt"))
    
    if pending_files or working_files:
        return True # Not empty, do not send false completion alert!

    print(f"\n[QUEUE EMPTY] Both pending and working folders are empty! Will alert via Telegram every {interval_seconds}s until new task appears.")
    
    # Send initial alert
    send_telegram_message("📢 *Comrade Yoan!* All tasks in `pending` and `working` folders are completed!\nWaiting for new tasks...")
    
    elapsed = 0
    while True:
        if stop_event and stop_event.is_set():
            return False
            
        # Check if new files dropped into pending or working
        pending_files = list(config.PENDING_DIR.glob("*.txt"))
        working_files = list(config.WORKING_DIR.glob("*.txt"))
        if pending_files or working_files:
            found_name = pending_files[0].name if pending_files else working_files[0].name
            print(f"[QUEUE] Detected task in queue: '{found_name}'! Resuming work...")
            send_telegram_message(f"🚀 *New task detected:* `{found_name}`. Resuming automation!")
            return True
            
        time.sleep(1)
        elapsed += 1
        
        if elapsed >= interval_seconds:
            send_telegram_message("⏳ *Reminder:* Both pending and working folders are still empty. Machine is resting in the bunker.")
            elapsed = 0

if __name__ == "__main__":
    print("Testing Telegram connection...")
    success = send_telegram_message("🇷🇺 *Privet from Make-Another-Me-inator!* Automation bot is online and ready!")
    if success:
        print("Telegram test passed!")
    else:
        print("Telegram test failed. Please verify bot token and chat ID.")
