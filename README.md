# OS-Aware File Organizer Agent

A fully agentic file organizer that interacts with the OS at multiple layers —
reading inode metadata, querying running processes, handling UNIX signals, monitoring
disk health, and writing to the system logger.

Built for a 4-week university Operating Systems course project.

---

## OS Interaction Points

| OS Feature | How it's used | Python API |
|---|---|---|
| **inotify / FSEvents** | Subscribe to file system events | `watchdog` |
| **inode / stat** | Read permissions, size, timestamps | `os.stat()` |
| **lsof** | Detect which process created the file | `subprocess` |
| **SIGTERM / SIGINT** | Graceful shutdown on OS signals | `signal.signal()` |
| **disk_usage** | Pause if disk > 90% full | `shutil.disk_usage()` |
| **syslog** | Write structured entries to OS log | `syslog` module |
| **file permissions** | Classify executables differently | `os.access(X_OK)` |
| **hard links** | Detect duplicate inodes | `stat.st_nlink` |

---

## Project Structure

```
file-organizer-agent/
├── main.py               ← Entry point + CLI
├── dashboard.html        ← Live web dashboard (open in browser)
├── requirements.txt
├── agent/
│   ├── agent.py          ← Core agent: perceive → reason → act → reflect
│   ├── classifier.py     ← Multi-signal OS-aware classifier
│   ├── storage.py        ← SQLite undo log + stats + reflection
│   └── server.py         ← Flask API bridging agent → dashboard
└── logs/
    └── agent.db          ← SQLite database (auto-created)
```

---

## Setup

```bash
# 1. Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the agent
python main.py --watch ./inbox --output ./organized

# 4. Open dashboard.html in your browser
open dashboard.html             # macOS
xdg-open dashboard.html         # Linux
```

---

## Usage

```bash
# Normal mode — watch ./inbox, sort into ./organized
python main.py --watch ./inbox --output ./organized

# Dry-run — show decisions without moving any files
python main.py --watch ./inbox --output ./organized --dry-run

# No dashboard (terminal only)
python main.py --watch ~/Downloads --output ~/Sorted --no-dashboard

# Custom API port
python main.py --watch ./inbox --output ./organized --port 8080
```

### Test it

```bash
# In a second terminal, drop test files into the inbox:
mkdir -p inbox
touch inbox/photo_summer.jpg
touch inbox/budget_2024.xlsx
echo "print('hello')" > inbox/main.py
cp /bin/ls inbox/my_script      # executable with no extension
```

Watch the dashboard update in real time.

### Undo

```bash
# Via dashboard — click "Undo last move"
# Via API:
curl -X POST http://localhost:5050/api/undo \
     -H "Content-Type: application/json" \
     -d '{"n": 1}'
```

---

## Agent Decision Loop

```
         ┌─────────────────────────────┐
         │  1. PERCEIVE                │
         │  watchdog detects new file  │
         │  os.stat() reads metadata   │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │  2. REASON                  │
         │  score: ext + keyword +     │
         │  permission + size + lsof   │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │  3. PLAN                    │
         │  confident? → category dir  │
         │  unsure?    → _unsorted/    │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │  4. ACT                     │
         │  safe_move() + SQLite log   │
         │  syslog entry written       │
         └──────────────┬──────────────┘
                        │
         ┌──────────────▼──────────────┐
         │  5. REFLECT                 │
         │  analyse _unsorted patterns │
         │  suggest new rules          │
         └─────────────────────────────┘
```

---

## Classifier Signals

Each file is scored across 5 independent OS signals:

| Signal | Weight | How |
|---|---|---|
| File extension | +0.55 | `path.suffix` |
| Filename keywords | +0.25 each, capped at 0.35 | `path.stem` |
| Execute permission | +0.40 | `os.access(X_OK)` |
| Size plausibility | −0.10 | `os.stat().st_size` |
| Creator process | +0.20 | `lsof` output |

Files scoring below **0.45** go to `_unsorted/` — the agent refuses to guess.

---

## Dashboard API

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | Full agent state + stats |
| `/api/events` | GET | Recent move log |
| `/api/undo` | POST | Undo last N moves |
| `/api/reflect` | GET | Rule improvement suggestions |

---

## Shutdown Report

On `Ctrl+C` or `SIGTERM`, the agent prints a full session report:

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
  Videos            5 files  1024.0 MB  conf 87%
  _unsorted         3 files     0.9 MB  conf  0%

  [Reflect] Suggested rule improvements:
    Extension '.sketch' appeared 3× in _unsorted — consider adding it.
────────────────────────────────────────────────────
```

---

## Key Design Decisions

**Why confidence thresholds?** A file organizer that misfiles silently is worse than
one that admits uncertainty. The `_unsorted/` bucket is the agent saying "I don't know"
— a trustworthy fallback rather than a silent guess.

**Why SQLite not a plain log file?** Every move is a structured record. This enables
atomic undo, per-category stats queries, and the reflection analysis — none of which
are practical with append-only text logs.

**Why syslog?** The agent is a daemon-like process. Writing to syslog means its events
appear alongside OS-level events in `journalctl` or `/var/log/syslog` — exactly where
a sysadmin would look. It treats the agent as a first-class OS citizen.

**Why wait_until_stable()?** The watchdog fires on `IN_CREATE`, which fires the instant
a file descriptor is opened — before any bytes are written. Without stability polling,
you'd classify and move empty `.crdownload` files.
