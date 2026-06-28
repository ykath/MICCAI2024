#!/usr/bin/env python3
"""Build a local SQLite database for a conference paper page.

Customize parse_main_page() and parse_detail_page() for the target site.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from lxml import html


BASE_URL = "https://papers.miccai.org/miccai-2024/"
SLUG = "miccai2024"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache" / "html"
DB_PATH = DATA_DIR / f"{SLUG}.sqlite"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def fetch_text(session: requests.Session, url: str, cache_path: Path, refresh: bool = False) -> str:
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            text = response.content.decode("utf-8", errors="replace")
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 10))
    else:
        raise RuntimeError(f"failed to fetch {url}") from last_error
    cache_path.write_text(text, encoding="utf-8")
    return text


def section_after(root: html.HtmlElement, heading_id: str, stop_tags: tuple[str, ...] = ("h1",)) -> list[html.HtmlElement]:
    heading = root.xpath(f'//*[@id="{heading_id}"]')
    if not heading:
        return []
    nodes = []
    for sibling in heading[0].itersiblings():
        if sibling.tag in stop_tags:
            break
        nodes.append(sibling)
    return nodes


def section_text(root: html.HtmlElement, heading_id: str, stop_tags: tuple[str, ...] = ("h1",)) -> str:
    return "\n\n".join(clean_text(node.text_content()) for node in section_after(root, heading_id, stop_tags) if clean_text(node.text_content()))


def links_in(nodes: list[html.HtmlElement]) -> list[str]:
    found = []
    for node in nodes:
        for href in node.xpath(".//a/@href"):
            absolute = urljoin(BASE_URL, href)
            if absolute not in found:
                found.append(absolute)
    return found


def parse_bibtex_value(bibtex: str, key: str) -> str:
    match = re.search(rf"{re.escape(key)}\s*=\s*\{{\s*(.*?)\s*\}}\s*,?", bibtex, flags=re.I | re.S)
    if not match:
        return ""
    value = match.group(1)
    if key.lower() == "title":
        value = re.sub(r"^\{\s*|\s*\}$", "", value.strip())
    return clean_text(value)


def parse_main_page(page: str) -> list[dict]:
    """Return paper dicts from the listing page.

    Expected keys:
    ordinal, paper_id, title, authors, authors_text, pdf_url, info_url.

    This default parser handles MICCAI-style pages with <li>, PDF links,
    Paper Information links, and BibTeX <pre> blocks. Replace it for other
    conference sites.
    """
    root = html.fromstring(page)
    papers = []
    items = root.xpath('//li[.//a[normalize-space(.)="PDF"] and not(.//li//a[normalize-space(.)="PDF"])]')
    for idx, li in enumerate(items, start=1):
        pdf_links = li.xpath('.//a[normalize-space(.)="PDF"]/@href')
        info_links = li.xpath('.//a[contains(normalize-space(.), "Paper Information") or contains(normalize-space(.), "Info")]/@href')
        pre_nodes = li.xpath(".//pre")
        if not pdf_links:
            continue
        bibtex = clean_text(pre_nodes[0].text_content()) if pre_nodes else ""
        title = parse_bibtex_value(bibtex, "title") if bibtex else clean_text(li.xpath("string(.//strong|.//b|.//h3|.//h2)"))
        authors_text = parse_bibtex_value(bibtex, "author") if bibtex else ""
        authors = [clean_text(part) for part in re.split(r"\s+and\s+|;\s*", authors_text, flags=re.I) if clean_text(part)]
        pdf_url = urljoin(BASE_URL, pdf_links[0])
        info_url = urljoin(BASE_URL, info_links[0]) if info_links else pdf_url
        paper_id_match = re.search(r"(\d+|[A-Za-z0-9_-]+)(?:_paper)?\.pdf", pdf_url)
        paper_id = paper_id_match.group(1) if paper_id_match else f"{idx:04d}"
        papers.append({
            "ordinal": idx,
            "paper_id": paper_id,
            "title": title or f"Paper {idx}",
            "authors": authors,
            "authors_text": "; ".join(authors) if authors else authors_text,
            "pdf_url": pdf_url,
            "info_url": info_url,
            "bibtex": bibtex,
            "booktitle": parse_bibtex_value(bibtex, "booktitle"),
            "year": parse_bibtex_value(bibtex, "year"),
            "publisher": parse_bibtex_value(bibtex, "publisher"),
            "volume": parse_bibtex_value(bibtex, "volume"),
            "month": parse_bibtex_value(bibtex, "month"),
            "pages": parse_bibtex_value(bibtex, "pages"),
        })
    return papers


def parse_detail_page(page: str) -> dict:
    """Parse per-paper detail page. Customize for the target site."""
    root = html.fromstring(page)
    link_nodes = section_after(root, "link-id")
    code_nodes = section_after(root, "code-id")
    dataset_nodes = section_after(root, "dataset-id")
    categories = [
        clean_text(a.text_content())
        for a in root.xpath('//a[contains(@href, "categories#") or contains(@href, "/categories#")]')
        if clean_text(a.text_content())
    ]
    link_text = section_text(root, "link-id")
    doi_urls = [u for u in links_in(link_nodes) if "doi.org" in u]
    sharedit_urls = [u for u in links_in(link_nodes) if "rdcu.be" in u]
    supplementary_urls = [u for u in links_in(link_nodes) if "/supp/" in u or "supplement" in u.lower()]
    abstract = section_text(root, "abstract-id")
    if not abstract:
        meta_desc = root.xpath('string(//meta[@name="description"]/@content | //meta[@property="og:description"]/@content)')
        abstract = clean_text(meta_desc)
    return {
        "abstract": abstract,
        "links_text": link_text,
        "sharedit_url": sharedit_urls[0] if sharedit_urls else "",
        "doi_url": doi_urls[0] if doi_urls else "",
        "supplementary": supplementary_urls[0] if supplementary_urls else "",
        "code_urls": links_in(code_nodes),
        "dataset_urls": links_in(dataset_nodes),
        "categories": categories,
        "reviews_text": section_text(root, "review-id"),
        "author_feedback": section_text(root, "authorFeedback-id"),
        "meta_review": section_text(root, "metareview-id") or section_text(root, "meta-review-id"),
    }


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS papers (
  id INTEGER PRIMARY KEY,
  ordinal INTEGER NOT NULL,
  paper_id TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  authors_text TEXT,
  abstract TEXT,
  abstract_zh TEXT,
  pdf_url TEXT,
  info_url TEXT,
  sharedit_url TEXT,
  doi_url TEXT,
  supplementary TEXT,
  code_urls TEXT,
  dataset_urls TEXT,
  categories TEXT,
  links_text TEXT,
  bibtex TEXT,
  booktitle TEXT,
  year TEXT,
  publisher TEXT,
  volume TEXT,
  month TEXT,
  pages TEXT,
  reviews_text TEXT,
  reviews_text_zh TEXT,
  author_feedback TEXT,
  meta_review TEXT,
  meta_review_zh TEXT,
  local_pdf_path TEXT,
  downloaded INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS authors (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS paper_authors (
  paper_id TEXT NOT NULL, author_id INTEGER NOT NULL, author_order INTEGER NOT NULL,
  PRIMARY KEY (paper_id, author_id)
);
CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS paper_categories (
  paper_id TEXT NOT NULL, category_id INTEGER NOT NULL,
  PRIMARY KEY (paper_id, category_id)
);
"""


def recreate_database(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    DROP TABLE IF EXISTS paper_categories;
    DROP TABLE IF EXISTS categories;
    DROP TABLE IF EXISTS paper_authors;
    DROP TABLE IF EXISTS authors;
    DROP TABLE IF EXISTS papers;
    """)
    conn.executescript(SCHEMA)


def upsert_lookup(conn: sqlite3.Connection, table: str, name: str) -> int:
    conn.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (name,))
    return int(conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()[0])


def save_papers(conn: sqlite3.Connection, papers: list[dict]) -> None:
    for paper in papers:
        conn.execute("""
        INSERT INTO papers (
          ordinal, paper_id, title, authors_text, abstract, pdf_url, info_url,
          sharedit_url, doi_url, supplementary, code_urls, dataset_urls, categories,
          links_text, bibtex, booktitle, year, publisher, volume, month, pages,
          reviews_text, author_feedback, meta_review, updated_at
        ) VALUES (
          :ordinal, :paper_id, :title, :authors_text, :abstract, :pdf_url, :info_url,
          :sharedit_url, :doi_url, :supplementary, :code_urls, :dataset_urls, :categories,
          :links_text, :bibtex, :booktitle, :year, :publisher, :volume, :month, :pages,
          :reviews_text, :author_feedback, :meta_review, CURRENT_TIMESTAMP
        )
        ON CONFLICT(paper_id) DO UPDATE SET
          title=excluded.title, authors_text=excluded.authors_text, abstract=excluded.abstract,
          pdf_url=excluded.pdf_url, info_url=excluded.info_url, sharedit_url=excluded.sharedit_url,
          doi_url=excluded.doi_url, supplementary=excluded.supplementary, code_urls=excluded.code_urls,
          dataset_urls=excluded.dataset_urls, categories=excluded.categories, links_text=excluded.links_text,
          bibtex=excluded.bibtex, booktitle=excluded.booktitle, year=excluded.year,
          publisher=excluded.publisher, volume=excluded.volume, month=excluded.month, pages=excluded.pages,
          reviews_text=excluded.reviews_text, author_feedback=excluded.author_feedback,
          meta_review=excluded.meta_review, updated_at=CURRENT_TIMESTAMP
        """, paper)
        conn.execute("DELETE FROM paper_authors WHERE paper_id = ?", (paper["paper_id"],))
        for order, author in enumerate(paper.get("authors", []), start=1):
            author_id = upsert_lookup(conn, "authors", author)
            conn.execute("INSERT OR IGNORE INTO paper_authors VALUES (?, ?, ?)", (paper["paper_id"], author_id, order))
        conn.execute("DELETE FROM paper_categories WHERE paper_id = ?", (paper["paper_id"],))
        for category in paper.get("category_list", []):
            category_id = upsert_lookup(conn, "categories", category)
            conn.execute("INSERT OR IGNORE INTO paper_categories VALUES (?, ?)", (paper["paper_id"], category_id))
    conn.commit()


def build_database(refresh: bool = False, limit: int | None = None, delay: float = 0.05) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": f"{SLUG}-local-paper-browser/1.0"})
    main_html = fetch_text(session, BASE_URL, CACHE_DIR / "index.html", refresh=refresh)
    papers = parse_main_page(main_html)
    if limit:
        papers = papers[:limit]
    print(f"Found {len(papers)} papers.")
    for idx, paper in enumerate(papers, start=1):
        cache_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", paper["info_url"].rstrip("/").split("/")[-1] or paper["paper_id"])
        detail_html = fetch_text(session, paper["info_url"], CACHE_DIR / cache_name, refresh=refresh)
        detail = parse_detail_page(detail_html)
        paper.update(detail)
        paper["code_urls"] = json.dumps(detail["code_urls"], ensure_ascii=False)
        paper["dataset_urls"] = json.dumps(detail["dataset_urls"], ensure_ascii=False)
        paper["category_list"] = detail["categories"]
        paper["categories"] = json.dumps(detail["categories"], ensure_ascii=False)
        if idx % 25 == 0 or idx == len(papers):
            print(f"Parsed {idx}/{len(papers)}")
        if delay:
            time.sleep(delay)
    with sqlite3.connect(DB_PATH) as conn:
        recreate_database(conn)
        save_papers(conn, papers)
        print("integrity", conn.execute("pragma integrity_check").fetchone()[0])
        print(f"Saved {conn.execute('select count(*) from papers').fetchone()[0]} papers to {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=0.05)
    args = parser.parse_args()
    build_database(args.refresh, args.limit, args.delay)


if __name__ == "__main__":
    main()
