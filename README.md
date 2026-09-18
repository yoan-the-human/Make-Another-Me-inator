# 🚜 Make-Another-Me-inator

Automated Soviet Comrade Pipeline for Windows & PuTTY.

Orchestrates automated development execution across two active PuTTY sessions:
1. **Claude PuTTY**: Runs [Claude Code CLI](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) permanently in `/data/development`, handles coding prompts, monitors token quota, and auto-clears context.
2. **Git PuTTY**: Manages branch checkouts directly off `origin/main`, auto-enters credentials on push, cleans up merged branches, and executes remote commands.

---

## 📁 Task Queue Structure

The pipeline uses a multi-tier queue system located in `tasks/`:

```
tasks/
├── pending/      # Priority 2: New feature tasks (creates new branch off origin/main)
├── re/           # Priority 1: Re-opened tasks (switches to existing branch)
├── merged/       # Priority 0: Clean-up queue (deletes local branch if not active)
├── working/      # Currently active task (auto-recovers on script restart)
├── completed/    # Finished tasks pushed to remote GitLab/GitHub
├── archive/      # Cleaned-up tasks whose branches have been deleted
└── log/          # Saved terminal snapshots of Claude's output (<task>_claude.txt)
```

### Queue Priority Order:
1. **`merged/`**: Checks if local development has finished on the branch. If development is on another branch, runs `git branch -d <branch>` and archives the file to `archive/`.
2. **`re/`**: Higher priority tasks that continue work on an existing branch.
3. **`pending/`**: Standard queue for brand-new feature branches created from `origin/main`.
4. **`working/`**: If the script is aborted or crashes, any orphan tasks in `working/` are automatically restored to `re/` or `pending/` on next run.

---

## 📋 Task File Format

Place task files in `tasks/pending/` or `tasks/re/` with the `.txt` extension.

Each task file follows this 3-part format:
```text
new/catalog-videos
Add catalog videos pagination and layout improvements
Create the component for catalog videos pagination.
Make sure to follow existing styling patterns in the project.
Add tests and ensure build passes.
```

- **Line 1**: Branch name or git checkout command (e.g. `new/catalog-videos`, `feature/auth-refresh`, or `git checkout -b new/catalog-videos`).
- **Line 2**: Commit message used for `git commit -m "..."`.
- **Line 3+**: Full prompt sent to Claude Code. Supports multiline instructions.

---

## ⚙️ Configuration (`.env`)

Create a `.env` file in the root project directory:

```env
USERNAME=your_git_username
PASSWORD=your_git_password
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
USAGE_THRESHOLD=80.0
CLAUDE_POLL_INTERVAL=120
PUTTY_LOG_PATH=C:\path\to\putty_claude.log
```

| Variable | Description | Default |
| :--- | :--- | :--- |
| `USERNAME` | Git username for HTTP/HTTPS remote prompts | *Required* |
| `PASSWORD` | Git password / personal access token | *Required* |
| `TELEGRAM_BOT_TOKEN` | Bot token from `@BotFather` | *Required* |
| `TELEGRAM_CHAT_ID` | Your personal Telegram chat ID | *Required* |
| `USAGE_THRESHOLD` | Claude session quota limit % before pipeline safely pauses | `80.0` |
| `CLAUDE_POLL_INTERVAL` | Screen inspection interval in seconds while Claude thinks | `120` |
| `PUTTY_LOG_PATH` | Path to the PuTTY session printable log file | `putty_claude.log` |

---

## 📝 PuTTY Logging Setup for Claude

To allow the log watcher to monitor Claude's activity:
1. In the PuTTY session running Claude Code, right-click the title bar -> **Change Settings...**
2. In the left panel, navigate to **Session** -> **Logging**.
3. Under **Session logging**, select **Printable output**.
4. Set **Log file name** to a file path matching `PUTTY_LOG_PATH` in `.env` (e.g. `C:\scripts\Make-Another-Me-inator\putty_claude.log`).
5. Under **What to do if the log file already exists**, select **Always append to the end of it**.
6. Click **Apply**.

---

## 🕹️ Window Identification on Startup

When launching the script for the first time:
1. The script discovers all open PuTTY windows.
2. It brings each window to the front, flashes the title bar, and prompts:
   - `[1] Git Window`
   - `[2] Claude Window`
   - `[3] None / Skip`
3. Window handles are cached in `.putty_cache.json`. On subsequent launches, the script reuses these windows instantly without asking.
4. If you close or reopen your PuTTY sessions, run with `--reset-windows` to re-pair.

---

## 🤖 Telegram Bot Control & Alerts

You can message your Telegram bot directly while the script is idling:

| Command | Action |
| :--- | :--- |
| `reproduction` or `/reproduction` | Runs `cd /data/development && git fetch origin main && git -C ../haskovo.net pull origin main` in Git PuTTY. Automatically handles multiple username/password prompts. **Immediately resets the 2-minute idle timer** to guarantee zero collision with merge checks. |
| `takeabreak` or `/takeabreak` | Safely stops the automation pipeline immediately, simulating a clean `Ctrl+C` interrupt. |

### Automated Bot Notifications:
- **❓ Claude Question Alert**: Claude asks for an interactive decision, file permission, or tool approval. The bot sends a preview of the question to your phone.
- **🚀 Answer Detected**: Bot notifies you as soon as you answer in Claude PuTTY and generation resumes.
- **🚨 Git Push / Auth Failure**: Alerts if git authentication fails. The script stands guard waiting for manual push confirmation before continuing.
- **📊 Quota Protection**: When `/usage` exceeds `USAGE_THRESHOLD` (default 80%), the bot notifies you with quota reset time and halts the pipeline to avoid lockouts.
- **📢 Queue Idle Reminders**: Sends a reminder every 2 minutes when all task queues are empty.

---

## 🔄 Automated Execution Lifecycle

For every task file:
1. **Move**: Moves task from `pending/` or `re/` into `working/`.
2. **Git Setup** (Git PuTTY):
   - For `pending/`: Runs `git fetch origin main && git checkout -B <branch> origin/main`.
   - For `re/`: Runs `git checkout <branch>`.
   - Automatically enters username/password if prompted.
3. **Claude Execution** (Claude PuTTY):
   - Runs `/clear` to wipe previous context.
   - Pastes prompt (Line 3+).
   - Monitors terminal screen and log file until generation completes (`· done` / prompt returned).
   - Saves terminal snapshot to `tasks/log/<task_name>_claude.txt`.
4. **Git Commit & Push** (Git PuTTY):
   - Checks if changes exist via `git status --porcelain`.
   - Commits with line 2 message.
   - Pushes with GitLab MR flags: `git push -u origin <branch> -o merge_request.create -o merge_request.target=main`.
   - Auto-enters credentials when prompted.
   - Remains on the branch (no worktrees, no extra branch switches).
5. **Complete**:
   - Moves task from `working/` to `completed/`.
6. **Quota & Clean**:
   - Runs `/usage` to check session consumption.
   - Runs `/clear` to leave Claude clean for the next task.
7. **Idle Loop**:
   - If `merged/` contains tasks, checks every 2 minutes if the branch can be deleted (`git branch -d`).
   - Listens for Telegram commands (`reproduction`, `takeabreak`).
   - Automatically picks up new tasks as soon as files are added.

---

## 🚀 How to Run

Open PowerShell in the project directory:

```powershell
py main.py
```

### Command Line Options:
```powershell
# Test task parsing and planned commands without sending keystrokes to PuTTY
py main.py --dry-run

# Force re-selection of Git and Claude PuTTY windows
py main.py --reset-windows
```

---

## 📄 License

MIT License. Copyright (c) 2026 Yoan (the human).