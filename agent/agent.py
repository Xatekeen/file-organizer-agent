"""
agent.py — OS-Aware File Organizer Agent.

OS interaction points:
  • watchdog   → inotify/FSEvents file system event subscription
  • os.stat()  → inode, permissions, size, timestamps
  • lsof       → creator process detection
  • shutil.disk_usage → disk health before every move
  • signal     → SIGTERM / SIGINT graceful shutdown
  • syslog     → writes structured entries to the OS system log
  • subprocess → shells out to OS utilities (lsof)
"""

from __future__ import annotations
import os
import sys
import signal
import shutil
import platform
import time
import threading
from pathlib import Path
from datetime import datetime

# Graceful import of syslog (Linux/macOS only)
try:
    import syslog
    HAS_SYSLOG = True
except ImportError:
    HAS_SYSLOG = False   # Windows fallback

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

from agent.classifier import classify
from agent.storage import init_db, log_move, get_stats, reflect

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISK_PAUSE_THRESHOLD = 90.0   # % usage above which agent pauses
STABILITY_POLLS      = 3      # how many times to check file size stability
STABILITY_INTERVAL   = 0.4    # seconds between stability polls
SKIP_PREFIXES        = {".", "~", "_"}   # hidden / temp / our own dirs
SKIP_EXTENSIONS      = {".crdownload", ".part", ".tmp", ".download"}


# ---------------------------------------------------------------------------
# OS utilities
# ---------------------------------------------------------------------------
def _syslog(level: int, msg: str) -> None:
    if HAS_SYSLOG:
        syslog.syslog(level, msg)


def disk_ok(watch_path: str) -> bool:
    """Return False and log a warning if the disk is dangerously full."""
    total, used, free = shutil.disk_usage(watch_path)
    pct = (used / total) * 100
    if pct > DISK_PAUSE_THRESHOLD:
        msg = f"[Agent] Disk {pct:.1f}% full — operations paused."
        print(msg)
        _syslog(syslog.LOG_WARNING if HAS_SYSLOG else 0, msg)
        return False
    return True


def wait_until_stable(path: Path) -> bool:
    """
    Poll file size STABILITY_POLLS times to confirm the file is fully written.
    Guards against acting on partial downloads (.crdownload race condition).
    """
    sizes = []
    for _ in range(STABILITY_POLLS):
        try:
            sizes.append(path.stat().st_size)
        except FileNotFoundError:
            return False
        time.sleep(STABILITY_INTERVAL)
    return len(set(sizes)) == 1


def safe_move(src: Path, dest_dir: Path) -> Path:
    """
    Move src → dest_dir, auto-renaming on collision.
    Returns the actual destination path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        ts    = int(time.time())
        dest  = dest_dir / f"{src.stem}_{ts}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


def should_skip(path: Path) -> bool:
    """Return True for hidden files, temp files, our own subdirs."""
    name = path.name
    if any(name.startswith(p) for p in SKIP_PREFIXES):
        return True
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    if not path.is_file():
        return True
    return False


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------
class FileOrganizerHandler(FileSystemEventHandler):
    def __init__(self, watch_dir: Path, output_dir: Path,
                 dry_run: bool, event_log: list, lock: threading.Lock):
        self.watch_dir  = watch_dir
        self.output_dir = output_dir
        self.dry_run    = dry_run
        self.event_log  = event_log   # shared list for dashboard
        self.lock       = lock
        self._seen: set[str] = set()  # dedup rapid duplicate events

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        key  = str(path)

        with self.lock:
            if key in self._seen:
                return
            self._seen.add(key)

        # --- Perceive ---
        if should_skip(path):
            self._seen.discard(key)
            return

        if not wait_until_stable(path):
            self._seen.discard(key)
            return

        # --- OS disk health check ---
        if not disk_ok(str(self.watch_dir)):
            self._log_event(path.name, "_paused", 0.0, None, dry=False)
            self._seen.discard(key)
            return

        # --- Reason (classify) ---
        result = classify(path)

        # --- Plan (destination) ---
        dest_dir = self.output_dir / result.category

        # --- Act ---
        if not self.dry_run:
            try:
                dest = safe_move(path, dest_dir)
                move_id = log_move(
                    original   = path,
                    dest       = dest,
                    category   = result.category,
                    confidence = result.confidence,
                    size_bytes = result.size_bytes,
                    creator    = result.creator_process,
                    signals    = result.signals,
                )
                action = "MOVED"
                dest_str = str(dest)
            except OSError as e:
                action   = "ERROR"
                dest_str = str(e)
                move_id  = -1
        else:
            action   = "DRY-RUN"
            dest_str = str(dest_dir / path.name)

        # --- Syslog (OS integration) ---
        log_msg = (
            f"file-organizer: {action} '{path.name}' → "
            f"{result.category} (conf={result.confidence:.0%})"
        )
        _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0, log_msg)

        # --- Update shared event log for dashboard ---
        self._log_event(path.name, result.category, result.confidence,
                        result.creator_process, dry=self.dry_run)

        self._seen.discard(key)

    def _log_event(self, name, cat, conf, creator, dry):
        entry = {
            "time":    datetime.now().strftime("%H:%M:%S"),
            "name":    name,
            "cat":     cat,
            "conf":    conf,
            "creator": creator or "—",
            "dry":     dry,
        }
        with self.lock:
            self.event_log.insert(0, entry)
            if len(self.event_log) > 100:
                self.event_log.pop()


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------
class FileOrganizerAgent:
    def __init__(self, watch_dir: str, output_dir: str, dry_run: bool = False):
        self.watch_dir  = Path(watch_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.dry_run    = dry_run
        self.running    = False
        self.event_log: list[dict] = []
        self.lock       = threading.Lock()
        self.observer: Observer | None = None

        # --- OS signal handlers ---
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

        # Open syslog connection
        if HAS_SYSLOG:
            syslog.openlog("file-organizer-agent",
                           syslog.LOG_PID, syslog.LOG_USER)

        init_db()

    def _handle_signal(self, sig, frame):
        sig_name = "SIGTERM" if sig == signal.SIGTERM else "SIGINT"
        print(f"\n[Agent] {sig_name} received — shutting down gracefully.")
        _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0,
                f"file-organizer-agent: received {sig_name}, stopping.")
        self.stop()
        self._print_shutdown_report()
        sys.exit(0)

    def start(self):
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.running = True

        handler = FileOrganizerHandler(
            self.watch_dir, self.output_dir,
            self.dry_run, self.event_log, self.lock
        )
        self.observer = Observer()
        self.observer.schedule(handler, str(self.watch_dir), recursive=False)
        self.observer.start()

        msg = (f"[Agent] Watching '{self.watch_dir}' "
               f"{'(DRY-RUN) ' if self.dry_run else ''}"
               f"→ organising into '{self.output_dir}'")
        print(msg)
        _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0,
                "file-organizer-agent: started. " + msg)

    def stop(self):
        self.running = False
        if self.observer:
            self.observer.stop()
            self.observer.join()

    def _print_shutdown_report(self):
        stats = get_stats()
        uptime = int(stats["uptime_secs"])
        h, m   = divmod(uptime // 60, 60)
        s      = uptime % 60
        print("\n" + "─" * 52)
        print("  File Organizer Agent — Session Report")
        print("─" * 52)
        print(f"  Uptime          : {h:02d}h {m:02d}m {s:02d}s")
        print(f"  Total moved     : {stats['total_moves']} files")
        print(f"  Unsorted        : {stats['unsorted']} files")
        print()
        for cat, data in stats["categories"].items():
            mb = data["bytes"] / (1024 * 1024)
            print(f"  {cat:<15} {data['count']:>4} files  "
                  f"{mb:>7.1f} MB  conf {data['avg_conf']:.0%}")

        # --- Reflect ---
        suggestions = reflect()
        if suggestions:
            print("\n  [Reflect] Suggested rule improvements:")
            for s in suggestions:
                print(s)
        print("─" * 52)

    def get_status(self) -> dict:
        """Return a snapshot for the dashboard."""
        return {
            "running":    self.running,
            "watch_dir":  str(self.watch_dir),
            "output_dir": str(self.output_dir),
            "dry_run":    self.dry_run,
            "os":         platform.system(),
            "events":     self.event_log[:],
            **get_stats(),
        }
