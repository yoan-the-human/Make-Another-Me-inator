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
        # Check if an existing branch name argument follows worktree_dir (no -b)
        remainder = line1[worktree_match.end():].strip()
        tokens = remainder.split()
        if tokens and not tokens[0].startswith("-"):
            branch_name = tokens[0]
        else:
            # Fallback if no branch argument provided
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

def handle_git_push(git_hwnd: int, branch_name: str, commit_msg: str, worktree_dir: str) -> bool:
    """
    Execute git add, commit, and push with Merge Request creation.
    - Dynamically monitors for credentials prompts (Username/Password).
    - If authentication fails or push fails: halts pipeline, alerts via Telegram,
      and waits until user provides credentials/pushes and Git confirmation is detected!
    """
    abs_worktree_dir = resolve_server_worktree_path(worktree_dir)
    push_uid = f"{int(time.time())}_{os.getpid()}"
    push_ok_marker = f"==PUSH_OK_{push_uid}=="
    push_fail_marker = f"==PUSH_FAIL_{push_uid}=="

    git_script = (
        f"cd {abs_worktree_dir} && "
        f"if [ -n \"$(git status --porcelain)\" ]; then git add . && git commit -m \"{commit_msg}\" ; fi ; "
        f"git push -u origin {branch_name} -o merge_request.create -o merge_request.target=main ; "
        f"PUSH_RC=$? ; "
        f"if [ $PUSH_RC -eq 0 ]; then echo '{push_ok_marker}' ; else echo '{push_fail_marker}' ; fi"
    )

    print(f"\n[GIT PUITY] 5. Checking git status, committing, and pushing branch '{branch_name}'...")
    putty.paste_text(git_hwnd, git_script, press_enter=True)

    print("[GIT PUITY] Monitoring git push for credentials prompt or completion...")
    start_push = time.time()
    user_sent = False
    pass_sent = False
    auth_failed = False

    while time.time() - start_push < 180:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]
        recent_text = "\n".join(recent_lines)

        # 1. Check success marker (ONLY in recent lines!)
        has_push_ok = any(
            (l == push_ok_marker or l == f'"{push_ok_marker}"' or l == f"'{push_ok_marker}'")
            for l in recent_lines
            if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
        )
        if has_push_ok:
            print("[GIT PUITY] ✅ Push and commit completed successfully!")
            return True

        # 2. Check for failure marker or authentication errors
        has_fail_marker = any(
            (l == push_fail_marker or l == f'"{push_fail_marker}"' or l == f"'{push_fail_marker}'")
            for l in recent_lines
            if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
        )
        has_auth_err = any(err in recent_text.lower() for err in [
            "authentication failed",
            "access denied",
            "invalid username or password",
            "fatal: could not read username",
            "fatal: could not read password",
            "fatal: authentication"
        ])

        if has_fail_marker or (has_auth_err and (user_sent or pass_sent)):
            print("\n[GIT PUITY] 🚨 Authentication or git push failure detected!")
            auth_failed = True
            break

        # 3. Handle Username prompt
        if not user_sent and any("Username for" in l for l in recent_lines[-5:]):
            print("[GIT PUITY] Detected Username prompt! Entering username...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            user_sent = True
            time.sleep(1.0)
        # 4. Handle Password prompt
        elif not pass_sent and any("Password for" in l for l in recent_lines[-5:]):
            print("[GIT PUITY] Detected Password prompt! Entering password...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
            pass_sent = True
            time.sleep(2.0)

        time.sleep(1.0)

    # If push timed out without confirmation, treat as failure
    if not auth_failed:
        print("[GIT PUITY] ⚠️ Push did not confirm within 180s timeout! Halting for verification.")
        auth_failed = True

    # --- HALT AND TELEGRAM ALERT ---
    print("\n" + "=" * 65)
    print("🚨 [PIPELINE HALTED] GIT PUSH FAILED OR REQUIRES MANUAL INTERACTION!")
    print(f"Branch:   {branch_name}")
    print(f"Worktree: {abs_worktree_dir}")
    print("Please open Git PuTTY, enter credentials or execute push manually.")
    print("Script is waiting for Git confirmation message...")
    print("=" * 65 + "\n")

    telegram_alert.send_git_auth_failed_alert(branch_name, abs_worktree_dir)

    # Confirmation patterns indicating push succeeded
    confirmation_patterns = [
        f"-> {branch_name}",
        f"-> origin/{branch_name}",
        f"[new branch]      {branch_name}",
        push_ok_marker,
        "Everything up-to-date",
        "Everything up to date",
        "merge_requests",
        "View merge request"
    ]

    last_reminder_time = time.time()

    while True:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]
        bottom_text = "\n".join(recent_lines)

        confirmed = False
        for pat in confirmation_patterns:
            if pat.lower() in bottom_text.lower():
                if pat == push_ok_marker and not any(
                    (l == push_ok_marker or l == f'"{push_ok_marker}"' or l == f"'{push_ok_marker}'")
                    for l in recent_lines
                    if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
                ):
                    continue
                confirmed = True
                break

        if confirmed:
            print(f"\n[GIT PUITY] ✅ Git confirmation detected! Branch '{branch_name}' push confirmed!")
            telegram_alert.send_git_push_confirmed_notification(branch_name)
            time.sleep(1.0)
            return True

        # Periodic reminder every 120s
        if time.time() - last_reminder_time >= 120:
            print(f"[GIT PUITY] ⏳ Still waiting for git push confirmation for '{branch_name}'...")
            telegram_alert.send_telegram_message(
                f"⏳ *Reminder:* Pipeline is still halted, waiting for git push confirmation for `{branch_name}` in Git PuTTY!"
            )
            last_reminder_time = time.time()

        time.sleep(2.0)

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
    marker = f"==WORKTREE_READY_{int(time.time() * 1000)}=="
    git_full_cmd = f"cd {config.SERVER_REPO_DIR} && {raw_git_cmd} && cd {worktree_dir} && echo '{marker}'"
    putty.paste_text(git_hwnd, git_full_cmd, press_enter=True)
    
    print(f"[GIT PUITY] Checking out files on server (waiting up to 120s for 100% completion)...")
    ready = putty.wait_for_git_worktree(git_hwnd, worktree_dir, timeout=120, marker=marker)
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

    # Step 7: In Git PuTTY, commit and push changes with auth failure detection & halt
    handle_git_push(git_hwnd, branch_name, commit_msg, worktree_dir)
        
    # Step 8: Return to base server repo and remove worktree
    print(f"\n[GIT PUITY] 6. Returning to {config.SERVER_REPO_DIR} and removing worktree ({worktree_dir})...")
    cleanup_marker = f"==CLEANUP_DONE_{int(time.time() * 1000)}=="
    cleanup_cmd = f"cd {config.SERVER_REPO_DIR} && git worktree remove --force {worktree_dir} ; echo '{cleanup_marker}'"
    putty.paste_text(git_hwnd, cleanup_cmd, press_enter=True)
    putty.wait_for_screen_text(git_hwnd, [cleanup_marker], timeout=30)
    time.sleep(1.0)

    # Step 9: Move file to completed folder
    completed_file = config.COMPLETED_DIR / task_name
    shutil.move(str(working_file), str(completed_file))
    print(f"\n[SUCCESS] ✅ Task '{task_name}' completed and archived to {completed_file}!\n")

    # Step 10: Check Claude session usage
    usage_pct, reset_time, is_over_limit = putty.check_claude_usage(claude_hwnd, threshold=config.USAGE_THRESHOLD)

    # Step 11: Clear Claude's context after task completion
    print("[CLAUDE PUITY] 8. Sending /clear to wipe memory for next assignment...")
    putty.paste_text(claude_hwnd, "/clear", press_enter=True)
    time.sleep(1.5)

    if is_over_limit:
        telegram_alert.send_quota_alert(usage_pct, reset_time)
        raise QuotaLimitExceeded(usage_pct, reset_time)

    return True

class QuotaLimitExceeded(Exception):
    def __init__(self, usage_pct: float, reset_time: str = ""):
        self.usage_pct = usage_pct
        self.reset_time = reset_time
        super().__init__(f"Claude session quota reached {usage_pct}% >= {config.USAGE_THRESHOLD}% (Resets: {reset_time})")

def run_task_loop(git_hwnd: int, claude_hwnd: int, watcher: LogWatcher):
    """
    Continuous worker loop:
    - High priority: Processes all tasks in re/ folder first!
    - Second priority: Processes tasks in pending/ folder only once re/ is empty.
    - Recovers orphaned tasks in working/ back to their appropriate folder (re/ or pending/).
    - ONLY engages Telegram alert when re/, pending/, and working/ are all completely empty!
    - Automatically resumes when new tasks appear.
    """
    while True:
        re_files = sorted(list(config.RE_DIR.glob("*.txt")))
        pending_files = sorted(list(config.PENDING_DIR.glob("*.txt")))
        working_files = sorted(list(config.WORKING_DIR.glob("*.txt")))
        
        # If there are orphaned tasks in working/ (e.g. from crash or previous run), recover them
        if working_files and not re_files and not pending_files:
            for orphan in working_files:
                is_re = False
                try:
                    with open(orphan, "r", encoding="utf-8", errors="ignore") as f:
                        first_line = f.readline()
                        if "-b" not in first_line:
                            is_re = True
                except Exception:
                    pass
                target_dir = config.RE_DIR if is_re else config.PENDING_DIR
                target_file = target_dir / orphan.name
                print(f"[QUEUE] Found orphaned task in working folder: '{orphan.name}'. Recovering to {target_dir.name} queue...")
                if target_file.exists():
                    target_file.unlink()
                shutil.move(str(orphan), str(target_file))
            re_files = sorted(list(config.RE_DIR.glob("*.txt")))
            pending_files = sorted(list(config.PENDING_DIR.glob("*.txt")))
        
        task_file = None
        origin_dir = None
        if re_files:
            task_file = re_files[0]
            origin_dir = config.RE_DIR
            print(f"[QUEUE] 🔄 Selecting task from RE folder: '{task_file.name}' (Remaining in re: {len(re_files)})")
        elif pending_files:
            task_file = pending_files[0]
            origin_dir = config.PENDING_DIR
            print(f"[QUEUE] ⏳ Selecting task from PENDING folder: '{task_file.name}' (Remaining in pending: {len(pending_files)})")
            
        if task_file:
            try:
                success = process_single_task(task_file, git_hwnd, claude_hwnd, watcher)
                if not success:
                    print(f"[ERROR] Task '{task_file.name}' did not complete successfully.")
            except QuotaLimitExceeded as qe:
                print(f"\n[STOPPING] 🛑 Claude session quota is at {qe.usage_pct}% (>= {config.USAGE_THRESHOLD}%)!")
                print(f"[STOPPING] Telegram alert dispatched. Halting automation pipeline now, tovarisch!\n")
                break
            except Exception as e:
                print(f"[ERROR] Exception during task execution: {e}")
                # If file got stuck in working folder, move it back to its origin folder
                working_target = config.WORKING_DIR / task_file.name
                if working_target.exists():
                    fallback_dir = origin_dir if origin_dir else config.PENDING_DIR
                    target = fallback_dir / task_file.name
                    if target.exists():
                        target.unlink()
                    shutil.move(str(working_target), str(target))
                time.sleep(3)
        else:
            # BOTH re, pending, and working are completely empty!
            has_new = telegram_alert.wait_for_new_task_or_alert(interval_seconds=120)
            if not has_new:
                break

if __name__ == "__main__":
    print("Testing task runner setup...")
    print(f"Re-open tasks: {len(list(config.RE_DIR.glob('*.txt')))}")
    print(f"Pending tasks: {len(list(config.PENDING_DIR.glob('*.txt')))}")
