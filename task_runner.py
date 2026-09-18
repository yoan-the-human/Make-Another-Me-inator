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
    Line 1: Git branch command or branch name (e.g. "new/catalog-videos" or git checkout -b ...)
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
            is_new = any(line1.lower().startswith(p) for p in ["new/", "feat/", "feature/"])

    # Extract branch name
    branch_match = re.search(r'(?:-b|-c|--create)\s+([^\s]+)', line1)
    if branch_match:
        branch_name = branch_match.group(1).strip()
    elif "git worktree add" in line1:
        m = re.search(r'git\s+worktree\s+add\s+[^\s]+\s+([^\s]+)', line1)
        if m:
            branch_name = m.group(1).strip()
        else:
            m2 = re.search(r'git\s+worktree\s+add\s+([^\s]+)', line1)
            branch_name = Path(m2.group(1)).name.replace("task-", "") if m2 else file_path.stem
    elif line1.startswith("git checkout") or line1.startswith("git switch"):
        tokens = line1.split()
        branch_tokens = [t for t in tokens[2:] if not t.startswith("-")]
        branch_name = branch_tokens[0] if branch_tokens else file_path.stem
    else:
        tokens = line1.split()
        branch_name = tokens[0] if tokens else file_path.stem

    branch_name = branch_name.strip("'\";&")

    return {
        "raw_git_cmd": line1,
        "branch_name": branch_name,
        "is_new_branch": is_new,
        "commit_message": line2,
        "prompt": prompt
    }

def handle_git_push(git_hwnd: int, branch_name: str, commit_msg: str, repo_dir: str = config.SERVER_REPO_DIR) -> bool:
    """Execute git add, commit, and push directly in repo_dir (/data/development)."""
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
    
    putty.activate_window(git_hwnd)
    time.sleep(0.3)
    putty.paste_text(git_hwnd, git_script, press_enter=True)
    print("[GIT PUITY] Monitoring git push for credentials prompt or completion...")

    start_push = time.time()
    user_sent = False
    pass_sent = False

    while time.time() - start_push < 180:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]
        recent_text = "\n".join(recent_lines)
        last_line = lines[-1].lower() if lines else ""

        # 1. Check success marker
        has_push_ok = any(
            (l == push_ok_marker or l == f'"{push_ok_marker}"' or l == f"'{push_ok_marker}'")
            for l in recent_lines
            if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
        )
        if has_push_ok:
            print("[GIT PUITY] ✅ Push and commit completed successfully!")
            return True

        # 2. Check for fatal auth error ONLY if password was already entered
        if pass_sent:
            has_fail_marker = any(
                (l == push_fail_marker or l == f'"{push_fail_marker}"' or l == f"'{push_fail_marker}'")
                for l in recent_lines
                if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
            )
            has_auth_err = any(err in recent_text.lower() for err in [
                "fatal: authentication",
                "access denied",
                "invalid username or password"
            ])
            if has_fail_marker or has_auth_err:
                print("\n[GIT PUITY] 🚨 Authentication failed after submitting credentials!")
                break

        # 3. Handle Username prompt
        if not user_sent and any("username for" in l.lower() for l in lines[-3:]):
            print("[GIT PUITY] Detected Username prompt! Entering username...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            user_sent = True
            time.sleep(1.5)
            continue

        # 4. Handle Password prompt
        if user_sent and not pass_sent and any("password for" in l.lower() for l in lines[-3:]):
            if not last_line.endswith("#") and not last_line.endswith("$"):
                print("[GIT PUITY] Detected Password prompt! Entering password...")
                time.sleep(0.5)
                putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
                pass_sent = True
                time.sleep(2.0)
                continue

        time.sleep(1.0)

    # --- Halt and alert if not successful ---
    print("\n" + "=" * 65)
    print("🚨 [PIPELINE HALTED] GIT PUSH FAILED OR REQUIRES MANUAL INTERACTION!")
    print(f"Branch:     {branch_name}")
    print(f"Repository: {repo_dir}")
    print("Please check Git PuTTY.")
    print("=" * 65 + "\n")

    telegram_alert.send_git_auth_failed_alert(branch_name, repo_dir)

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

        if time.time() - last_reminder_time >= 120:
            print(f"[GIT PUITY] ⏳ Still waiting for git push confirmation for '{branch_name}'...")
            telegram_alert.send_telegram_message(
                f"⏳ *Reminder:* Pipeline is waiting for git push confirmation for `{branch_name}` in Git PuTTY!"
            )
            last_reminder_time = time.time()

        time.sleep(2.0)

def handle_reproduction(git_hwnd: int) -> bool:
    """
    Executes 'git fetch origin main && git -C ../haskovo.net pull origin main' in Git PuTTY.
    Dynamically listens and fills username and password prompts (which may occur multiple times).
    """
    uid = f"{int(time.time() * 1000)}_{os.getpid()}"
    ok_marker = f"==REPRO_OK_{uid}=="
    fail_marker = f"==REPRO_FAIL_{uid}=="

    repro_cmd = (
        f"cd {config.SERVER_REPO_DIR} && "
        f"git fetch origin main && git -C ../haskovo.net pull origin main ; "
        f"REPRO_RC=$? ; "
        f"if [ $REPRO_RC -eq 0 ]; then echo '{ok_marker}' ; else echo '{fail_marker}' ; fi"
    )

    print(f"\n[REPRODUCTION] 🚀 Pasting reproduction command into Git PuTTY...")
    putty.activate_window(git_hwnd)
    time.sleep(0.3)
    putty.paste_text(git_hwnd, repro_cmd, press_enter=True)

    start_time = time.time()
    last_prompt_fill_time = 0

    while time.time() - start_time < 240:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]
        bottom_3 = lines[-3:] if len(lines) >= 3 else lines

        # 1. Check success marker
        has_ok = any(
            (l == ok_marker or l == f"'{ok_marker}'" or l == f'"{ok_marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and "REPRO_RC" not in l and not l.startswith("root@")
        )
        if has_ok:
            print("[REPRODUCTION] ✅ Reproduction command completed successfully!")
            return True

        # 2. Check failure marker
        has_fail = any(
            (l == fail_marker or l == f"'{fail_marker}'" or l == f'"{fail_marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and "REPRO_RC" not in l and not l.startswith("root@")
        )
        if has_fail:
            print("[REPRODUCTION] ❌ Reproduction command failed with non-zero exit code!")
            return False

        now = time.time()
        # Ensure at least 1.5s delay between entering credentials to prevent double submission
        if now - last_prompt_fill_time > 1.5:
            # Check for unanswered Username prompt (line ends with colon, e.g. "Username for '...':")
            is_user_prompt = any(
                ("username for" in l.lower() and l.endswith(":"))
                for l in bottom_3
            )
            if is_user_prompt:
                print("[REPRODUCTION] 🔑 Detected Username prompt! Submitting username...")
                putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
                last_prompt_fill_time = time.time()
                time.sleep(1.0)
                continue

            # Check for unanswered Password prompt (line ends with colon, e.g. "Password for '...':")
            is_pass_prompt = any(
                ("password for" in l.lower() and l.endswith(":"))
                for l in bottom_3
            )
            if is_pass_prompt:
                print("[REPRODUCTION] 🔒 Detected Password prompt! Submitting password...")
                putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
                last_prompt_fill_time = time.time()
                time.sleep(2.0)
                continue

        time.sleep(1.0)

    print("[REPRODUCTION] ⚠️ Reproduction command timed out after 240 seconds!")
    return False

def handle_merged_branch_cleanup(git_hwnd: int, task_file: Path) -> bool:
    """Process a file in tasks/merged/: delete branch if not active, archive task."""
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
    """Execute full workflow for a single task directly in /data/development."""
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
    
    try:
        task_info = parse_task_file(working_file, is_new_branch=is_from_pending)
    except Exception as e:
        print(f"[ERROR] Failed to parse task file: {e}")
        return False
        
    branch_name = task_info["branch_name"]
    is_new = task_info["is_new_branch"]
    commit_msg = task_info["commit_message"].replace('"', '\\"')
    prompt = task_info["prompt"]
    
    print(f"  -> Repository:    {config.SERVER_REPO_DIR}")
    print(f"  -> Branch:        {branch_name} ({'NEW branch off origin/main' if is_new else 'EXISTING branch'})")
    print(f"  -> Commit Msg:    {commit_msg}")
    print(f"  -> Prompt length: {len(prompt)} chars")

    # Step 2: Switch or create branch in /data/development
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

    # Step 3: Clear Claude context
    putty.clear_claude_context(claude_hwnd)

    # Step 4: Send Claude prompt
    print("\n[CLAUDE PUITY] 3. Sending task prompt to Claude...")
    time.sleep(1.0)
    watcher.mark_start()
    pasted = putty.paste_text(claude_hwnd, prompt, press_enter=True)
    if not pasted:
        print("[WARNING] Initial prompt paste failed! Retrying...")
        time.sleep(1.0)
        putty.paste_text(claude_hwnd, prompt, press_enter=True)

    # Step 5: Wait for completion
    print("[CLAUDE PUITY] Waiting for Claude to finish working on task...")
    completed = watcher.wait_for_claude_completion(claude_hwnd=claude_hwnd, idle_seconds=8, max_timeout=2400)
    if not completed:
        print("[WARNING] Claude did not finish cleanly or reached timeout! Proceeding with git check...")

    # Save log snapshot
    log_file = config.LOG_DIR / f"{task_file.stem}_claude.txt"
    try:
        claude_screen = putty.capture_screen_text(claude_hwnd)
        with open(log_file, "w", encoding="utf-8", errors="replace") as f:
            f.write(claude_screen)
        print(f"[LOG] 📝 Saved Claude's response snapshot to {log_file}")
    except Exception as e:
        print(f"[WARNING] Could not save Claude response log: {e}")

    # Step 6: Commit and push
    handle_git_push(git_hwnd, branch_name, commit_msg, config.SERVER_REPO_DIR)
    print(f"\n[GIT PUITY] ✅ Staying on branch '{branch_name}' in {config.SERVER_REPO_DIR}.")

    # Step 7: Move to completed
    completed_file = config.COMPLETED_DIR / task_name
    shutil.move(str(working_file), str(completed_file))
    print(f"\n[SUCCESS] ✅ Task '{task_name}' completed and archived to {completed_file}!\n")

    # Step 8: Usage check
    usage_pct, reset_time, is_over_limit = putty.check_claude_usage(claude_hwnd, threshold=config.USAGE_THRESHOLD)

    # Step 9: Clear Claude context
    print("[CLAUDE PUITY] 5. Sending /clear to wipe memory for next assignment...")
    putty.clear_claude_context(claude_hwnd)

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
    - Cleans up merged/ folder.
    - Processes re/ then pending/.
    - Recovers orphaned tasks in working/.
    - When idle, monitors Telegram for 'reproduction' and 'takeabreak' commands.
    """
    telegram_alert.init_telegram_listener()

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
        
        # Recover orphaned tasks in working/
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
                working_target = config.WORKING_DIR / task_file.name
                if working_target.exists():
                    fallback_dir = origin_dir if origin_dir else config.PENDING_DIR
                    target = fallback_dir / task_file.name
                    if target.exists():
                        target.unlink()
                    shutil.move(str(working_target), str(target))
                time.sleep(3)
        else:
            # BOTH re, pending, and working are completely empty: enter idle monitor
            has_new = telegram_alert.wait_for_new_task_or_alert(
                interval_seconds=120,
                git_hwnd=git_hwnd,
                reproduction_handler=handle_reproduction
            )
            if not has_new:
                break

if __name__ == "__main__":
    print("Testing task runner setup...")
    print(f"Re-open tasks: {len(list(config.RE_DIR.glob('*.txt')))}")
    print(f"Pending tasks: {len(list(config.PENDING_DIR.glob('*.txt')))}")