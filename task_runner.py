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
    branch_name = task_info["branch_name"]
    commit_msg = task_info["commit_message"].replace('"', '\\"') # escape quotes for bash
    prompt = task_info["prompt"]
    
    print(f"  -> Worktree Dir: {worktree_dir}")
    print(f"  -> Branch Name:   {branch_name}")
    print(f"  -> Commit Msg:    {commit_msg}")
    print(f"  -> Prompt length: {len(prompt)} chars")

    # Step 2: In Git PuTTY, execute worktree command
    print("\n[GIT PUITY] 1. Creating worktree...")
    putty.paste_text(git_hwnd, raw_git_cmd, press_enter=True)
    time.sleep(3.0)
    
    print(f"[GIT PUITY] 2. Navigating into worktree: cd {worktree_dir}")
    putty.paste_text(git_hwnd, f"cd {worktree_dir}", press_enter=True)
    time.sleep(1.5)

    # Step 3: In Claude PuTTY, switch directory and navigate trust prompt
    print("\n[CLAUDE PUITY] 3. Clearing previous context (/clear)...")
    putty.paste_text(claude_hwnd, "/clear", press_enter=True)
    time.sleep(2.0)
    
    print(f"[CLAUDE PUITY] 4. Changing Claude directory: /cd {worktree_dir}")
    watcher.mark_start()
    putty.paste_text(claude_hwnd, f"/cd {worktree_dir}", press_enter=True)
    time.sleep(1.5)
    
    # Check if trust prompt appears
    print("[CLAUDE PUITY] Checking for directory trust prompt...")
    has_trust_prompt = watcher.wait_for_trust_prompt(timeout=5)
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
    print("\n[CLAUDE PUITY] 5. Sending task prompt to Claude...")
    watcher.mark_start()
    putty.paste_text(claude_hwnd, prompt, press_enter=True)
    
    # Step 5: Wait for Claude completion
    print("[CLAUDE PUITY] Waiting for Claude to finish working on task...")
    completed = watcher.wait_for_claude_completion(idle_seconds=7, max_timeout=2400)
    if not completed:
        print("[WARNING] Claude did not finish cleanly or reached timeout! Proceeding with git check...")

    # Step 6: Claude leave directory before cleanup
    print("\n[CLAUDE PUITY] Releasing worktree folder: /cd /data/testapp")
    putty.paste_text(claude_hwnd, f"/cd {config.SERVER_REPO_DIR}", press_enter=True)
    time.sleep(1.5)

    # Step 7: In Git PuTTY, commit and push changes
    print("\n[GIT PUITY] 6. Checking git status and committing changes...")
    git_script = f"""if [ -n "$(git status --porcelain)" ]; then
  git add .
  git commit -m "{commit_msg}"
  git push -u origin {branch_name} -o merge_request.create -o merge_request.target=main
fi"""
    putty.paste_text(git_hwnd, git_script, press_enter=True)
    
    # Handle credentials prompt
    print("[GIT PUITY] Waiting for credentials prompt (Username/Password)...")
    time.sleep(3.0)
    if config.GIT_USERNAME:
        print(f"[GIT PUITY] Sending Git Username...")
        putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
        time.sleep(2.0)
    if config.GIT_PASSWORD:
        print(f"[GIT PUITY] Sending Git Password...")
        putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
        time.sleep(4.0)
        
    # Step 8: Return to base server repo and delete worktree
    print(f"\n[GIT PUITY] 7. Returning to {config.SERVER_REPO_DIR} and removing worktree...")
    putty.paste_text(git_hwnd, f"cd {config.SERVER_REPO_DIR}", press_enter=True)
    time.sleep(1.5)
    putty.paste_text(git_hwnd, f"git worktree remove --force {worktree_dir}", press_enter=True)
    time.sleep(2.0)

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
    - When pending is empty, engages Telegram alert loop every 2 minutes
    - Automatically resumes when new tasks appear in pending/
    """
    while True:
        pending_files = sorted(list(config.PENDING_DIR.glob("*.txt")))
        
        if pending_files:
            task_file = pending_files[0]
            try:
                process_single_task(task_file, git_hwnd, claude_hwnd, watcher)
            except Exception as e:
                print(f"[ERROR] Exception during task execution: {e}")
                # Don't spin uncontrollably on error
                time.sleep(3)
        else:
            # Pending queue is empty!
            has_new = telegram_alert.wait_for_new_task_or_alert(interval_seconds=120)
            if not has_new:
                break

if __name__ == "__main__":
    print("Testing task runner setup...")
    print(f"Pending tasks: {len(list(config.PENDING_DIR.glob('*.txt')))}")
