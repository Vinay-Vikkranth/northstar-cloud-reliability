"""Load versioned runbooks. Re-running updates nothing; a new file version needs a new version number."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core")]

from sqlalchemy import select

from recovery_api.db import session_factory
from recovery_api.models import RunbookDoc

VERSION = 1


def load() -> int:
    db = session_factory()()
    added = 0
    try:
        for path in sorted((ROOT / "runbooks").glob("*.md")):
            slug = path.stem
            existing = db.scalar(select(RunbookDoc).where(RunbookDoc.slug == slug, RunbookDoc.version == VERSION))
            if existing:
                continue
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            db.add(RunbookDoc(slug=slug, version=VERSION, title=title, body=text))
            added += 1
        db.commit()
        return added
    finally:
        db.close()


if __name__ == "__main__":
    print(f"runbooks_added={load()}")
