"""
agent.py — OS-Aware File Organizer Agent.

OS interaction points:
  • watchdog        → inotify/FSEvents file system event subscription
  • os.stat()       → inode, permissions, size, timestamps
  • lsof            → creator process detection
  • shutil.disk_usage → disk health before every move
  • signal          → SIGTERM / SIGINT graceful shutdown
  • syslog          → writes structured entries to the OS system log
  • subprocess      → shells out to OS utilities (lsof)
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

try:
    import syslog
    HAS_SYSLOG = True
except ImportError:
    HAS_SYSLOG = False

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

from agent.classifier import classify
from agent.storage import init_db, log_move, get_stats, reflect

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DISK_PAUSE_THRESHOLD = 90.0
STABILITY_POLLS      = 3
STABILITY_INTERVAL   = 0.4
SKIP_PREFIXES        = {".", "~", "_"}
SKIP_EXTENSIONS      = {".crdownload", ".part", ".tmp", ".download"}


# ---------------------------------------------------------------------------
# OS utilities
# ---------------------------------------------------------------------------
def _syslog(level: int, msg: str) -> None:
    if HAS_SYSLOG:
        syslog.syslog(level, msg)


def disk_ok(watch_path: str) -> bool:
    total, used, free = shutil.disk_usage(watch_path)
    pct = (used / total) * 100
    if pct > DISK_PAUSE_THRESHOLD:
        msg = f"[Agent] Disk {pct:.1f}% full — operations paused."
        print(msg)
        _syslog(syslog.LOG_WARNING if HAS_SYSLOG else 0, msg)
        return False
    return True


def wait_until_stable(path: Path) -> bool:
    sizes = []
    for _ in range(STABILITY_POLLS):
        try:
            sizes.append(path.stat().st_size)
        except FileNotFoundError:
            return False
        time.sleep(STABILITY_INTERVAL)
    return len(set(sizes)) == 1


def safe_move(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        ts   = int(time.time())
        dest = dest_dir / f"{src.stem}_{ts}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


def should_skip(path: Path) -> bool:
    name = path.name
    if any(name.startswith(p) for p in SKIP_PREFIXES):
        return True
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    return False


# ---------------------------------------------------------------------------
# Recursive folder organizer
# ---------------------------------------------------------------------------
def organize_folder_recursive(
    src_folder: Path,
    dest_parent: Path,
    handler: "FileOrganizerHandler",
    depth: int = 0,
) -> None:
    """
    Recursively organize a folder and all its subfolders.

    For every folder encountered:
      - Creates a '<foldername>-organized' counterpart in dest_parent
      - Files inside → copied and sorted into category subfolders
      - Subfolders → recursively handled the same way

    The original src_folder is deleted after organizing is complete.

    Example (depth=0, called on inbox/my_docs):
        organized/my_docs-organized/
            Images/photo.jpg
            reports-organized/          ← subfolder, same logic
                Documents/report.pdf
                Code/script.py
    """
    indent = "  " * depth
    organized_name = f"{src_folder.name}-organized"
    dest_folder    = dest_parent / organized_name
    dest_folder.mkdir(parents=True, exist_ok=True)

    print(f"[Agent] {indent}Organizing '{src_folder.name}' → '{organized_name}'")
    _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0,
            f"file-organizer: organizing folder '{src_folder.name}'")

    # Small wait at root level for files to finish copying in
    if depth == 0:
        time.sleep(1.5)

    # --- Process files directly inside this folder ---
    for item in sorted(src_folder.iterdir()):

        if item.is_file():
            if should_skip(item):
                continue

            if not wait_until_stable(item):
                continue

            if not disk_ok(str(src_folder)):
                break

            result  = classify(item)
            cat_dir = dest_folder / result.category

            if not handler.dry_run:
                try:
                    dest = safe_move(item, cat_dir)
                    log_move(
                        original   = item,
                        dest       = dest,
                        category   = result.category,
                        confidence = result.confidence,
                        size_bytes = result.size_bytes,
                        creator    = result.creator_process,
                        signals    = result.signals,
                    )
                    action = "MOVED"
                    print(f"[Agent] {indent}  '{item.name}' → {organized_name}/{result.category}/ ({result.confidence:.0%})")
                except OSError as e:
                    action = "ERROR"
                    print(f"[Agent] {indent}  ERROR moving '{item.name}': {e}")
            else:
                action = "DRY-RUN"
                print(f"[Agent] {indent}  [DRY] '{item.name}' → {organized_name}/{result.category}/")

            _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0,
                    f"file-organizer: {action} '{item.name}' → '{organized_name}/{result.category}'")

            handler._log_event(
                f"{'  ' * depth}[{src_folder.name}] {item.name}",
                result.category,
                result.confidence,
                result.creator_process,
                dry=handler.dry_run,
            )

        elif item.is_dir():
            # Recurse into subfolder — dest_parent is the current dest_folder
            organize_folder_recursive(item, dest_folder, handler, depth + 1)

    # --- Delete the now-empty (or fully processed) original folder ---
    if not handler.dry_run:
        try:
            # Remove whatever is left (empty dirs, skipped files)
            shutil.rmtree(str(src_folder))
            print(f"[Agent] {indent}Removed original '{src_folder.name}' from inbox")
            _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0,
                    f"file-organizer: removed original folder '{src_folder.name}'")
        except OSError as e:
            print(f"[Agent] {indent}Could not remove '{src_folder.name}': {e}")
    else:
        print(f"[Agent] {indent}[DRY] Would delete '{src_folder.name}' from inbox")


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------
class FileOrganizerHandler(FileSystemEventHandler):
    def __init__(self, watch_dir: Path, output_dir: Path,
                 dry_run: bool, event_log: list, lock: threading.Lock):
        self.watch_dir  = watch_dir
        self.output_dir = output_dir
        self.dry_run    = dry_run
        self.event_log  = event_log
        self.lock       = lock
        self._seen:         set[str] = set()
        self._seen_folders: set[str] = set()

    def on_created(self, event: FileCreatedEvent) -> None:
        path = Path(event.src_path)
        key  = str(path)

        # --- Folder dropped directly into watch_dir ---
        if event.is_directory:
            if path.parent != self.watch_dir:
                return   # ignore events from deeper nesting (we handle those ourselves)
            with self.lock:
                if key in self._seen_folders:
                    return
                self._seen_folders.add(key)
            # Run in background thread — keeps watcher loop responsive
            t = threading.Thread(
                target=organize_folder_recursive,
                args=(path, self.output_dir, self, 0),
                daemon=True,
            )
            t.start()
            return

        # --- File dropped directly into watch_dir ---
        if path.parent != self.watch_dir:
            return   # files inside subfolders handled by organize_folder_recursive

        with self.lock:
            if key in self._seen:
                return
            self._seen.add(key)

        if should_skip(path):
            self._seen.discard(key)
            return

        if not wait_until_stable(path):
            self._seen.discard(key)
            return

        if not disk_ok(str(self.watch_dir)):
            self._log_event(path.name, "_paused", 0.0, None, dry=False)
            self._seen.discard(key)
            return

        result   = classify(path)
        dest_dir = self.output_dir / result.category

        if not self.dry_run:
            try:
                dest = safe_move(path, dest_dir)
                log_move(
                    original   = path,
                    dest       = dest,
                    category   = result.category,
                    confidence = result.confidence,
                    size_bytes = result.size_bytes,
                    creator    = result.creator_process,
                    signals    = result.signals,
                )
                action = "MOVED"
            except OSError as e:
                action = "ERROR"
        else:
            action = "DRY-RUN"

        _syslog(syslog.LOG_INFO if HAS_SYSLOG else 0,
                f"file-organizer: {action} '{path.name}' → {result.category} "
                f"(conf={result.confidence:.0%})")

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
        self.observer:  Observer | None = None

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT,  self._handle_signal)

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
        self.observer.schedule(handler, str(self.watch_dir), recursive=True)
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
        stats  = get_stats()
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
        suggestions = reflect()
        if suggestions:
            print("\n  [Reflect] Suggested rule improvements:")
            for s in suggestions:
                print(s)
        print("─" * 52)

    def get_status(self) -> dict:
        return {
            "running":    self.running,
            "watch_dir":  str(self.watch_dir),
            "output_dir": str(self.output_dir),
            "dry_run":    self.dry_run,
            "os":         platform.system(),
            "events":     self.event_log[:],
            **get_stats(),
        }
