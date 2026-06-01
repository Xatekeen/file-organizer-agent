"""
main.py — Entry point for the OS-Aware File Organizer Agent.

Usage:
    python main.py --watch ~/Downloads --output ~/Organized
    python main.py --watch ~/Downloads --output ~/Organized --dry-run
    python main.py --watch ~/Downloads --output ~/Organized --no-dashboard
"""

from __future__ import annotations
import argparse
import time
import sys
from pathlib import Path

from agent.agent import FileOrganizerAgent
from agent.server import set_agent, start_server_thread


def parse_args():
    p = argparse.ArgumentParser(
        description="OS-Aware File Organizer Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --watch ~/Downloads --output ~/Organized
  python main.py --watch ~/Downloads --output ~/Organized --dry-run
  python main.py --watch /tmp/inbox   --output /tmp/sorted --no-dashboard
        """
    )
    p.add_argument("--watch",    default="./inbox",
                   help="Directory to watch for new files (default: ./inbox)")
    p.add_argument("--output",   default="./organized",
                   help="Root directory for organised files (default: ./organized)")
    p.add_argument("--dry-run",  action="store_true",
                   help="Show what would happen without moving any files")
    p.add_argument("--no-dashboard", action="store_true",
                   help="Disable the web dashboard API server")
    p.add_argument("--port",     type=int, default=5050,
                   help="Dashboard API port (default: 5050)")
    return p.parse_args()


def main():
    args = parse_args()

    print("╔══════════════════════════════════════════════╗")
    print("║     OS-Aware File Organizer Agent  v1.0     ║")
    print("╚══════════════════════════════════════════════╝")

    agent = FileOrganizerAgent(
        watch_dir  = args.watch,
        output_dir = args.output,
        dry_run    = args.dry_run,
    )

    if not args.no_dashboard:
        set_agent(agent)
        start_server_thread(port=args.port)
        print(f"[Server] Dashboard API running at http://localhost:{args.port}")
        print(f"[Server] Open dashboard.html in your browser to monitor the agent")

    agent.start()

    print("[Agent] Running. Press Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass   # SIGINT handler in agent takes over


if __name__ == "__main__":
    main()
