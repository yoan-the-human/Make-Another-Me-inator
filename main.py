import os
import sys
import time
import argparse
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
import putty_controller as putty
from log_watcher import LogWatcher
import task_runner
import telegram_alert

BANNER = r"""
  __  __       _                  _            _   _               __  __           _             _             
 |  \/  | __ _| | _____          / \   _ __   ___ | |_| |__   ___ _ __|  \/  | ___     (_)_ __   __ _| |_ ___  _ __ 
 | |\/| |/ _` | |/ / _ \ _____   / _ \ | '_ \ / _ \| __| '_ \ / _ \ '__| |\/| |/ _ \ ___| | '_ \ / _` | __/ _ \| '__|
 | |  | | (_| |   <  __/_____| / ___ \| | | | (_) | |_| | | |  __/ |  | |  | |  __/___| | | | | (_| | || (_) | |   
 |_|  |_|\__,_|_|\_\___|      /_/   \_\_| |_|\___/ \__|_| |_|\___|_|  |_|  |_|\___|   |_|_| |_|\__,_|\__\___/|_|   
                                                                                                                      
                 >>> Automated Soviet Comrade Pipeline for Windows & PuTTY <<<
"""

def print_banner():
    print(BANNER)
    print(f"Base Directory:     {config.BASE_DIR}")
    print(f"Pending Directory:  {config.PENDING_DIR}")
    print(f"Working Directory:  {config.WORKING_DIR}")
    print(f"Completed Directory:{config.COMPLETED_DIR}")
    print(f"Git User:           {config.GIT_USERNAME}")
    print(f"Telegram Chat:      {config.TELEGRAM_CHAT_ID}")
    print(f"PuTTY Log:          {config.PUTTY_LOG_PATH}")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Make-Another-Me-inator: Local Windows PuTTY Automation")
    parser.add_argument("--reset-windows", action="store_true", help="Force re-identification of PuTTY windows")
    parser.add_argument("--dry-run", action="store_true", help="Simulate task parsing without sending keys")
    args = parser.parse_args()

    print_banner()

    # Step 1: Identify PuTTY Windows
    git_hwnd, claude_hwnd = putty.select_putty_windows(force_prompt=args.reset_windows)

    # Step 2: Initialize Log Watcher
    watcher = LogWatcher()

    if args.dry_run:
        print("\n[DRY RUN] Running in dry-run mode. Checking pending tasks...")
        pending = list(config.PENDING_DIR.glob("*.txt"))
        print(f"Found {len(pending)} pending tasks:")
        for idx, f in enumerate(pending, 1):
            info = task_runner.parse_task_file(f)
            print(f"\nTask [{idx}]: {f.name}")
            print(f"  Branch:     {info['branch_name']}")
            print(f"  Worktree:   {info['worktree_dir']}")
            print(f"  Commit Msg: {info['commit_message']}")
            print(f"  Prompt:     {info['prompt'][:80]}...")
        print("\n[DRY RUN] Finished verification without sending keystrokes!")
        return

    # Step 3: Start Execution Loop
    print("\n[SYSTEM] Starting main automation loop. Press Ctrl+C to abort at any time.\n")
    try:
        task_runner.run_task_loop(git_hwnd, claude_hwnd, watcher)
    except KeyboardInterrupt:
        print("\n\n[ABORT] KeyboardInterrupt detected! Safe stop executed. Do svidaniya, tovarisch!")
        sys.exit(0)

if __name__ == "__main__":
    main()
