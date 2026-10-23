import os
import sys
import time
import shutil
import re
import random
from pathlib import Path
import requests

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
        "prompt": prompt,
        "task_name": file_path.stem,
        "is_exo": "/data/exo-android" in prompt,
        "has_backend": re.search(r"^\s*(?:#+\s*)?(?:Backend|Laravel)\b.*:", prompt, re.M | re.IGNORECASE) is not None
    }

def task_repos(info: dict) -> list[str]:
    """Return repositories list for task. Laravel first if backend present."""
    if not info.get("is_exo"):
        server_repo = config.SERVER_REPO_DIR if (config.SERVER_REPO_DIR and config.SERVER_REPO_DIR != config.EXO_REPO_DIR) else config.DEV_REPO_DIR
        return [server_repo]
    return ([config.DEV_REPO_DIR] if info.get("has_backend") else []) + [config.EXO_REPO_DIR]

def handle_git_push(git_hwnd: int, branch_name: str, commit_msg: str, repos: list[str] | str = None, is_exo: bool = False, task_name: str = "") -> bool:
    """Execute git add, commit, and push in target repo(s)."""
    if repos is None:
        target_repos = [config.SERVER_REPO_DIR]
    elif isinstance(repos, str):
        target_repos = [repos]
    else:
        target_repos = list(repos)

    push_uid = f"{int(time.time())}_{os.getpid()}"
    push_ok_marker = f"==PUSH_OK_{push_uid}=="
    push_fail_marker = f"==PUSH_FAIL_{push_uid}=="

    repos_str = " ".join(f'"{r}"' for r in target_repos)

    if is_exo:
        git_script = (
            f"PUSH_RC=0 ; "
            f"for R in {repos_str}; do "
            f"cd \"$R\" || continue ; "
            f"git show-ref -q \"refs/heads/{branch_name}\" || continue ; "
            f"[ -n \"$(git status --porcelain)\" ] && git add . && git commit -m \"{commit_msg}\" ; "
            f"[ \"$(git rev-list --count origin/main..\"{branch_name}\")\" -gt 0 ] || continue ; "
            f"git push -u origin \"{branch_name}\" -o merge_request.create -o merge_request.target=main "
            f"-o merge_request.label=exo "
            f"-o merge_request.description=\"EXO task {task_name}. Same branch in yoan/exo-android and yoan/haskovo.net-l13. Merge Laravel first.\" || PUSH_RC=$? ; "
            f"done ; "
            f"cd {config.DEV_REPO_DIR} ; "
            f"if [ $PUSH_RC -eq 0 ]; then echo '{push_ok_marker}' ; else echo '{push_fail_marker}' ; fi"
        )
    else:
        target_dir = target_repos[0] if target_repos else config.SERVER_REPO_DIR
        git_script = (
            f"cd {target_dir} && "
            f"if [ -n \"$(git status --porcelain)\" ]; then git add . && git commit -m \"{commit_msg}\" ; fi ; "
            f"git push -u origin {branch_name} -o merge_request.create -o merge_request.target=main ; "
            f"PUSH_RC=$? ; "
            f"if [ $PUSH_RC -eq 0 ]; then echo '{push_ok_marker}' ; else echo '{push_fail_marker}' ; fi"
        )

    print(f"\n[GIT PUTTY] 4. Checking git status, committing, and pushing branch '{branch_name}' in {target_repos}...")
    
    putty.activate_window(git_hwnd)
    time.sleep(0.3)
    putty.paste_text(git_hwnd, git_script, press_enter=True)
    print("[GIT PUTTY] Monitoring git push for credentials prompt or completion...")

    start_push = time.time()
    user_sent = False
    pass_sent = False

    while time.time() - start_push < 240:
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
            print("[GIT PUTTY] ✅ Push and commit completed successfully!")
            return True

        # Check active prompts on the last visible line
        is_user_prompt = ("username for" in last_line and last_line.endswith(":"))
        is_pass_prompt = ("password for" in last_line and last_line.endswith(":"))

        # Reset submission flags once Git has processed credentials and moved past prompt
        if not is_user_prompt:
            user_sent = False
        if not is_pass_prompt:
            pass_sent = False

        # 2. Check for fatal auth error ONLY if password was already entered and not at active prompt
        if pass_sent and not is_pass_prompt:
            has_fail_marker = any(
                (l == push_fail_marker or l == f'"{push_fail_marker}"' or l == f'"{push_fail_marker}"')
                for l in recent_lines
                if not l.startswith("echo") and not l.startswith("root@") and "PUSH_RC" not in l
            )
            has_auth_err = any(err in recent_text.lower() for err in [
                "fatal: authentication",
                "access denied",
                "invalid username or password"
            ])
            if has_fail_marker or has_auth_err:
                print("\n[GIT PUTTY] 🚨 Authentication failed after submitting credentials!")
                break

        # 3. Handle Username prompt
        if is_user_prompt and not user_sent:
            print("[GIT PUTTY] Detected Username prompt! Entering username...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            user_sent = True
            time.sleep(1.0)
            continue

        # 4. Handle Password prompt
        if is_pass_prompt and not pass_sent:
            print("[GIT PUTTY] Detected Password prompt! Entering password...")
            time.sleep(0.5)
            putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
            pass_sent = True
            time.sleep(2.0)
            continue

        time.sleep(1.0)

    # --- Halt and alert if not successful ---
    primary_repo = target_repos[0] if target_repos else config.SERVER_REPO_DIR
    print("\n" + "=" * 65)
    print("🚨 [PIPELINE HALTED] GIT PUSH FAILED OR REQUIRES MANUAL INTERACTION!")
    print(f"Branch:       {branch_name}")
    print(f"Repositories: {target_repos}")
    print("Please check Git PuTTY.")
    print("=" * 65 + "\n")

    telegram_alert.send_git_auth_failed_alert(branch_name, primary_repo)

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
            print(f"\n[GIT PUTTY] ✅ Git confirmation detected! Branch '{branch_name}' push confirmed!")
            telegram_alert.send_git_push_confirmed_notification(branch_name)
            time.sleep(1.0)
            return True

        if time.time() - last_reminder_time >= 60:
            print(f"[GIT PUTTY] ⏳ Still waiting for git push confirmation for '{branch_name}'...")
            telegram_alert.send_telegram_message(
                f"⏳ *Reminder:* Pipeline is waiting for git push confirmation for `{branch_name}` in Git PuTTY!"
            )
            last_reminder_time = time.time()

        time.sleep(2.0)

def handle_reproduction(git_hwnd: int, repo_dir: str = None) -> bool:
    """
    Executes 'git fetch origin main && git -C ../haskovo.net pull origin main' in Git PuTTY.
    Dynamically listens and fills username and password prompts (which may occur multiple times).
    Always runs in config.DEV_REPO_DIR (/data/development).
    """
    target_dir = config.DEV_REPO_DIR
    uid = f"{int(time.time() * 1000)}_{os.getpid()}"
    ok_marker = f"==REPRO_OK_{uid}=="
    fail_marker = f"==REPRO_FAIL_{uid}=="

    repro_cmd = (
        f"cd {target_dir} && "
        f"git fetch origin main && git -C ../haskovo.net pull origin main ; "
        f"REPRO_RC=$? ; "
        f"if [ $REPRO_RC -eq 0 ]; then echo '{ok_marker}' ; else echo '{fail_marker}' ; fi"
    )

    print(f"\n[REPRODUCTION] 🚀 Pasting reproduction command into Git PuTTY ({target_dir})...")
    putty.activate_window(git_hwnd)
    time.sleep(0.3)
    putty.paste_text(git_hwnd, repro_cmd, press_enter=True)

    start_time = time.time()
    username_submitted = False
    password_submitted = False

    while time.time() - start_time < 240:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]
        last_line = lines[-1].lower() if lines else ""

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

        # 3. Check active prompts on the last visible line
        is_user_prompt = ("username for" in last_line and last_line.endswith(":"))
        is_pass_prompt = ("password for" in last_line and last_line.endswith(":"))

        # Reset submission flags once Git has processed credentials and moved past the prompt
        if not is_user_prompt:
            username_submitted = False
        if not is_pass_prompt:
            password_submitted = False

        # Submit username if active and not already submitted for this prompt
        if is_user_prompt and not username_submitted:
            print("[REPRODUCTION] 🔑 Detected Username prompt! Submitting username...")
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            username_submitted = True
            time.sleep(1.0)
            continue

        # Submit password if active and not already submitted for this prompt
        if is_pass_prompt and not password_submitted:
            print("[REPRODUCTION] 🔒 Detected Password prompt! Submitting password...")
            putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
            password_submitted = True
            time.sleep(1.5)
            continue

        time.sleep(0.5)

    print("[REPRODUCTION] ⚠️ Reproduction command timed out after 240 seconds!")
    return False

def handle_merged_branch_cleanup(git_hwnd: int, task_file: Path, repo_dir: str = None) -> bool:
    """Process a file in tasks/merged/: delete branch if not active, archive task."""
    task_name = task_file.name
    try:
        task_info = parse_task_file(task_file, is_new_branch=False)
        branch_name = task_info["branch_name"]
    except Exception as e:
        print(f"[MERGED QUEUE] ⚠️ Failed to parse branch from '{task_name}': {e}")
        return False

    repos = task_repos(task_info) if task_info.get("is_exo") else [repo_dir or config.SERVER_REPO_DIR]
    repos_str = " ".join(f'"{r}"' for r in repos)

    uid = f"{int(time.time() * 1000)}_{os.getpid()}"
    active_marker = f"==BRANCH_ACTIVE_{uid}=="
    deleted_marker = f"==BRANCH_DELETED_{uid}=="

    check_cmd = (
        f"ACTIVE=0 ; "
        f"for R in {repos_str}; do "
        f"cd \"$R\" || continue ; "
        f"git show-ref -q \"refs/heads/{branch_name}\" || continue ; "
        f"CUR_B=$(git branch --show-current) ; "
        f"if [ \"$CUR_B\" = \"{branch_name}\" ]; then ACTIVE=1; break; fi ; "
        f"git branch -d \"{branch_name}\" ; "
        f"done ; "
        f"if [ $ACTIVE -eq 1 ]; then echo '{active_marker}'; else echo '{deleted_marker}'; fi"
    )

    print(f"\n[MERGED QUEUE] 🧹 Checking branch '{branch_name}' from '{task_name}' in {repos}...")
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
            print(f"[MERGED QUEUE] ⏸️ Development/repo is currently on branch '{branch_name}'! Doing nothing; file remains in merged/.\n")
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

def handle_review(git_hwnd: int, args: list[str]) -> bool:
    """
    Handle Telegram 'review [name]' command while pipeline is idle.
    """
    if not args:
        completed_files = sorted(list(config.COMPLETED_DIR.glob("*.txt")))
        if not completed_files:
            telegram_alert.send_telegram_message("📱 *Review:* No tasks currently in `completed/`.")
            return True
        lines = ["📱 *Completed tasks ready for review:*"]
        for cf in completed_files:
            try:
                info = parse_task_file(cf)
                t_type = "API" if info.get("has_backend") else ("app-only" if info.get("is_exo") else "site")
                if info.get("is_exo"):
                    apk_url = f"{config.EXO_APK_BASE_URL}{info['task_name']}.apk"
                    lines.append(f"• `{info['task_name']}` ({t_type}) - [APK]({apk_url})")
                else:
                    lines.append(f"• `{info['task_name']}` ({t_type})")
            except Exception:
                lines.append(f"• `{cf.stem}`")
        telegram_alert.send_telegram_message("\n".join(lines))
        return True

    task_name = args[0].strip().lower()
    if not re.match(r'^[a-z0-9-]+$', task_name):
        telegram_alert.send_telegram_message(f"❌ *Invalid task name:* `{task_name}`. Only alphanumeric characters and hyphens allowed!")
        return False

    candidate_dirs = [config.COMPLETED_DIR, config.RE_DIR, config.MERGED_DIR, config.ARCHIVE_DIR]
    found_file = None
    for c_dir in candidate_dirs:
        cand = c_dir / f"{task_name}.txt"
        if cand.exists():
            found_file = cand
            break

    if not found_file:
        telegram_alert.send_telegram_message(f'❌ No task "{task_name}"')
        return False

    try:
        info = parse_task_file(found_file)
    except Exception as e:
        telegram_alert.send_telegram_message(f'❌ Failed to parse task "{task_name}": {e}')
        return False

    branch = info["branch_name"]
    is_exo = info.get("is_exo", False)
    has_backend = info.get("has_backend", False)

    dev_status = ""
    migration_warning = ""

    if has_backend or not is_exo:
        uid = f"{int(time.time() * 1000)}_{os.getpid()}"
        dirty_m = f"==REVIEW_DIRTY_{uid}=="
        ok_m = f"==REVIEW_OK_{uid}=="
        fail_m = f"==REVIEW_FAIL_{uid}=="
        mig_start = f"==MIG_START_{uid}=="
        mig_end = f"==MIG_END_{uid}=="

        review_script = (
            f"cd {config.DEV_REPO_DIR} && "
            f"if [ -n \"$(git status --porcelain)\" ]; then echo '{dirty_m}'; "
            f"elif git checkout {branch}; then echo '{ok_m}'; "
            f"echo '{mig_start}'; git diff --name-only origin/main...{branch} -- database/migrations; echo '{mig_end}'; "
            f"else echo '{fail_m}'; fi"
        )
        print(f"\n[REVIEW] 📱 Switching /data/development to '{branch}' for review...")
        putty.activate_window(git_hwnd)
        time.sleep(0.3)
        putty.paste_text(git_hwnd, review_script, press_enter=True)

        def _is_marker(line, marker):
            return (line == marker or line == f"'{marker}'" or line == f'"{marker}"') and not line.startswith("echo ") and " && echo " not in line and not line.startswith("root@")

        start_t = time.time()
        switched = False
        is_dirty = False
        failed = False
        captured_lines = []
        while time.time() - start_t < 30:
            screen = putty.capture_screen_text(git_hwnd)
            lines = [l.strip() for l in screen.splitlines() if l.strip()]
            recent = lines[-25:]
            if any(_is_marker(l, dirty_m) for l in recent):
                is_dirty = True
                break
            if any(_is_marker(l, fail_m) for l in recent):
                failed = True
                break
            if any(_is_marker(l, mig_end) for l in recent):
                switched = True
                captured_lines = lines
                break
            time.sleep(0.5)

        if not is_dirty and not failed and not switched:
            if any(_is_marker(l, ok_m) for l in lines[-35:]):
                switched = True
                captured_lines = lines

        if is_dirty:
            dev_status = f"development → ⚠️ /data/development has uncommitted changes, not switching"
        elif switched:
            dev_status = f"development → {branch} ✅"
            mig_files = []
            capturing = False
            for l in captured_lines:
                if _is_marker(l, mig_start):
                    capturing = True
                    mig_files = []
                    continue
                if _is_marker(l, mig_end) and capturing:
                    break
                if capturing and l and not l.startswith("git ") and not l.startswith("echo ") and not l.startswith("root@"):
                    if not l.startswith("==") and not l.startswith("Already on") and not l.startswith("Switched to") and not l.startswith("Your branch"):
                        mig_files.append(l)
            if mig_files:
                migration_warning = f"\n⚠️ *This branch has migrations:* `{', '.join(mig_files)}`.\nRun them yourself (`php artisan migrate --path=...`) before testing."
        else:
            dev_status = f"development → ❌ Failed to switch to {branch}"
    else:
        dev_status = "development → app-only, development unchanged"

    apk_line = ""
    if is_exo:
        apk_url = f"{config.EXO_APK_BASE_URL}{task_name}.apk"
        try:
            res = requests.head(apk_url, timeout=10)
            has_apk = (res.status_code == 200)
        except Exception:
            has_apk = False
        if has_apk:
            apk_line = f"\nAPK: {apk_url}"
        else:
            apk_line = f"\n⚠️ No APK yet for {task_name}"

    reply_msg = (
        f"📱 *Review:* `{task_name}` (`{branch}`)\n"
        f"{dev_status}"
        f"{migration_warning}"
        f"{apk_line}"
    )
    telegram_alert.send_telegram_message(reply_msg)
    return True

def handle_reapk(git_hwnd: int, args: list[str] = None) -> bool:
    """
    Builds debug APK of the main branch in /data/exo-android (./gradlew assembleDebug),
    copies it to /mnt/nvme/exo-apks/ (served at https://development.haskovo.net/exo/),
    and verifies the APK via HTTP HEAD request before sending link to Telegram.
    """
    apk_name = "exo"
    if args and args[0].strip():
        cand = args[0].strip().lower()
        if re.match(r'^[a-z0-9-]+$', cand):
            apk_name = cand

    uid = f"{int(time.time() * 1000)}_{os.getpid()}"
    dirty_marker = f"==REAPK_DIRTY_{uid}=="
    ok_marker = f"==REAPK_OK_{uid}=="
    fail_marker = f"==REAPK_FAIL_{uid}=="

    apk_url = f"{config.EXO_APK_BASE_URL}{apk_name}.apk"

    cmd = (
        f"cd {config.EXO_REPO_DIR} && "
        f"if [ -n \"$(git status --porcelain)\" ]; then echo '{dirty_marker}'; false; fi && "
        f"git fetch origin main && git checkout main && git pull origin main && "
        f"./gradlew assembleDebug && "
        f"mkdir -p /mnt/nvme/exo-apks && "
        f"cp app/build/outputs/apk/debug/app-debug.apk /mnt/nvme/exo-apks/{apk_name}.apk ; "
        f"RC=$? ; "
        f"cd {config.DEV_REPO_DIR} ; "
        f"if [ $RC -eq 0 ]; then echo '{ok_marker}'; else echo '{fail_marker}'; fi"
    )

    print(f"\n[REAPK] 🚀 Building APK for branch 'main' in {config.EXO_REPO_DIR} -> /mnt/nvme/exo-apks/{apk_name}.apk...")
    putty.activate_window(git_hwnd)
    time.sleep(0.3)
    putty.paste_text(git_hwnd, cmd, press_enter=True)

    start_time = time.time()
    username_submitted = False
    password_submitted = False

    while time.time() - start_time < 360:
        screen = putty.capture_screen_text(git_hwnd)
        lines = [l.strip() for l in screen.splitlines() if l.strip()]
        recent_lines = lines[-25:]
        last_line = lines[-1].lower() if lines else ""

        # 0. Check dirty marker
        if any((l == dirty_marker or l == f"'{dirty_marker}'" or l == f'"{dirty_marker}"') for l in recent_lines if not l.startswith("echo ") and "RC=" not in l):
            print("[REAPK] 🚨 /data/exo-android has uncommitted changes!")
            telegram_alert.send_telegram_message("⚠️ *Vnimaniye, Comrade Yoan!* `/data/exo-android` has uncommitted changes! Refusing to switch to `main` to build APK.")
            return False

        # 1. Check success marker
        has_ok = any(
            (l == ok_marker or l == f"'{ok_marker}'" or l == f'"{ok_marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and "RC=" not in l and not l.startswith("root@")
        )
        if has_ok:
            print(f"[REAPK] ✅ APK built and copied successfully to /mnt/nvme/exo-apks/{apk_name}.apk!")
            apk_ok = False
            try:
                res = requests.head(apk_url, timeout=10)
                apk_ok = (res.status_code == 200)
            except Exception:
                apk_ok = False

            if apk_ok:
                msg = f"📱 *EXO APK Ready!* 🚀\nBranch: `main` (`exo-android`)\nAPK: {apk_url}"
            else:
                msg = f"✅ Build finished, but HTTP check to {apk_url} returned non-200. Please verify files on server!"
            telegram_alert.send_telegram_message(msg)
            return True

        # 2. Check failure marker
        has_fail = any(
            (l == fail_marker or l == f"'{fail_marker}'" or l == f'"{fail_marker}"')
            for l in recent_lines
            if not l.startswith("echo ") and "RC=" not in l and not l.startswith("root@")
        )
        if has_fail:
            print("[REAPK] ❌ Gradle build or copy failed with non-zero exit code!")
            telegram_alert.send_telegram_message("⚠️ *Vnimaniye, Comrade Yoan!* Gradle build or copy of `main` failed! Please check Git PuTTY.")
            return False

        # 3. Check active prompts on the last visible line
        is_user_prompt = ("username for" in last_line and last_line.endswith(":"))
        is_pass_prompt = ("password for" in last_line and last_line.endswith(":"))

        if not is_user_prompt:
            username_submitted = False
        if not is_pass_prompt:
            password_submitted = False

        if is_user_prompt and not username_submitted:
            print("[REAPK] 🔑 Detected Username prompt! Submitting username...")
            putty.paste_text(git_hwnd, config.GIT_USERNAME, press_enter=True)
            username_submitted = True
            time.sleep(1.0)
            continue

        if is_pass_prompt and not password_submitted:
            print("[REAPK] 🔒 Detected Password prompt! Submitting password...")
            putty.paste_text(git_hwnd, config.GIT_PASSWORD, press_enter=True)
            password_submitted = True
            time.sleep(1.5)
            continue

        time.sleep(1.0)

    print("[REAPK] ⚠️ Build timed out after 360 seconds!")
    telegram_alert.send_telegram_message("⚠️ *Vnimaniye, Comrade Yoan!* Building APK of `main` timed out after 6 minutes. Check Git PuTTY!")
    return False

def process_single_task(task_file: Path, git_hwnd: int, claude_hwnd: int, watcher: LogWatcher, is_quota_retry: bool = False) -> bool:
    """Execute full workflow for a single task directly in detected repository directory."""
    telegram_alert.set_dark_mode(False)
    task_name = task_file.name
    origin_parent = task_file.parent.name.lower()
    is_from_pending = (origin_parent == "pending")

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
    is_exo = task_info.get("is_exo", False)
    has_backend = task_info.get("has_backend", False)

    if is_exo:
        active_repo_dir = config.EXO_REPO_DIR
    else:
        detected_dir = putty.detect_git_working_dir(git_hwnd)
        if detected_dir and detected_dir != config.EXO_REPO_DIR:
            active_repo_dir = detected_dir
        else:
            active_repo_dir = config.DEV_REPO_DIR
        config.SERVER_REPO_DIR = active_repo_dir

    repos = task_repos(task_info)

    print(f"\n=======================================================")
    print(f"🚀 STARTING TASK: {task_name}")
    print(f"📍 REPOSITORIES: {repos}")
    print(f"=======================================================")

    # Format prompt for EXO tasks or adapt for site tasks
    if is_exo:
        repos_str = ", ".join(repos)
        header = (
            f'EXO task "{task_info["task_name"]}" (task file {task_info["task_name"]}.txt). '
            f'First read /data/exo-android/delete-later/instructions.txt.\n'
            f'The pipeline already created and checked out branch {branch_name} in: {repos_str}. '
            f"Don't create or switch branches,\n"
            f"don't commit and don't push: the pipeline commits and pushes after you finish.\n"
            f"---\n"
        )
        prompt = header + prompt
    else:
        folder_name = Path(active_repo_dir).name
        if folder_name.lower() != "development" and "development folder" in prompt.lower():
            prompt = re.sub(r'development\s+folder', f'{folder_name} folder', prompt, flags=re.IGNORECASE)

    print(f"  -> Repositories:  {repos}")
    print(f"  -> Branch:        {branch_name} ({'NEW branch off origin/main' if is_new else 'EXISTING branch'})")
    print(f"  -> Commit Msg:    {commit_msg}")
    print(f"  -> Prompt length: {len(prompt)} chars")

    # Step 2: Switch or create branch in target repositories
    marker = f"==BRANCH_READY_{int(time.time() * 1000)}=="
    dirty_marker = f"==DEV_DIRTY_{int(time.time() * 1000)}=="

    if is_exo:
        parts = []
        if has_backend:
            parts.append(f'cd {config.DEV_REPO_DIR} && [ -z "$(git status --porcelain)" ] || {{ echo \'{dirty_marker}\'; false; }}')
        for r in repos:
            if is_new:
                parts.append(f"cd {r} && git fetch origin main && git checkout -B {branch_name} origin/main")
            else:
                parts.append(f"cd {r} && {{ git show-ref -q refs/heads/{branch_name} && git checkout {branch_name} || true; }}")
        parts.append(f"echo '{marker}'")
        git_cmd = " && \\\n".join(parts)
        print(f"\n[GIT PUTTY] 1. Preparing branch '{branch_name}' across repos: {repos}...")
    else:
        if is_new:
            print(f"\n[GIT PUTTY] 1. Fetching origin main and checking out new branch '{branch_name}' in {active_repo_dir}...")
            git_cmd = (
                f"cd {active_repo_dir} && "
                f"git fetch origin main && "
                f"git checkout -B {branch_name} origin/main && "
                f"echo '{marker}'"
            )
        else:
            print(f"\n[GIT PUTTY] 1. Switching to existing branch '{branch_name}' in {active_repo_dir}...")
            git_cmd = (
                f"cd {active_repo_dir} && "
                f"git checkout {branch_name} && "
                f"echo '{marker}'"
            )

    putty.activate_window(git_hwnd)
    time.sleep(0.3)
    putty.paste_text(git_hwnd, git_cmd, press_enter=True)
    ready = putty.wait_for_git_branch(git_hwnd, branch_name, marker=marker, timeout=60, dirty_marker=dirty_marker)

    if ready == "dirty":
        print(f"\n[GIT PUTTY] 🚨 Branch setup refused: /data/development has uncommitted changes!")
        telegram_alert.send_telegram_message(
            f"🚨 *Vnimaniye, Comrade Yoan!* Task `{task_name}` halted: `/data/development` has uncommitted changes!\nRefusing to switch branches to protect your work. 🪆🐻"
        )
        return False

    if not ready:
        print(f"\n[GIT PUTTY] 🚨 Branch setup/fetch failed or halted for '{branch_name}'!")
        telegram_alert.send_git_auth_failed_alert(branch_name, str(repos))
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
                print(f"[GIT PUTTY] ✅ Branch '{branch_name}' confirmed active! Resuming pipeline...")
                telegram_alert.send_git_push_confirmed_notification(branch_name)
                time.sleep(1.0)
                break

            if time.time() - last_rem >= 60:
                print(f"[GIT PUTTY] ⏳ Still waiting for branch setup confirmation for '{branch_name}'...")
                telegram_alert.send_telegram_message(
                    f"⏳ *Reminder:* Pipeline is waiting for branch `{branch_name}` to be checked out in Git PuTTY!"
                )
                last_rem = time.time()

            time.sleep(2.0)

    # Step 3: Position Claude and clear context
    claude_target_dir = config.EXO_REPO_DIR if is_exo else active_repo_dir
    putty.ensure_claude_directory(claude_hwnd, claude_target_dir, watcher)
    putty.clear_claude_context(claude_hwnd)

    # Step 4: Send Claude prompt
    print("\n[CLAUDE PUTTY] 3. Sending task prompt to Claude...")
    time.sleep(1.0)
    watcher.mark_start()
    pasted = putty.paste_text(claude_hwnd, prompt, press_enter=True)
    if not pasted:
        print("[WARNING] Initial prompt paste failed! Retrying...")
        time.sleep(1.0)
        putty.paste_text(claude_hwnd, prompt, press_enter=True)

    # Step 5: Wait for completion
    print("[CLAUDE PUTTY] Waiting for Claude to finish working on task...")
    completed = watcher.wait_for_claude_completion(claude_hwnd=claude_hwnd, idle_seconds=8, max_timeout=2400)
    if not completed:
        print("[WARNING] Claude did not finish cleanly or reached timeout! Proceeding with git check...")

    # Step 6: Commit and push
    handle_git_push(git_hwnd, branch_name, commit_msg, repos=repos, is_exo=is_exo, task_name=task_info["task_name"])
    print(f"\n[GIT PUTTY] ✅ Staying on branch '{branch_name}' in {repos}.")

    # Step 7: Move to completed
    completed_file = config.COMPLETED_DIR / task_name
    shutil.move(str(working_file), str(completed_file))
    print(f"\n[SUCCESS] ✅ Task '{task_name}' completed and archived to {completed_file}!\n")

    # Step 7.5: Post-push APK check for EXO tasks
    if is_exo:
        apk_url = f"{config.EXO_APK_BASE_URL}{task_info['task_name']}.apk"
        apk_ok = False
        try:
            res = requests.head(apk_url, timeout=10)
            apk_ok = (res.status_code == 200)
        except Exception:
            apk_ok = False

        repos_count = len(repos)
        repo_desc = f"{repos_count} repos" if repos_count > 1 else "1 repo"
        if apk_ok:
            telegram_alert.send_telegram_message(f"✅ `{task_info['task_name']}` done. MR: {repo_desc}. APK: {apk_url}")
        else:
            telegram_alert.send_telegram_message(f"⚠️ `{task_info['task_name']}` finished but the APK is missing: {apk_url}")

    # Step 8: Usage check
    usage_pct, reset_time, is_over_limit = putty.check_claude_usage(claude_hwnd, threshold=config.USAGE_THRESHOLD)

    # Step 9: Clear Claude context and rename session for later access
    rand_id = random.randint(100, 999)
    session_name = f"{branch_name}-{rand_id}"
    print(f"[CLAUDE PUTTY] 5. Sending /clear {session_name} to save conversation and wipe memory for next assignment...")
    putty.clear_claude_context(claude_hwnd, session_name=session_name)

    if is_over_limit:
        telegram_alert.send_quota_alert(usage_pct, reset_time, is_repeat=is_quota_retry)
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
    - Halts task starting when quota reaches 80%+, keeps script alive, and waits for /keepgoing.
    - When idle, monitors Telegram for 'reproduction', 'review', 'takeabreak', and 'godark' commands.
    """
    telegram_alert.init_telegram_listener()

    last_merged_state = {}
    quota_paused = False
    quota_usage_pct = 0.0
    quota_reset_time = ""
    quota_one_shot = False

    def clean_merged_if_needed():
        nonlocal last_merged_state
        curr_merged_state = config.get_merged_snapshot()
        has_new_merged = any(
            name not in last_merged_state or mtime > last_merged_state[name]
            for name, mtime in curr_merged_state.items()
        )
        if has_new_merged:
            merged_files = sorted(list(config.MERGED_DIR.glob("*.txt")))
            if merged_files:
                detected_dir = putty.detect_git_working_dir(git_hwnd)
                if detected_dir and detected_dir != config.EXO_REPO_DIR:
                    config.SERVER_REPO_DIR = detected_dir
                else:
                    config.SERVER_REPO_DIR = config.DEV_REPO_DIR
                for m_file in merged_files:
                    handle_merged_branch_cleanup(git_hwnd, m_file, config.SERVER_REPO_DIR)
                    time.sleep(0.5)
            last_merged_state = config.get_merged_snapshot()
        else:
            last_merged_state = {k: v for k, v in last_merged_state.items() if k in curr_merged_state}

    while True:
        # Priority 0: Clean up merged branches when new or updated files appear in merged/
        clean_merged_if_needed()

        # If quota is at 80%+, halt task starting and wait for /keepgoing command or quota reset
        if quota_paused and not quota_one_shot:
            allowed = telegram_alert.wait_for_quota_pause(
                usage_pct=quota_usage_pct,
                reset_time=quota_reset_time,
                claude_hwnd=claude_hwnd,
                git_hwnd=git_hwnd,
                reproduction_handler=handle_reproduction,
                review_handler=handle_review,
                reapk_handler=handle_reapk,
                merged_handler=clean_merged_if_needed,
                interval_seconds=getattr(config, "CLAUDE_POLL_INTERVAL", 120)
            )
            if not allowed:
                break
            quota_one_shot = True

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
            was_quota_retry = quota_paused and quota_one_shot
            quota_one_shot = False

            try:
                success = process_single_task(task_file, git_hwnd, claude_hwnd, watcher, is_quota_retry=was_quota_retry)
                if not success:
                    print(f"[ERROR] Task '{task_file.name}' did not complete successfully.")
                else:
                    if quota_paused:
                        print(f"[QUOTA] 🎉 Session quota is now below threshold! Resuming normal automation pipeline.")
                        quota_paused = False
                        telegram_alert.send_quota_cleared_notification(quota_usage_pct)
            except QuotaLimitExceeded as qe:
                print(f"\n[QUOTA PAUSE] 🛑 Claude session quota is at {qe.usage_pct}% (>= {config.USAGE_THRESHOLD}%)!")
                print(f"[QUOTA PAUSE] Task starting halted. Script remains alive in bunker waiting for /keepgoing.\n")
                quota_paused = True
                quota_usage_pct = qe.usage_pct
                quota_reset_time = qe.reset_time
                quota_one_shot = False
            except Exception as e:
                print(f"[ERROR] Exception during task execution: {e}")
                quota_one_shot = False
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
                interval_seconds=60,
                git_hwnd=git_hwnd,
                reproduction_handler=handle_reproduction,
                review_handler=handle_review,
                reapk_handler=handle_reapk
            )
            if not has_new:
                break

if __name__ == "__main__":
    print("Testing task runner setup...")
    print(f"Re-open tasks: {len(list(config.RE_DIR.glob('*.txt')))}")
    print(f"Pending tasks: {len(list(config.PENDING_DIR.glob('*.txt')))}")