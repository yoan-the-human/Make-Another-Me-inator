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
