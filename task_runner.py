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
import telegram_alert

def parse_task_file(file_path: Path):
    """
    Parse a task text file.
    Line 1: Git worktree command e.g. git worktree add ../tasks/task-XYZ -b feature/XYZ
    Line 2: Commit message
    Line 3+: Prompt for Claude
    """
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.rstrip("\r\n") for line in f]
        
    if len(lines) < 2:
        raise ValueError(f"Task file {file_path.name} is too short (needs at least 2 lines)!")
        
    line1 = lines[0].strip()
    line2 = lines[1].strip()
    prompt = "\n".join(lines[2:]).strip() if len(lines) > 2 else ""
    
    # Extract worktree directory
    # matches: git worktree add <dir> -b <branch>
    worktree_match = re.search(r'git\s+worktree\s+add\s+([^\s]+)', line1)
    if not worktree_match:
        raise ValueError(f"Could not extract worktree directory from Line 1: '{line1}'")
    worktree_dir = worktree_match.group(1).strip()
    
    # Extract branch name
    branch_match = re.search(r'-b\s+([^\s]+)', line1)
    if branch_match:
        branch_name = branch_match.group(1).strip()
    else:
        # Fallback if no -b flag
        branch_name = Path(worktree_dir).name
        
    return {
        "raw_git_cmd": line1,
        "worktree_dir": worktree_dir,
        "branch_name": branch_name,
        "commit_message": line2,
        "prompt": prompt
    }

def resolve_server_worktree_path(worktree_dir: str) -> str:
    """Resolve relative worktree path (e.g. '../tasks/task-XYZ') to absolute '/data/tasks/task-XYZ'."""
    if worktree_dir.startswith("/"):
        return worktree_dir
    clean = worktree_dir.replace("../", "").lstrip("/")
    return f"/data/{clean}"

def process_single_task(task_file: Path, git_hwnd: int, claude_hwnd: int, watcher: LogWatcher) -> bool:
    """
    Execute full workflow for a single task:
    1. Move pending -> working
    2. Git worktree create & cd
    3. Claude /clear, /cd, trust prompt navigation, paste prompt, wait completion
    4. Git commit, push with credentials, worktree cleanup
    5. Move working -> completed
    """
    task_name = task_file.name
    print(f"\n=======================================================")
    print(f"🚀 STARTING TASK: {task_name}")
    print(f"=======================================================")
    
    # Step 1: Move to working folder
    working_file = config.WORKING_DIR / task_name
    shutil.move(str(task_file), str(working_file))
    print(f"[TASK] Moved to {working_file}")
    
    # Parse file
    try:
        task_info = parse_task_file(working_file)
    except Exception as e:
        print(f"[ERROR] Failed to parse task file: {e}")
        return False
        
    raw_git_cmd = task_info["raw_git_cmd"]
    worktree_dir = task_info["worktree_dir"]
    abs_worktree_dir = resolve_server_worktree_path(worktree_dir)
    branch_name = task_info["branch_name"]
    commit_msg = task_info["commit_message"].replace('"', '\\"') # escape quotes for bash
    prompt = task_info["prompt"]
    
    print(f"  -> Worktree Dir: {worktree_dir} (Absolute: {abs_worktree_dir})")
    print(f"  -> Branch Name:   {branch_name}")
    print(f"  -> Commit Msg:    {commit_msg}")
    print(f"  -> Prompt length: {len(prompt)} chars")

    # Step 2: In Git PuTTY, execute worktree command and wait until all files are checked out!
    print("\n[GIT PUITY] 1. Creating worktree and checking out files...")
    marker = "==WORKTREE_READY_100=="
    git_full_cmd = f"{raw_git_cmd} && cd {worktree_dir} && echo '{marker}'"
    putty.paste_text(git_hwnd, git_full_cmd, press_enter=True)
    
    print(f"[GIT PUITY] Checking out files on server (waiting up to 180s for 100% completion)...")
    ready = putty.wait_for_git_worktree(git_hwnd, worktree_dir, timeout=180)
    if not ready:
        print("[WARNING] Worktree checkout did not confirm via marker. Waiting extra 5s...")
        time.sleep(5.0)

    # Step 3: In Claude PuTTY, switch directory and navigate trust prompt
    print("\n[CLAUDE PUITY] 2. Clearing previous context (/clear)...")
    putty.paste_text(claude_hwnd, "/clear", press_enter=True)
    time.sleep(2.0)
    
    print(f"[CLAUDE PUITY] 3. Changing Claude directory: /cd {abs_worktree_dir}")
    watcher.mark_start()
    putty.paste_text(claude_hwnd, f"/cd {abs_worktree_dir}", press_enter=True)
    time.sleep(1.5)
    
    # Check if trust prompt appears
    print("[CLAUDE PUITY] Checking for directory trust prompt...")
    has_trust_prompt = watcher.wait_for_trust_prompt(timeout=6, claude_hwnd=claude_hwnd)
    if has_trust_prompt:
        print("[CLAUDE PUITY] Detected directory trust prompt! Selecting 'Yes, move here' (Down Arrow + Enter)...")
        time.sleep(0.5)
        putty.send_down_arrow(claude_hwnd)
        time.sleep(0.3)
        putty.send_enter(claude_hwnd)
        time.sleep(2.0)
    else:
        print("[CLAUDE PUITY] No trust prompt detected (or directory already trusted).")

    # Step 4: In Claude PuTTY, send the prompt
    print("\n[CLAUDE PUITY] 4. Sending task prompt to Claude...")
    watcher.mark_start()
    putty.paste_text(claude_hwnd, prompt, press_enter=True)
    
    # Step 5: Wait for Claude completion
    print("[CLAUDE PUITY] Waiting for Claude to finish working on task...")
    completed = watcher.wait_for_claude_completion(claude_hwnd=claude_hwnd, idle_seconds=8, max_timeout=2400)
    if not completed:
        print("[WARNING] Claude did not finish cleanly or reached timeout! Proceeding with git check...")

    # Step 6: Claude leave directory before cleanup
    print("\n[CLAUDE PUITY] Releasing worktree folder: /cd /data/testapp")
    putty.paste_text(claude_hwnd, f"/cd {config.SERVER_REPO_DIR}", press_enter=True)
    time.sleep(1.5)

    # Step 7: In Git PuTTY, commit and push changes with dynamic credential monitoring
    print("\n[GIT PUITY] 5. Checking git status and committing changes...")
    push_marker = "==PUSH_COMPLETE_OK=="
    git_script = f"""if [ -n "$(git status --porcelain)" ]; then
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
    while time.time() - start_push < 180:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        has_push_marker = any((l == push_marker or l == f'"{push_marker}"' or l == f"'{push_marker}'") for l in lines if not l.startswith("echo") and not l.startswith("root@"))
        if has_push_marker:
            print("[GIT PUITY] ✅ Push and commit completed successfully!")
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
        
    # Step 8: Return to base server repo, remove worktree, and delete local branch
    print(f"\n[GIT PUITY] 6. Returning to {config.SERVER_REPO_DIR}, removing worktree and deleting local branch...")
    cleanup_marker = "==CLEANUP_DONE=="
    cleanup_cmd = f"cd {config.SERVER_REPO_DIR} && git worktree remove --force {worktree_dir} && git branch -D {branch_name} ; echo '{cleanup_marker}'"
    putty.paste_text(git_hwnd, cleanup_cmd, press_enter=True)
    putty.wait_for_screen_text(git_hwnd, [cleanup_marker], timeout=30)
    time.sleep(1.0)

    # Step 9: Move file to completed folder
    completed_file = config.COMPLETED_DIR / task_name
    shutil.move(str(working_file), str(completed_file))
    print(f"\n[SUCCESS] ✅ Task '{task_name}' completed and archived to {completed_file}!\n")

    # Step 10: Clear Claude's context after task completion
    print("[CLAUDE PUITY] 8. Sending /clear to wipe memory for next assignment...")
    putty.paste_text(claude_hwnd, "/clear", press_enter=True)
    time.sleep(1.5)

    return True

def run_task_loop(git_hwnd: int, claude_hwnd: int, watcher: LogWatcher):
    """
    Continuous worker loop:
    - Processes all tasks in pending/
    - If a task is in working/ (e.g. from previous run), recovers it to pending/
    - ONLY engages Telegram alert when BOTH pending/ and working/ are completely empty
    - Automatically resumes when new tasks appear
    """
    while True:
        pending_files = sorted(list(config.PENDING_DIR.glob("*.txt")))
        working_files = sorted(list(config.WORKING_DIR.glob("*.txt")))
        
        # If there are orphaned tasks in working/, recover them to pending/
        if working_files and not pending_files:
            print(f"[QUEUE] Found task in working folder: '{working_files[0].name}'. Recovering to pending queue...")
            task_file = working_files[0]
            target_pending = config.PENDING_DIR / task_file.name
            if target_pending.exists():
                target_pending.unlink()
            shutil.move(str(task_file), str(target_pending))
            pending_files = [target_pending]
        
        if pending_files:
            task_file = pending_files[0]
            try:
                success = process_single_task(task_file, git_hwnd, claude_hwnd, watcher)
                if not success:
                    print(f"[ERROR] Task '{task_file.name}' did not complete successfully.")
            except Exception as e:
                print(f"[ERROR] Exception during task execution: {e}")
                # If file got stuck in working folder, move it back to pending so it doesn't get lost
                working_target = config.WORKING_DIR / task_file.name
                if working_target.exists():
                    target_pending = config.PENDING_DIR / task_file.name
                    if target_pending.exists():
                        target_pending.unlink()
                    shutil.move(str(working_target), str(target_pending))
                time.sleep(3)
        else:
            # BOTH pending and working are completely empty!
            has_new = telegram_alert.wait_for_new_task_or_alert(interval_seconds=120)
            if not has_new:
                break

if __name__ == "__main__":
    print("Testing task runner setup...")
    print(f"Pending tasks: {len(list(config.PENDING_DIR.glob('*.txt')))}")
