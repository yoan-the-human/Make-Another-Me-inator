import os
import sys
import time
import shutil
import re
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
from task_runner import parse_task_file, resolve_server_worktree_path, run_task_loop
import telegram_alert

def resume_active_task():
    """
    Resume an in-flight task from right after Claude prompt submission:
    1. Locates active task in tasks/working/ (or tasks/pending/)
    2. Verifies Claude completion in Claude PuTTY
    3. Commands Claude to exit worktree (/cd /data/testapp)
    4. In Git PuTTY: git commit, git push with GitLab MR, enter credentials if prompted
    5. In Git PuTTY: git worktree remove --force <worktree>
    6. Moves task file to tasks/completed/
    7. Sends /clear to Claude PuTTY
    8. Checks if queue is empty -> Telegram notification
    """
    print("=======================================================")
    print("🇷🇺 MAKE-ANOTHER-ME-INATOR: RESUME MISSION PROTOCOL")
    print("=======================================================")
    
    # 1. Identify active task file
    working_files = sorted(list(config.WORKING_DIR.glob("*.txt")))
    pending_files = sorted(list(config.PENDING_DIR.glob("*.txt")))
    
    if working_files:
        task_file = working_files[0]
        print(f"[RESUME] Found in-flight task in working folder: '{task_file.name}'")
    elif pending_files:
        task_file = pending_files[0]
        working_target = config.WORKING_DIR / task_file.name
        shutil.move(str(task_file), str(working_target))
        task_file = working_target
        print(f"[RESUME] Moved pending task to working folder: '{task_file.name}'")
    else:
        print("[INFO] No tasks found in working/ or pending/ folders to resume!")
        return False
        
    task_name = task_file.name
    
    # 2. Parse task details
    try:
        task_info = parse_task_file(task_file)
    except Exception as e:
        print(f"[ERROR] Failed to parse task file {task_name}: {e}")
        return False
        
    worktree_dir = task_info["worktree_dir"]
    abs_worktree_dir = resolve_server_worktree_path(worktree_dir)
    branch_name = task_info["branch_name"]
    commit_msg = task_info["commit_message"].replace('"', '\\"')
    
    print(f"  -> Task File:     {task_name}")
    print(f"  -> Worktree Dir:  {worktree_dir} (Absolute: {abs_worktree_dir})")
    print(f"  -> Branch:        {branch_name}")
    print(f"  -> Commit Msg:    {commit_msg}")
    
    # 3. Connect to PuTTY windows
    git_hwnd, claude_hwnd = putty.select_putty_windows(force_prompt=False)
    watcher = LogWatcher()
    
    # 4. Wait / verify Claude completion
    print("\n[CLAUDE PUITY] Verifying Claude completion status...")
    completed = watcher.wait_for_claude_completion(claude_hwnd=claude_hwnd, idle_seconds=5, max_timeout=2400)
    if not completed:
        print("[WARNING] Claude completion was not cleanly confirmed. Proceeding with caution...")
    else:
        print("[CLAUDE PUITY] Claude execution confirmed finished!")
        
    # 5. Claude release directory
    print(f"\n[CLAUDE PUITY] Releasing worktree folder: /cd {config.SERVER_REPO_DIR}")
    putty.paste_text(claude_hwnd, f"/cd {config.SERVER_REPO_DIR}", press_enter=True)
    time.sleep(1.5)
    
    # 6. In Git PuTTY: commit and push
    print(f"\n[GIT PUITY] Checking git status and committing changes in {worktree_dir}...")
    push_marker = "==PUSH_COMPLETE_OK=="
    git_script = f"""cd {worktree_dir}
if [ -n "$(git status --porcelain)" ]; then
  git add .
  git commit -m "{commit_msg}"
  git push -u origin {branch_name} -o merge_request.create -o merge_request.target=main
fi
echo "{push_marker}" """
    putty.paste_text(git_hwnd, git_script, press_enter=True)
    
    print("[GIT PUITY] Monitoring git push for credentials prompt or completion...")
    start_push = time.time()
    user_sent = False
    pass_sent = False
    push_ok = False
    while time.time() - start_push < 180:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        has_push_marker = any((l == push_marker or l == f'"{push_marker}"' or l == f"'{push_marker}'") for l in lines if not l.startswith("echo") and not l.startswith("root@"))
        if has_push_marker:
            print("[GIT PUITY] ✅ Push and commit completed successfully!")
            push_ok = True
            break
        if not user_sent and any("Username for" in l for l in lines[-5:]):
            print("[GIT PUITY] Detected Username prompt! Entering username...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            user_sent = True
            time.sleep(1.0)
        elif not pass_sent and any("Password for" in l for l in lines[-5:]):
            print("[GIT PUITY] Detected Password prompt! Entering password...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
            pass_sent = True
            time.sleep(2.0)
        time.sleep(1.0)
        
    if not push_ok:
        print("[WARNING] Push marker did not confirm within timeout. Please inspect Git PuTTY window.")

    # 7. In Git PuTTY: cleanup worktree
    print(f"\n[GIT PUITY] Returning to {config.SERVER_REPO_DIR} and removing worktree...")
    cleanup_marker = "==CLEANUP_DONE=="
    cleanup_cmd = f"cd {config.SERVER_REPO_DIR} && git worktree remove --force {worktree_dir} && echo '{cleanup_marker}'"
    putty.paste_text(git_hwnd, cleanup_cmd, press_enter=True)
    putty.wait_for_screen_text(git_hwnd, [cleanup_marker], timeout=30)
    time.sleep(1.0)
    
    # 8. Move task to completed
    completed_file = config.COMPLETED_DIR / task_name
    shutil.move(str(task_file), str(completed_file))
    print(f"\n[SUCCESS] ✅ Task '{task_name}' successfully completed and archived to {completed_file}!")
    
    # 9. Clear Claude's memory
    print("\n[CLAUDE PUITY] Sending /clear to wipe memory for next assignment...")
    putty.paste_text(claude_hwnd, "/clear", press_enter=True)
    time.sleep(1.5)
    
    # 10. Check if queue is empty
    rem_pending = list(config.PENDING_DIR.glob("*.txt"))
    rem_working = list(config.WORKING_DIR.glob("*.txt"))
    if not rem_pending and not rem_working:
        print("\n[QUEUE EMPTY] Both pending and working folders are completely empty!")
        telegram_alert.send_telegram_message("📢 *Comrade Yoan!* All tasks in `pending` and `working` folders are completed!\nMachine is resting in the bunker.")
    else:
        print(f"\n[QUEUE] Remaining tasks in pending queue: {len(rem_pending)}")
        
    return True

if __name__ == "__main__":
    success = resume_active_task()
    if success:
        rem_pending = list(config.PENDING_DIR.glob("*.txt"))
        if rem_pending:
            ans = input(f"\nThere are {len(rem_pending)} more tasks in pending. Do you want to run main task runner loop now? (y/n): ").strip().lower()
            if ans == "y":
                git_h, claude_h = putty.select_putty_windows(force_prompt=False)
                watcher = LogWatcher()
                run_task_loop(git_h, claude_h, watcher)
        else:
            print("\nAll done! You are ready for next task.")
