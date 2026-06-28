#!/usr/bin/env python3
"""Download PDFs listed in the local SQLite database."""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
SLUG = "miccai2024"
DB_PATH = ROOT / "data" / f"{SLUG}.sqlite"
PDF_DIR = ROOT / "pdfs"


def safe_filename(text: str, max_len: int = 120) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len].rstrip(" .")


def download_one(url: str, target: Path, overwrite: bool = False) -> bool:
    if target.exists() and target.stat().st_size > 10_000 and not overwrite:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            session = requests.Session()
            session.headers.update({"User-Agent": f"{SLUG}-local-paper-browser/1.0"})
            with session.get(url, stream=True, timeout=(20, 120)) as response:
                response.raise_for_status()
                with tmp.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 10))
    else:
        raise RuntimeError(f"failed to download {url}") from last_error
    tmp.replace(target)
    return True


def target_for(row: sqlite3.Row, out_dir: Path) -> Path:
    return out_dir / f"{int(row['ordinal']):04d}_{row['paper_id']}_{safe_filename(row['title'])}.pdf"


def download_task(row: sqlite3.Row, out_dir: Path, overwrite: bool) -> tuple[str, str, bool, str]:
    target = target_for(row, out_dir)
    try:
        changed = download_one(row["pdf_url"], target, overwrite)
        return row["paper_id"], str(target.relative_to(ROOT)), changed, ""
    except Exception as exc:
        return row["paper_id"], str(target.relative_to(ROOT)), False, str(exc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--out", type=Path, default=PDF_DIR)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        where = "" if args.overwrite else "WHERE downloaded = 0 OR downloaded IS NULL OR local_pdf_path IS NULL"
        rows = conn.execute(f"SELECT paper_id, ordinal, title, pdf_url FROM papers {where} ORDER BY ordinal").fetchall()
        if args.limit:
            rows = rows[:args.limit]
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_to_row = {executor.submit(download_task, row, args.out, args.overwrite): row for row in rows}
            for index, future in enumerate(as_completed(future_to_row), start=1):
                row = future_to_row[future]
                paper_id, local_path, changed, error = future.result()
                if not error:
                    conn.execute("UPDATE papers SET local_pdf_path = ?, downloaded = 1, updated_at = CURRENT_TIMESTAMP WHERE paper_id = ?", (local_path, paper_id))
                    conn.commit()
                    print(f"[{index}/{len(rows)}] {'downloaded' if changed else 'exists'}: {Path(local_path).name}", flush=True)
                else:
                    print(f"[{index}/{len(rows)}] failed {row['paper_id']}: {error}", flush=True)
                if args.delay:
                    time.sleep(args.delay)


if __name__ == "__main__":
    main()
