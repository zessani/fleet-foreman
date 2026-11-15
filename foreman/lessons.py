"""Put the AI agents' lessons file in a known state before a demo.

    python -m foreman.lessons show     list what is in runs/lessons.jsonl
    python -m foreman.lessons reset    empty it: the AI Recovery agent starts with no history
    python -m foreman.lessons seed     load one earlier day of lessons (foreman/data/lessons_day1.jsonl)

Press Reset on the dashboard afterwards: each new day reads the file again.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LESSONS = ROOT / "runs" / "lessons.jsonl"
SEED = Path(__file__).resolve().parent / "data" / "lessons_day1.jsonl"


def main(argv: list[str] | None = None) -> int:
    action = (argv if argv is not None else sys.argv[1:] or ["show"])[0]
    LESSONS.parent.mkdir(parents=True, exist_ok=True)
    if action == "reset":
        LESSONS.write_text("", encoding="utf-8")
        print(f"Emptied {LESSONS}. The AI Recovery agent starts with no history.")
    elif action == "seed":
        if not SEED.exists():
            print(f"No seed file at {SEED}.")
            return 1
        shutil.copyfile(SEED, LESSONS)
        print(f"Loaded {len(SEED.read_text().splitlines())} lessons from one earlier day into {LESSONS}.")
    elif action == "show":
        lines = LESSONS.read_text(encoding="utf-8").splitlines() if LESSONS.exists() else []
        print(f"{len(lines)} lesson(s) in {LESSONS}")
        for line in lines:
            print(" ", line)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
