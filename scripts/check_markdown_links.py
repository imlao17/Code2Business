#!/usr/bin/env python3
"""Fail when a tracked Markdown file links to a missing local path."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def tracked_markdown_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def local_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        # Optional Markdown title: (path "title"). Repository paths do not
        # contain unescaped spaces, so the first token is the actual target.
        target = target.split(maxsplit=1)[0]
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith("/"):
        return None
    return unquote(parsed.path)


def main() -> int:
    missing: list[str] = []
    for document in tracked_markdown_files():
        for line_number, line in enumerate(
            document.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in LINK_RE.finditer(line):
                target = local_target(match.group(1))
                if not target:
                    continue
                resolved = (document.parent / target).resolve()
                if not resolved.exists():
                    missing.append(
                        f"{document.relative_to(ROOT)}:{line_number}: {target}"
                    )
    if missing:
        print("Missing local Markdown link targets:")
        print("\n".join(f"- {item}" for item in missing))
        return 1
    print(f"Checked local links in {len(tracked_markdown_files())} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
