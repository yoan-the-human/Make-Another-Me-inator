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

def parse_task_file(file_path: Path, is_new_branch: bool | None = None):
    """
    Parse a task text file.
    Line 1: Git branch command or branch name (e.g. "new/catalog-videos" or new/catalog-videos or git checkout -b ...)
    Line 2: Commit message
    Line 3+: Prompt for Claude
    """
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [line.rstrip("\r\n") for line in f]
        
    if len(lines) < 2:
        raise ValueError(f"Task file {file_path.name} is too short (needs at least 2 lines)!")
        
    line1 = lines[0].strip().strip('"\'')
    line2 = lines[1].strip()
    prompt = "\n".join(lines[2:]).strip() if len(lines) > 2 else ""
    
    # Determine if this creates a new branch or switches to an existing branch
    if is_new_branch is not None:
        is_new = is_new_branch
    else:
        if bool(re.search(r'(?:^|\s)(?:-b|-c|--create)\s+', line1)):
            is_new = True
        elif "pending" in file_path.parent.name.lower():
            is_new = True
        elif "re" in file_path.parent.name.lower():
            is_new = False
        else:
            # If in working folder or root, check if line1 starts with new/ or feat/
            is_new = any(line1.lower().startswith(p) for p in ["new/", "feat/", "feature/"])

    # Extract branch name
    branch_match = re.search(r'(?:-b|-c|--create)\s+([^\s]+)', line1)
    if branch_match:
        branch_name = branch_match.group(1).strip()
    elif "git worktree add" in line1:
        # Legacy worktree format without -b: git worktree add <dir> <branch>
        m = re.search(r'git\s+worktree\s+add\s+[^\s]+\s+([^\s]+)', line1)
        if m:
            branch_name = m.group(1).strip()
        else:
            m2 = re.search(r'git\s+worktree\s+add\s+([^\s]+)', line1)
            branch_name = Path(m2.group(1)).name.replace("task-", "") if m2 else file_path.stem
    elif line1.startswith("git checkout") or line1.startswith("git switch"):
        # e.g. git checkout <branch> or git switch <branch>
        tokens = line1.split()
        branch_tokens = [t for t in tokens[2:] if not t.startswith("-")]
        branch_name = branch_tokens[0] if branch_tokens else file_path.stem
    else:
        # Bare line or unrecognized prefix, e.g. "new/catalog-videos-something"
        tokens = line1.split()
        branch_name = tokens[0] if tokens else file_path.stem

    # Strip punctuation/quotes
    branch_name = branch_name.strip("'\";&")

    return {
        "raw_git_cmd": line1,
        "branch_name": branch_name,
        "is_new_branch": is_new,
        "commit_message": line2,
        "prompt": prompt
    }

def handle_git_push(git_hwnd: int, branch_name: str, commit_msg: str, repo_dir: str = config.SERVER_REPO_DIR) -> bool:
    """
    Execute git add, commit, and push with Merge Request creation directly in repo_dir (/data/development).
    - Dynamically monitors for credentials prompts (Username/Password).
    - If authentication fails or push fails: halts pipeline, alerts via Telegram,
      and waits until user provides credentials/pushes and Git confirmation is detected!
    """
    push_uid = f"{int(time.time())}_{os.getpid()}"
    push_ok_marker = f"==PUSH_OK_{push_uid}=="
    push_fail_marker = f"==PUSH_FAIL_{push_uid}=="

    git_script = (
        f"cd {repo_dir} && "
        f"if [ -n \"$(git status --porcelain)\" ]; then git add . && git commit -m \"{commit_msg}\" ; fi ; "
        f"git push -u origin {branch_name} -o merge_request.create -o merge_request.target=main ; "
        f"PUSH_RC=$? ; "
        f"if [ $PUSH_RC -eq 0 ]; then echo '{push_ok_marker}' ; else echo '{push_fail_marker}' ; fi"
    )

    print(f"\n[GIT PUITY] 4. Checking git status, committing, and pushing branch '{branch_name}' in {repo_dir}...")
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

        bottom_lines = recent_lines[-5:] if len(recent_lines) >= 5 else recent_lines
        bottom_text = "\n".join(bottom_lines).lower()

        # 2. Handle Username prompt (Check before error checks!)
        if not user_sent and any("username for" in l.lower() for l in bottom_lines):
            print("[GIT PUITY] Detected Username prompt! Entering username...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            user_sent = True
            time.sleep(1.0)
            continue

        # 3. Handle Password prompt (Check before error checks!)
        elif not pass_sent and any("password for" in l.lower() for l in bottom_lines):
            print("[GIT PUITY] Detected Password prompt! Entering password...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
            pass_sent = True
            time.sleep(2.0)
            continue

        # 4. Check for failure marker or authentication errors ONLY after password has been submitted!
        has_fail_marker = any(
            (l == push_fail_marker or l == f'"{push_fail_marker}"' or l == f"'{push_fail_marker}'")
            for l in recent_lines
            if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
        )
        has_auth_err = pass_sent and any(err in bottom_text for err in [
            "authentication failed",
            "access denied",
            "invalid username or password",
            "fatal: authentication"
        ])

        if has_fail_marker or has_auth_err:
            print("\n[GIT PUITY] 🚨 Authentication or git push failure detected!")
            auth_failed = True
            break
            time.sleep(2.0)

        time.sleep(1.0)

    # If push timed out without confirmation, treat as failure
    if not auth_failed:
        print("[GIT PUITY] ⚠️ Push did not confirm within 180s timeout! Halting for verification.")
        auth_failed = True

    # --- HALT AND TELEGRAM ALERT ---
    print("\n" + "=" * 65)
    print("🚨 [PIPELINE HALTED] GIT PUSH FAILED OR REQUIRES MANUAL INTERACTION!")
    print(f"Branch:     {branch_name}")
    print(f"Repository: {repo_dir}")
    print("Please open Git PuTTY, enter credentials or execute push manually.")
    print("Script is waiting for Git confirmation message...")
    print("=" * 65 + "\n")

    telegram_alert.send_git_auth_failed_alert(branch_name, repo_dir)

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

def handle_merged_branch_cleanup(git_hwnd: int, task_file: Path) -> bool:
    """
    Process a file in tasks/merged/:
    1. Read Line 1 to extract branch_name.
    2. In Git PuTTY, check if /data/development is currently on that branch.
    3. If yes: do nothing, leave file in tasks/merged/.
    4. If not: execute 'git branch -d {branch_name}', archive file to tasks/archive/.
    """
    task_name = task_file.name
    try:
        task_info = parse_task_file(task_file, is_new_branch=False)
        branch_name = task_info["branch_name"]
    except Exception as e:
        print(f"[MERGED QUEUE] ⚠️ Failed to parse branch from '{task_name}': {e}")
        return False

    uid = f"{int(time.time() * 1000)}_{os.getpid()}"
    active_marker = f"==BRANCH_ACTIVE_{uid}=="
    deleted_marker = f"==BRANCH_DELETED_{uid}=="

    check_cmd = (
        f"cd {config.SERVER_REPO_DIR} && "
        f"CUR_B=$(git branch --show-current) ; "
        f"if [ \"$CUR_B\" = \"{branch_name}\" ]; then "
        f"echo '{active_marker}' ; "
        f"else "
        f"git branch -d \"{branch_name}\" ; "
        f"echo '{deleted_marker}' ; "
        f"fi"
    )

    print(f"\n[MERGED QUEUE] 🧹 Checking branch '{branch_name}' from '{task_name}' in {config.SERVER_REPO_DIR}...")
    putty.paste_text(git_hwnd, check_cmd, press_enter=True)

    start_time = time.time()
    while time.time() - start_time < 30:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]

        is_active = any(
            (l == active_marker or l == f"'{active_marker}'" or l == f'"{active_marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and " && echo " not in l and not l.startswith("root@")
        )
        if is_active:
            print(f"[MERGED QUEUE] ⏸️ Development is currently on branch '{branch_name}'! Doing nothing; file remains in merged/.\n")
            return False

        is_deleted = any(
            (l == deleted_marker or l == f"'{deleted_marker}'" or l == f'"{deleted_marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and " && echo " not in l and not l.startswith("root@")
        )
        if is_deleted:
            print(f"[MERGED QUEUE] ✅ Branch '{branch_name}' deleted (`git branch -d`)!")
            archive_target = config.ARCHIVE_DIR / task_name
            if archive_target.exists():
                archive_target.unlink()
            shutil.move(str(task_file), str(archive_target))
            print(f"[MERGED QUEUE] 📦 Archived '{task_name}' to {archive_target}\n")
            return True

        time.sleep(1.0)

    print(f"[MERGED QUEUE] ⚠️ Timed out waiting for check/delete marker for '{branch_name}'.\n")
    return False

def process_single_task(task_file: Path, git_hwnd: int, claude_hwnd: int, watcher: LogWatcher) -> bool:
    """
    Execute full workflow for a single task directly in /data/development:
    1. Move pending/re -> working
    2. Git branch switch/creation (new branch off origin/main, or existing branch)
    3. Claude /clear, paste prompt, wait completion
    4. Git commit & push with credentials handling
    5. Stay on branch (no cleanup, no switch to main)
    6. Move working -> completed
    7. Usage check & /clear
    """
    task_name = task_file.name
    origin_parent = task_file.parent.name.lower()
    is_from_pending = (origin_parent == "pending")

    print(f"\n=======================================================")
    print(f"🚀 STARTING TASK: {task_name}")
    print(f"=======================================================")
    
    # Step 1: Move to working folder
    working_file = config.WORKING_DIR / task_name
    shutil.move(str(task_file), str(working_file))
    print(f"[TASK] Moved to {working_file}")
    
    # Parse file
    try:
        task_info = parse_task_file(working_file, is_new_branch=is_from_pending)
    except Exception as e:
        print(f"[ERROR] Failed to parse task file: {e}")
        return False
        
    branch_name = task_info["branch_name"]
    is_new = task_info["is_new_branch"]
    commit_msg = task_info["commit_message"].replace('"', '\\"') # escape quotes for bash
    prompt = task_info["prompt"]
    
    print(f"  -> Repository:    {config.SERVER_REPO_DIR}")
    print(f"  -> Branch:        {branch_name} ({'NEW branch off origin/main' if is_new else 'EXISTING branch'})")
    print(f"  -> Commit Msg:    {commit_msg}")
    print(f"  -> Prompt length: {len(prompt)} chars")

    # Step 2: In Git PuTTY, switch or create branch in /data/development
    marker = f"==BRANCH_READY_{int(time.time() * 1000)}=="
    if is_new:
        print(f"\n[GIT PUITY] 1. Fetching origin main and checking out new branch '{branch_name}' in {config.SERVER_REPO_DIR}...")
        git_cmd = (
            f"cd {config.SERVER_REPO_DIR} && "
            f"git fetch origin main && "
            f"git checkout -B {branch_name} origin/main && "
            f"echo '{marker}'"
        )
    else:
        print(f"\n[GIT PUITY] 1. Switching to existing branch '{branch_name}' in {config.SERVER_REPO_DIR}...")
        git_cmd = (
            f"cd {config.SERVER_REPO_DIR} && "
            f"git checkout {branch_name} && "
            f"echo '{marker}'"
        )

    putty.paste_text(git_hwnd, git_cmd, press_enter=True)
    ready = putty.wait_for_git_branch(git_hwnd, branch_name, marker=marker, timeout=120)
    if not ready:
        print(f"\n[GIT PUITY] 🚨 Branch setup/fetch failed or halted for '{branch_name}'!")
        telegram_alert.send_git_auth_failed_alert(branch_name, config.SERVER_REPO_DIR)
        print(f"Please switch to Git PuTTY, provide credentials or checkout branch '{branch_name}' manually.")
        print("Script is waiting for confirmation that branch is checked out...")

        last_rem = time.time()
        while True:
            screen = putty.capture_screen_text(git_hwnd)
            lines = [l.strip() for l in screen.splitlines() if l.strip()]
            recent_text = "\n".join(lines[-25:])

            branch_ok = (
                f"Switched to branch '{branch_name}'" in recent_text or
                f"Switched to a new branch '{branch_name}'" in recent_text or
                f"On branch {branch_name}" in recent_text or
                any(
                    (l == marker or l == f"'{marker}'" or l == f'"{marker}"')
                    for l in lines[-25:]
                    if not l.startswith("echo ") and " && echo " not in l and not l.startswith("root@")
                )
            )
            if branch_ok:
                print(f"[GIT PUITY] ✅ Branch '{branch_name}' confirmed active! Resuming pipeline...")
                telegram_alert.send_git_push_confirmed_notification(branch_name)
                time.sleep(1.0)
                break

            if time.time() - last_rem >= 120:
                print(f"[GIT PUITY] ⏳ Still waiting for branch setup confirmation for '{branch_name}'...")
                telegram_alert.send_telegram_message(
                    f"⏳ *Reminder:* Pipeline is waiting for branch `{branch_name}` to be checked out in Git PuTTY!"
                )
                last_rem = time.time()

            time.sleep(2.0)

    # Step 3: In Claude PuTTY, clear previous context
    # Claude is permanently in /data/development - no /cd hopping, no trust dialogs!
    print("\n[CLAUDE PUITY] 2. Clearing previous context (/clear)...")
    putty.paste_text(claude_hwnd, "/clear", press_enter=True)
    time.sleep(1.5)

    # Step 4: In Claude PuTTY, send the prompt
    print("\n[CLAUDE PUITY] 3. Sending task prompt to Claude...")
    watcher.mark_start()
    putty.paste_text(claude_hwnd, prompt, press_enter=True)
    
    # Step 5: Wait for Claude completion
    print("[CLAUDE PUITY] Waiting for Claude to finish working on task...")
    completed = watcher.wait_for_claude_completion(claude_hwnd=claude_hwnd, idle_seconds=8, max_timeout=2400)
    if not completed:
        print("[WARNING] Claude did not finish cleanly or reached timeout! Proceeding with git check...")

    # Step 6: In Git PuTTY, commit and push changes directly from /data/development
    handle_git_push(git_hwnd, branch_name, commit_msg, config.SERVER_REPO_DIR)
        
    # Note: Stay on branch! No worktree removal and no git checkout main.
    print(f"\n[GIT PUITY] ✅ Staying on branch '{branch_name}' in {config.SERVER_REPO_DIR} (no cleanup or switch needed).")

    # Step 7: Move file to completed folder
    completed_file = config.COMPLETED_DIR / task_name
    shutil.move(str(working_file), str(completed_file))
    print(f"\n[SUCCESS] ✅ Task '{task_name}' completed and archived to {completed_file}!\n")

    # Step 8: Check Claude session usage
    usage_pct, reset_time, is_over_limit = putty.check_claude_usage(claude_hwnd, threshold=config.USAGE_THRESHOLD)

    # Step 9: Clear Claude's context after task completion
    print("[CLAUDE PUITY] 5. Sending /clear to wipe memory for next assignment...")
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
    - Priority 0: Checks merged/ folder first to delete merged branches!
    - High priority: Processes all tasks in re/ folder first!
    - Second priority: Processes tasks in pending/ folder only once re/ is empty.
    - Recovers orphaned tasks in working/ back to their appropriate folder (re/ or pending/).
    - ONLY engages Telegram alert when all task folders are completely empty!
    - Automatically resumes when new tasks appear.
    """
    while True:
        # Priority 0: Clean up merged branches first!
        merged_files = sorted(list(config.MERGED_DIR.glob("*.txt")))
        if merged_files:
            for m_file in merged_files:
                handle_merged_branch_cleanup(git_hwnd, m_file)
                time.sleep(0.5)

        re_files = sorted(list(config.RE_DIR.glob("*.txt")))
        pending_files = sorted(list(config.PENDING_DIR.glob("*.txt")))
        working_files = sorted(list(config.WORKING_DIR.glob("*.txt")))
        
        # If there are orphaned tasks in working/ (e.g. from crash or previous run), recover them
        if working_files and not re_files and not pending_files:
            for orphan in working_files:
                is_re = False
                try:
                    task_info = parse_task_file(orphan)
                    if not task_info["is_new_branch"]:
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
