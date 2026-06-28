#!/usr/bin/env python3
"""Translate paper abstracts and reviews into Simplified Chinese."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent
SLUG = "miccai2024"
DB_PATH = ROOT / "data" / f"{SLUG}.sqlite"
API_BASE = "http://10.10.70.124:8082"
DEFAULT_MODEL = "Hy-MT2-1.8B-Q8_0.gguf"
DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    for column in ("abstract_zh", "reviews_text_zh", "meta_review_zh"):
        if column not in columns:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {column} TEXT")
    conn.commit()


def normalize_source_text(text: str) -> str:
    return (
        text.replace("\ufeff", "")
        .replace("\ufffd", "")
        .replace("\x00", "")
        .replace('"', "'")
        .replace("\u201c", "'")
        .replace("\u201d", "'")
    )


def clean_translation(text: str) -> str:
    cleaned = text.strip()
    for prefix in ("Chinese:", "Translation:", "Translate to Chinese:", "中文翻译：", "翻译："):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip()
    return cleaned


def chat_completions_url(api_base: str, api_path: str) -> str:
    return f"{api_base.rstrip('/')}/{api_path.lstrip('/')}"


def translate_text(
    session: requests.Session,
    api_base: str,
    api_path: str,
    model: str,
    text: str,
    timeout: int,
    api_key: str | None = None,
) -> str:
    if not text.strip():
        return ""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "请将下面的医学/计算机辅助医疗论文内容翻译为简体中文。\n"
                    "要求：只输出译文；保留原有段落、编号、评分、URL、论文术语和专有名词；不要总结、不要解释。\n\n"
                    f"{normalize_source_text(text)}"
                ),
            }
        ],
        "temperature": 0,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = session.post(
                chat_completions_url(api_base, api_path),
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            return clean_translation(data["choices"][0]["message"]["content"])
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError("translation request failed") from last_error


def translate_row(row: sqlite3.Row, args: argparse.Namespace, api_key: str | None) -> tuple[str, int, str, dict[str, str], str]:
    session = requests.Session()
    updates = {}
    try:
        if args.overwrite or (row["abstract"] and not row["abstract_zh"]):
            updates["abstract_zh"] = translate_text(session, args.api_base, args.api_path, args.model, row["abstract"], args.timeout, api_key)
        if args.overwrite or (row["reviews_text"] and not row["reviews_text_zh"]):
            updates["reviews_text_zh"] = translate_text(session, args.api_base, args.api_path, args.model, row["reviews_text"], args.timeout, api_key)
        if args.overwrite or (row["meta_review"] and not row["meta_review_zh"]):
            updates["meta_review_zh"] = translate_text(session, args.api_base, args.api_path, args.model, row["meta_review"], args.timeout, api_key)
        return row["paper_id"], row["ordinal"], row["title"], updates, ""
    except Exception as exc:
        return row["paper_id"], row["ordinal"], row["title"], updates, str(exc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--provider", choices=("local", "deepseek"), default="deepseek")
    parser.add_argument("--api-base")
    parser.add_argument("--api-path")
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--paper-id")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.provider == "deepseek":
        args.api_base = args.api_base or DEEPSEEK_API_BASE
        args.api_path = args.api_path or "/chat/completions"
        args.model = args.model or DEEPSEEK_MODEL
        api_key = os.environ.get(args.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key environment variable: {args.api_key_env}")
    else:
        args.api_base = args.api_base or API_BASE
        args.api_path = args.api_path or "/v1/chat/completions"
        args.model = args.model or DEFAULT_MODEL
        api_key = None
    session = requests.Session()
    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        ensure_columns(conn)
        where, params = [], []
        if args.paper_id:
            where.append("paper_id = ?")
            params.append(args.paper_id)
        if not args.overwrite:
            where.append("""
            ((length(coalesce(abstract,'')) > 0 AND length(coalesce(abstract_zh,'')) = 0)
            OR (length(coalesce(reviews_text,'')) > 0 AND length(coalesce(reviews_text_zh,'')) = 0)
            OR (length(coalesce(meta_review,'')) > 0 AND length(coalesce(meta_review_zh,'')) = 0))
            """)
        sql = "SELECT paper_id, ordinal, title, abstract, abstract_zh, reviews_text, reviews_text_zh, meta_review, meta_review_zh FROM papers"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ordinal"
        if args.limit:
            sql += " LIMIT ?"
            params.append(args.limit)
        rows = conn.execute(sql, params).fetchall()
        print(f"Need translation for {len(rows)} papers.", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_to_row = {executor.submit(translate_row, row, args, api_key): row for row in rows}
            for index, future in enumerate(as_completed(future_to_row), start=1):
                paper_id, ordinal, title, updates, error = future.result()
                if updates:
                    set_sql = ", ".join(f"{key} = ?" for key in updates)
                    conn.execute(f"UPDATE papers SET {set_sql}, updated_at = CURRENT_TIMESTAMP WHERE paper_id = ?", [*updates.values(), paper_id])
                    conn.commit()
                if error:
                    print(f"[{index}/{len(rows)}] failed {paper_id}: {error}", flush=True)
                else:
                    print(f"[{index}/{len(rows)}] translated {ordinal:04d} {paper_id} {title[:80]}", flush=True)
                if args.delay:
                    time.sleep(args.delay)


if __name__ == "__main__":
    main()
