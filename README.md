<div align="center">

# 🗂️ OS-Aware File Organizer Agent

**An agentic AI system that autonomously classifies and organizes files and folders by reasoning directly with the operating system.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![OS: Linux | macOS | Windows](https://img.shields.io/badge/OS-Linux%20%7C%20macOS%20%7C%20Windows-informational?style=flat-square&logo=linux&logoColor=white)](https://kernel.org)
[![Agentic AI](https://img.shields.io/badge/Agentic-AI-8A2BE2?style=flat-square)](https://github.com)
[![Course](https://img.shields.io/badge/Course-Operating%20Systems-1B4332?style=flat-square)](https://github.com)

*Built as a university Operating Systems course project — Sejong University*

</div>

---

## 🌍 Real-World Problem

Every computer user accumulates a chaotic **Downloads folder** — screenshots next to invoices next to source code, all with cryptic names. Manually sorting them is repetitive and error-prone.

Existing solutions rely **only on file extension rules** — which break the moment a file has no extension, an unusual name, or was created by a background process.

**This agent solves it differently:** it treats the **operating system itself as a source of truth**. Before classifying any file it asks:
- *What process created this?* → via `lsof`
- *Does it have execute permissions?* → via `os.access()`
- *How large is it relative to this category?* → via `os.stat()`
- *Is the disk healthy enough to proceed?* → via `shutil.disk_usage()`

Only after gathering all OS-level signals does it decide — and if it isn't confident enough, it **admits uncertainty** rather than misfiling silently.

---

## 🧠 How It Works — The Agent Loop

```
┌─────────────────────────────────────────────────────────────┐
│  1. PERCEIVE  — watchdog detects new file or folder         │
│                 os.stat() reads inode, permissions, size    │
├─────────────────────────────────────────────────────────────┤
│  2. REASON    — multi-signal classifier scores the file     │
│                 ext (+0.55) · keyword (+0.35) · exec perm   │
│                 (+0.40) · size check · lsof creator (+0.20) │
├─────────────────────────────────────────────────────────────┤
│  3. PLAN      — confidence ≥ 0.45 → category folder        │
│                 confidence < 0.45 → _unsorted/ (honest)     │
├─────────────────────────────────────────────────────────────┤
│  4. ACT       — safe_move() + SQLite log + syslog entry     │
│                 SIGTERM/SIGINT handled for graceful exit     │
├─────────────────────────────────────────────────────────────┤
│  5. REFLECT   — analyses _unsorted/ patterns over time      │
│                 suggests new classification rules            │
└─────────────────────────────────────────────────────────────┘
                         ↑ repeats ↑
```

---

## 📁 File vs Folder Behavior

### Single files
Dropped directly into `inbox/` → moved to `organized/Category/`

```
inbox/photo.jpg        →   organized/Images/photo.jpg
inbox/report.pdf       →   organized/Documents/report.pdf
inbox/main.py          →   organized/Code/main.py
inbox/mystery_file     →   organized/_unsorted/mystery_file
```

### Folders (with full recursive support)
Dropped into `inbox/` → an organized copy is created in `organized/`, original is deleted from `inbox/` when done.

```
inbox/my_docs/                     ← original (deleted after organizing)
    photo.jpg
    budget.xlsx
    reports/                       ← subfolder
        report.pdf
        script.py

organized/my_docs-organized/       ← clean organized copy ✅
    Images/
        photo.jpg
    Spreadsheets/
        budget.xlsx
    reports-organized/             ← subfolder recursively organized ✅
        Documents/
            report.pdf
        Code/
            script.py
```

---

## ⚙️ OS Interaction Points

| OS Feature | Purpose | Python API |
|---|---|---|
| **inotify / FSEvents** | Real-time file & folder creation events | `watchdog` |
| **inode / stat** | Permissions, size, timestamps, hard links | `os.stat()` |
| **File permissions** | Classify executables differently | `os.access(X_OK)` |
| **lsof** | Detect which process created the file | `subprocess.run(["lsof"])` |
| **SIGTERM / SIGINT** | Graceful shutdown + state save | `signal.signal()` |
| **disk_usage** | Pause operations when disk > 90% | `shutil.disk_usage()` |
| **syslog** | Write events to OS system log (`journalctl`) | `syslog` module |
| **SQLite (filesystem)** | Atomic undo log + reflection queries | `sqlite3` |

---

## 📁 Project Structure

```
file-organizer-agent/
├── main.py               ← Entry point + CLI (--watch, --output, --dry-run)
├── dashboard.html        ← Live web dashboard (open in browser)
├── requirements.txt
├── .gitignore
├── agent/
│   ├── __init__.py
│   ├── agent.py          ← Core loop + recursive folder organizer
│   ├── classifier.py     ← Multi-signal OS-aware classifier
│   ├── storage.py        ← SQLite undo log + stats + reflection
│   └── server.py         ← Flask API bridging agent ↔ dashboard
└── logs/                 ← Auto-created on first run
    └── agent.db
```

> `inbox/`, `organized/`, `logs/`, and `venv/` are auto-created at runtime and are not included in the repository.

---

## 🚀 Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/file-organizer-agent.git
cd file-organizer-agent

# 2. Create virtual environment
python -m venv venv

# Linux/macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the agent
python main.py --watch ./inbox --output ./organized

# 5. Open dashboard.html via the local server
python -m http.server 8080 --bind 127.0.0.1
# Then open: http://127.0.0.1:8080/dashboard.html
```

---

## 🧪 Test It

**Single files:**
```bash
# Linux/macOS
touch inbox/photo_summer.jpg
touch inbox/report_draft.pdf
echo "print('hello')" > inbox/main.py
touch inbox/mystery_file

# Windows
copy NUL inbox\photo_summer.jpg
copy NUL inbox\report_draft.pdf
echo print("hello") > inbox\main.py
copy NUL inbox\mystery_file
```

**Folders (recursive):**
```bash
# Linux/macOS
mkdir -p inbox/my_docs/reports
touch inbox/my_docs/photo.jpg
touch inbox/my_docs/budget.xlsx
touch inbox/my_docs/reports/report.pdf
echo "x=1" > inbox/my_docs/reports/script.py

# Windows
mkdir inbox\my_docs\reports
copy NUL inbox\my_docs\photo.jpg
copy NUL inbox\my_docs\budget.xlsx
copy NUL inbox\my_docs\reports\report.pdf
echo x=1 > inbox\my_docs\reports\script.py
```

---

## 💻 CLI Options

```bash
# Dry-run: show decisions without moving anything
python main.py --watch ./inbox --output ./organized --dry-run

# Disable dashboard API
python main.py --watch ./inbox --output ./organized --no-dashboard

# Custom API port
python main.py --watch ./inbox --output ./organized --port 8080
```

---

## 📊 Classifier Signal Weights

| Signal | Weight | Source |
|---|---|---|
| File extension match | `+0.55` | `path.suffix` |
| Filename keyword match | `+0.25` each, max `0.35` | `path.stem` |
| Execute permission | `+0.40` | `os.access(X_OK)` |
| Size implausibility | `−0.10` | `os.stat().st_size` |
| Creator process hint | `+0.20` | `lsof` |

> Files scoring **below 0.45** go to `_unsorted/` — the agent admits uncertainty rather than guessing.

---

## 🔌 REST API

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | `GET` | Full agent state, uptime, per-category stats |
| `/api/events` | `GET` | Recent move log |
| `/api/undo` | `POST` | Undo last N moves `{"n": 1}` |
| `/api/reflect` | `GET` | Rule improvement suggestions |

---

## 📋 Session Report on Shutdown

```
────────────────────────────────────────────────────
  File Organizer Agent — Session Report
────────────────────────────────────────────────────
  Uptime          : 00h 12m 34s
  Total moved     : 47 files
  Unsorted        : 3 files

  Documents        18 files    42.1 MB  conf 89%
  Images           12 files   188.4 MB  conf 94%
  Code              9 files     1.2 MB  conf 91%
  _unsorted         3 files     0.9 MB  conf  0%

  [Reflect] Suggested rule improvements:
    Extension '.sketch' appeared 3× in _unsorted — consider adding it.
────────────────────────────────────────────────────
```

---

## 🛠️ Tech Stack

| Layer | Tools |
|---|---|
| **OS Interaction** | `watchdog` · `os.stat()` · `signal` · `syslog` · `shutil` · `subprocess` |
| **Agent Core** | `sqlite3` · `pathlib` · `threading` · `time` |
| **API & UI** | `flask` · `dashboard.html` (vanilla JS) |

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

<div align="center">
<sub>Built with Claude (Anthropic) · Sejong University · Operating Systems Course</sub>
</div>
