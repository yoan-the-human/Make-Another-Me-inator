# 🚜 Make-Another-Me-inator

Automated Soviet Comrade Pipeline for Windows & PuTTY.

Orchestrates automated execution between two open PuTTY sessions:
1. **Claude PuTTY**: Executes coding prompts via Claude Code CLI.
2. **Git PuTTY**: Manages Git worktrees, commits, GitLab Merge Request pushes, and worktree cleanup.

---

## 📋 Task Format

Place task files in `tasks/pending/` with the `.txt` extension.

Each task file must follow this 3-part format:
```text
git worktree add ../tasks/task-XYZ -b feature/XYZ
Commit message describing the changes
Claude prompt from line 3 until the end of the file...
Multiple lines of prompt instructions can be placed here.
```

- **Line 1**: Git worktree command. The script dynamically extracts the worktree folder (`../tasks/task-XYZ`) and the branch name (`feature/XYZ` or `new/...`).
- **Line 2**: Git commit message used for `git commit -m "..."`.
- **Line 3+**: Full prompt sent to Claude Code.

---

## ⚙️ Configuration (`.env`)

Ensure your `.env` in this directory contains:
```env
USERNAME=your_git_username
PASSWORD=your_git_password
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
PUTTY_LOG_PATH=C:\path\to\putty_claude.log
```
*(If `PUTTY_LOG_PATH` is omitted, it defaults to `putty_claude.log` in this project folder).*

### 📝 Enabling PuTTY Session Logging for Claude
1. In the PuTTY window running Claude, right-click the title bar -> **Change Settings...**
2. In the left tree, select **Session** -> **Logging**.
3. Under **Session logging**, select **Printable output**.
4. Set **Log file name** to a path on your PC (e.g. `C:\Users\yoanv\Documents\Yoan (the human)\💻\scripts\Make-Another-Me-inator\putty_claude.log`).
5. Under **What to do if the log file already exists**, select **Always append to the end of it**.
6. Click **Apply**.

---

## 🚀 How to Run

Open PowerShell in this project folder and run:
```powershell
py main.py
```

### Command-line Options:
- `py main.py --dry-run` : Parses tasks in `tasks/pending/` and displays planned commands without sending keystrokes.
- `py main.py --reset-windows` : Forces re-prompting to identify Git vs Claude PuTTY windows.

---

## 🕹️ Window Identification on Startup

When you launch `py main.py`:
1. The script finds all open PuTTY windows on your screen.
2. It brings each window to the front, flashes the title bar, and asks:
   - `[1] Git Window`
   - `[2] Claude Window`
   - `[3] None / Skip` (for other open PuTTY sessions)
3. Your window choices are cached in `.putty_cache.json` so you do not need to re-identify them every time you restart the script. (Use `--reset-windows` if you close/reopen PuTTY).

---

## 🔄 Execution Workflow

For every task in `tasks/pending/`:
1. Moves file to `tasks/working/`.
2. In Git PuTTY:
   - Runs `git worktree add ...`
   - Runs `cd ../tasks/task-XYZ`
3. In Claude PuTTY:
   - Runs `/clear` (wipes previous context to avoid token bloat).
   - Runs `/cd ../tasks/task-XYZ`.
   - Detects Claude's directory trust prompt (`"Is this a directory you created or one you trust?"`).
   - Automatically sends `Down Arrow` + `Enter` to select **"Yes, move here"**.
   - Pastes the prompt (Line 3+).
   - Monitors `putty_claude.log` until Claude finishes thinking and tool execution.
   - Runs `/cd /data/testapp` to safely release the worktree directory.
4. In Git PuTTY:
   - Runs:
     ```bash
     if [ -n "$(git status --porcelain)" ]; then
       git add .
       git commit -m "<Line 2>"
       git push -u origin <branch> -o merge_request.create -o merge_request.target=main
     fi
     ```
   - Automatically enters Username and Password from `.env` lines 1 & 2 when Git prompts.
   - Runs `cd /data/testapp` and `git worktree remove --force ../tasks/task-XYZ`.
5. Moves task file to `tasks/completed/`.
6. Sends `/clear` to Claude PuTTY to wipe memory fresh for the next task.
7. When `tasks/pending/` is empty:
   - Sends Telegram alert to your phone.
   - Repeats every 2 minutes until new task files are added to `tasks/pending/`.