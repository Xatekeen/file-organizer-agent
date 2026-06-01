"""
classifier.py — Multi-signal file classifier with OS metadata reasoning.
Scores files using: extension, filename keywords, permissions, size, and process origin.
"""

from __future__ import annotations
import os
import stat
import platform
import subprocess
from pathlib import Path
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Category rule definitions
# ---------------------------------------------------------------------------
RULES: dict[str, dict] = {
    "Images": {
        "extensions": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg",
                       ".webp", ".tiff", ".ico", ".heic", ".raw"},
        "keywords":   ["photo", "img", "image", "screenshot", "screen",
                       "wallpaper", "avatar", "thumbnail", "pic"],
        "creator_hints": ["screenshots", "camera", "photos"],
        "max_size_mb": None,
    },
    "Documents": {
        "extensions": {".pdf", ".docx", ".doc", ".txt", ".md", ".odt",
                       ".rtf", ".tex", ".epub", ".pages"},
        "keywords":   ["report", "notes", "draft", "memo", "letter",
                       "resume", "cv", "invoice", "receipt", "doc"],
        "creator_hints": ["word", "libreoffice", "pages"],
        "max_size_mb": 50,
    },
    "Code": {
        "extensions": {".py", ".js", ".ts", ".cpp", ".c", ".h", ".java",
                       ".go", ".rs", ".rb", ".php", ".sh", ".bash",
                       ".zsh", ".sql", ".html", ".css", ".json", ".yaml",
                       ".toml", ".xml", ".dockerfile"},
        "keywords":   ["script", "main", "utils", "helper", "config",
                       "test_", "_test", "setup", "build", "deploy"],
        "creator_hints": ["vscode", "vim", "emacs", "jetbrains"],
        "max_size_mb": 10,
    },
    "Videos": {
        "extensions": {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv",
                       ".webm", ".m4v", ".3gp", ".mpeg"},
        "keywords":   ["clip", "rec", "recording", "video", "movie",
                       "episode", "stream", "capture"],
        "creator_hints": ["obs", "quicktime", "ffmpeg"],
        "max_size_mb": None,
    },
    "Audio": {
        "extensions": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
                       ".wma", ".aiff", ".opus"},
        "keywords":   ["music", "song", "audio", "podcast", "voice",
                       "recording", "track", "beat"],
        "creator_hints": ["spotify", "audacity", "garageband"],
        "max_size_mb": None,
    },
    "Archives": {
        "extensions": {".zip", ".tar", ".gz", ".bz2", ".xz", ".rar",
                       ".7z", ".dmg", ".iso", ".pkg", ".deb", ".rpm"},
        "keywords":   ["backup", "archive", "compressed", "bundle",
                       "package", "release", "dist"],
        "creator_hints": ["curl", "wget", "browser"],
        "max_size_mb": None,
    },
    "Scripts": {
        "extensions": {".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat",
                       ".cmd", ".vbs"},
        "keywords":   ["run", "start", "install", "bootstrap", "init"],
        "creator_hints": [],
        "max_size_mb": 1,
    },
    "Spreadsheets": {
        "extensions": {".xlsx", ".xls", ".csv", ".ods", ".numbers"},
        "keywords":   ["data", "sheet", "table", "report", "stats",
                       "metrics", "sales", "budget"],
        "creator_hints": ["excel", "numbers", "libreoffice"],
        "max_size_mb": 100,
    },
    "Executables": {
        "extensions": {".exe", ".app", ".bin", ".out", ".run"},
        "keywords":   ["setup", "installer", "install", "app"],
        "creator_hints": [],
        "max_size_mb": None,
    },
}

CONFIDENCE_THRESHOLD = 0.45


@dataclass
class ClassificationResult:
    category: str
    confidence: float
    signals: dict[str, float]   # which signals contributed
    is_executable: bool
    size_bytes: int
    creator_process: str | None


def get_os_metadata(path: Path) -> dict:
    """Pull rich OS-level metadata via stat syscall."""
    try:
        s = os.stat(path)
        is_exec = os.access(path, os.X_OK)
        # st_birthtime on macOS; st_ctime on Linux (closest proxy)
        created = (s.st_birthtime
                   if hasattr(s, "st_birthtime")
                   else s.st_ctime)
        return {
            "size_bytes":    s.st_size,
            "created_at":    created,
            "last_accessed": s.st_atime,
            "permissions":   stat.filemode(s.st_mode),
            "is_executable": is_exec,
            "inode":         s.st_ino,
            "hard_links":    s.st_nlink,
            "uid":           s.st_uid,
        }
    except OSError:
        return {}


def find_creator_process(filepath: str) -> str | None:
    """
    Use lsof (Linux/macOS) or handle/wmic (Windows) to discover which
    process currently holds the file open — a pure OS-level signal.
    """
    system = platform.system()
    try:
        if system in ("Linux", "Darwin"):
            result = subprocess.run(
                ["lsof", "-F", "c", filepath],
                capture_output=True, text=True, timeout=2
            )
            for line in result.stdout.splitlines():
                if line.startswith("c"):
                    return line[1:].strip().lower()
        elif system == "Windows":
            result = subprocess.run(
                ["handle", filepath],
                capture_output=True, text=True, timeout=2
            )
            if result.stdout:
                return result.stdout.splitlines()[0].split()[0].lower()
    except Exception:
        pass
    return None


def classify(path: Path) -> ClassificationResult:
    """
    Score a file across all signals and return a ClassificationResult.
    Signals and their weights:
      extension   → 0.55 (strongest single signal)
      keyword     → 0.25 per match (capped at 0.35)
      executable  → +0.40 bonus toward Scripts/Executables
      size        → ±0.10 modifier
      creator     → +0.20 bonus if process hint matches
    """
    meta     = get_os_metadata(path)
    ext      = path.suffix.lower()
    stem     = path.stem.lower()
    is_exec  = meta.get("is_executable", False)
    size_b   = meta.get("size_bytes", 0)
    size_mb  = size_b / (1024 * 1024)
    creator  = find_creator_process(str(path))

    scores:  dict[str, float] = {cat: 0.0 for cat in RULES}
    signals: dict[str, float] = {}

    for cat, rule in RULES.items():
        score = 0.0

        # --- Extension signal (strongest) ---
        if ext in rule["extensions"]:
            score += 0.55
            signals[f"ext:{ext}→{cat}"] = 0.55

        # --- Keyword signal ---
        kw_score = 0.0
        for kw in rule["keywords"]:
            if kw in stem:
                kw_score = min(kw_score + 0.25, 0.35)
        if kw_score:
            score += kw_score
            signals[f"keyword→{cat}"] = kw_score

        # --- Executable permission bonus ---
        if is_exec and cat in ("Scripts", "Executables", "Code"):
            score += 0.40
            signals[f"exec_perm→{cat}"] = 0.40

        # --- Size plausibility modifier ---
        max_mb = rule.get("max_size_mb")
        if max_mb and size_mb > max_mb:
            score -= 0.10   # suspicious: a "code" file of 500 MB is unlikely

        # --- Creator process hint ---
        if creator:
            for hint in rule.get("creator_hints", []):
                if hint in creator:
                    score += 0.20
                    signals[f"creator:{creator}→{cat}"] = 0.20
                    break

        scores[cat] = max(score, 0.0)

    best_cat   = max(scores, key=scores.get)
    best_score = scores[best_cat]

    # Fall back to _unsorted if not confident enough
    if best_score < CONFIDENCE_THRESHOLD:
        best_cat   = "_unsorted"
        best_score = 0.0

    return ClassificationResult(
        category=best_cat,
        confidence=round(best_score, 3),
        signals=signals,
        is_executable=is_exec,
        size_bytes=size_b,
        creator_process=creator,
    )
