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

_last_update_id = 0
_is_dark = False

def is_dark_mode() -> bool:
    """Check if dark mode (silenced periodic idle reminders) is currently active."""
    return _is_dark

def set_dark_mode(enabled: bool):
    """Enable or disable dark mode."""
    global _is_dark
    _is_dark = enabled


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

def init_telegram_listener():
    """Discard all past unread messages on startup so old commands aren't executed."""
    global _last_update_id
    if not config.TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, params={"offset": -1, "timeout": 0}, timeout=5)
        if res.status_code == 200:
            data = res.json()
            results = data.get("result", [])
            if results:
                _last_update_id = results[-1]["update_id"]
                print(f"[TELEGRAM] Initialized command listener (last update id: {_last_update_id})")
    except Exception as e:
        print(f"[TELEGRAM] Warning during listener init: {e}")

def check_telegram_commands() -> list[tuple[str, list[str]]]:
    """
    Poll Telegram getUpdates for new messages from the authorized user chat.
    Returns a list of tuples: (cmd, args).
    """
    global _last_update_id
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return []

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"offset": _last_update_id + 1, "timeout": 0}
    
    commands = []
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            data = res.json()
            for update in data.get("result", []):
                _last_update_id = update["update_id"]
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id != str(config.TELEGRAM_CHAT_ID):
                    continue
                
                raw_text = msg.get("text", "").strip()
                if not raw_text:
                    continue

                text = raw_text.split("@")[0].strip().lstrip("/")
                parts = text.split()
                if not parts:
                    continue
                cmd, args = parts[0].lower(), parts[1:]
                commands.append((cmd, args))
    except Exception:
        pass
    return commands

def send_quota_alert(usage_pct: float, reset_time: str = "") -> bool:
    """Alert user via Telegram that Claude session quota reached or exceeded threshold."""
    msg = f"⚠️ *Vnimaniye, Comrade Yoan!*\n\nClaude session usage has reached *{usage_pct:.1f}%* (threshold is {config.USAGE_THRESHOLD:.0f}%)!"
    if reset_time:
        msg += f"\n⏳ *Quota resets at:* `{reset_time}`"
    msg += "\n\n🛑 Automated pipeline is halting all tasks to protect your quota from hitting the wall.\nRest by the samovar, drink vodka, and wait for reset, tovarisch! 🪆🐻"
    return send_telegram_message(msg)

def send_question_alert(question_text: str) -> bool:
    """Alert user via Telegram that Claude is asking a question or waiting for user choice."""
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
    """Notify user that answer was detected in PuTTY and Claude resumed work."""
    msg = "🚀 *Orders received!* Detected your answer to Claude in PuTTY. Task execution resumed! ☭"
    return send_telegram_message(msg)

def send_git_auth_failed_alert(branch_name: str, worktree_dir: str) -> bool:
    """Alert user via Telegram that git push authentication failed and pipeline is halted."""
    msg = (
        f"🚨 *Vnimaniye, Comrade Yoan! Git Push Failed!*\n\n"
        f"Authentication failed while pushing branch `{branch_name}` in worktree `{worktree_dir}`!\n\n"
        f"🛑 *Pipeline is HALTED.* Please switch to Git PuTTY window, provide correct credentials or push manually.\n"
        f"Machine is standing guard and waiting to detect Git's confirmation message, tovarisch! 🪆🐻"
    )
    return send_telegram_message(msg)

def send_git_push_confirmed_notification(branch_name: str) -> bool:
    """Notify user that git push confirmation was detected and pipeline resumed."""
    msg = f"✅ *Spasibo, Comrade Yoan!* Git push confirmed for `{branch_name}`! Resuming pipeline! 🚀☭"
    return send_telegram_message(msg)

def wait_for_new_task_or_alert(interval_seconds=60, stop_event=None, git_hwnd: int = None, reproduction_handler=None, review_handler=None, reapk_handler=None):
    """
    Called when re, pending, and working folders are empty.
    - Polls for user Telegram commands ('reproduction', 'review', 'reapk', 'takeabreak', 'godark') every second.
    - If 'takeabreak' is received: raises KeyboardInterrupt for safe exit.
    - If 'reproduction' is received: executes reproduction routine in Git PuTTY and resets the 2-minute timer.
    - If 'review' is received: executes review routine in Git PuTTY and resets the 2-minute timer.
    - If 'reapk' is received: builds and copies APK of main branch in Git PuTTY and resets the 2-minute timer.
    - If 'godark' is received: silences periodic reminders until a new task in pending or re starts.
    - Resumes immediately if new task files appear.
    """
    global _is_dark
    re_files = list(config.RE_DIR.glob("*.txt"))
    pending_files = list(config.PENDING_DIR.glob("*.txt"))
    working_files = list(config.WORKING_DIR.glob("*.txt"))
    
    if re_files or pending_files or working_files:
        _is_dark = False
        return True

    initial_merged_state = config.get_merged_snapshot()

    if initial_merged_state:
        print(f"\n[QUEUE] Tasks in re/pending are empty. File(s) in merged/ already checked once; waiting in bunker for new tasks...")
        if not _is_dark:
            send_telegram_message("📢 *Comrade Yoan!* Tasks in `re`, `pending`, and `working` are completed!\nWaiting for new tasks...")
        else:
            print("[QUEUE] 🤫 Dark mode is active: suppressing initial empty queue Telegram alert.")
    else:
        print(f"\n[QUEUE EMPTY] All task folders (merged, re, pending, working) are empty! Will alert via Telegram every {interval_seconds}s until new task appears.")
        if not _is_dark:
            send_telegram_message("📢 *Comrade Yoan!* All tasks in `merged`, `re`, `pending`, and `working` folders are completed!\nWaiting for new tasks...")
        else:
            print("[QUEUE] 🤫 Dark mode is active: suppressing initial empty queue Telegram alert.")
    
    elapsed = 0
    while True:
        if stop_event and stop_event.is_set():
            return False

        # --- Check for Telegram commands from user ---
        cmds = check_telegram_commands()
        for cmd, args in cmds:
            if cmd in ["takeabreak", "break", "stop", "exit"]:
                print(f"\n[TELEGRAM] 🛑 Command '{cmd}' received! Shutting down pipeline cleanly...")
                send_telegram_message("🛑 *Comrade Yoan! Order 'takeabreak' received!*\nExiting automation pipeline cleanly. Samovar is cooling down, tovarisch! 🪆🐻")
                raise KeyboardInterrupt("Telegram command 'takeabreak' received.")

            elif cmd in ["godark", "go_dark", "dark"]:
                _is_dark = True
                print(f"\n[TELEGRAM] 🤫 Command '{cmd}' received! Going dark - periodic idle reminders silenced...")
                send_telegram_message("🤫 *Orders received! Going dark, Comrade Yoan!* Periodic reminders silenced. Process remains on watch in the bunker and will resume normal communications when a new job starts in `pending` or `re`! 🥷🪆")

            elif cmd in ["golight", "go_light", "light", "loud", "nodark"]:
                _is_dark = False
                print(f"\n[TELEGRAM] 📢 Command '{cmd}' received! Resuming periodic reminders...")
                send_telegram_message("📢 *Orders received! Normal communication restored, Comrade Yoan!* Reminders enabled. 🪆☭")

            elif cmd == "reproduction":
                # 1. RESET TIMER IMMEDIATELY ON RECEIPT
                elapsed = 0
                print(f"\n[TELEGRAM] 🔄 Command 'reproduction' received! Executing reproduction in Git PuTTY ({config.DEV_REPO_DIR})...")
                send_telegram_message(f"🔄 *Orders received!* Executing reproduction command in Git PuTTY ({config.DEV_REPO_DIR}):\n`git fetch origin main && git -C ../haskovo.net pull origin main`")
                if git_hwnd and reproduction_handler:
                    success = reproduction_handler(git_hwnd, repo_dir=config.DEV_REPO_DIR)
                    if success:
                        send_telegram_message("✅ *Spasibo, Comrade Yoan!* Reproduction command executed successfully! 🚀☭")
                    else:
                        send_telegram_message("⚠️ *Vnimaniye!* Reproduction command failed or timed out. Please check Git PuTTY!")
                else:
                    print("[TELEGRAM] ⚠️ No Git PuTTY window handle or handler provided for reproduction.")
                print(f"✅ Reproduction finished.")

            elif cmd == "review":
                elapsed = 0
                print(f"\n[TELEGRAM] 📱 Command 'review' received with args: {args}")
                if review_handler:
                    review_handler(git_hwnd, args)
                else:
                    print("[TELEGRAM] ⚠️ No review handler provided.")

            elif cmd in ["reapk", "apk"]:
                elapsed = 0
                print(f"\n[TELEGRAM] 📱 Command '{cmd}' received with args: {args}")
                send_telegram_message("📱 *Orders received!* Building debug APK from `main` branch of `exo-android` in Git PuTTY... ⏳")
                if reapk_handler:
                    reapk_handler(git_hwnd, args)
                else:
                    print("[TELEGRAM] ⚠️ No reapk handler provided.")

        # Check if new files appeared in re, pending, working, or a NEW / updated file in merged
        curr_re = list(config.RE_DIR.glob("*.txt"))
        curr_pending = list(config.PENDING_DIR.glob("*.txt"))
        curr_working = list(config.WORKING_DIR.glob("*.txt"))
        curr_merged_state = config.get_merged_snapshot()
        new_merged = [
            name for name, mtime in curr_merged_state.items()
            if name not in initial_merged_state or mtime > initial_merged_state[name]
        ]
        initial_merged_state = {k: v for k, v in initial_merged_state.items() if k in curr_merged_state}

        if curr_re or curr_pending or curr_working or new_merged:
            if curr_re:
                found_name = f"re/{curr_re[0].name}"
            elif curr_pending:
                found_name = f"pending/{curr_pending[0].name}"
            elif curr_working:
                found_name = f"working/{curr_working[0].name}"
            else:
                found_name = f"merged/{new_merged[0]}"
            print(f"[QUEUE] Detected task in queue: '{found_name}'! Resuming work...")
            send_telegram_message(f"🚀 *New task detected:* `{found_name}`. Resuming automation!")
            if curr_re or curr_pending or curr_working:
                _is_dark = False
            return True
            
        time.sleep(1)
        elapsed += 1
        
        if elapsed >= interval_seconds:
            if not _is_dark:
                if initial_merged_state:
                    send_telegram_message("⏳ *Reminder:* Active task queues are empty. Waiting for new tasks or new files in `merged`...")
                else:
                    send_telegram_message("⏳ *Reminder:* All task folders (`merged`, `re`, `pending`) are still empty. Machine is resting in the bunker.")
            elapsed = 0

if __name__ == "__main__":
    print("Testing Telegram connection...")
    success = send_telegram_message("🇷🇺 *Privet from Make-Another-Me-inator!* Automation bot is online and ready!")
    if success:
        print("Telegram test passed!")
    else:
        print("Telegram test failed. Please verify bot token and chat ID.")