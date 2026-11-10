"""Hacker News 'Ask HN: Who is hiring?' collector (public Algolia + Firebase APIs)."""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx

from ..models import RawPost

ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def latest_thread_id(client: httpx.Client) -> tuple[int, str]:
    r = client.get(ALGOLIA, params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
    r.raise_for_status()
    for hit in r.json()["hits"]:
        if hit["title"].lower().startswith("ask hn: who is hiring"):
            return int(hit["objectID"]), hit["title"]
    raise LookupError("no 'Who is hiring' thread found")


def clean_html(text: str) -> str:
    text = re.sub(r"<p>", "\n", text)
    text = re.sub(r'<a href="([^"]+)"[^>]*>[^<]*</a>', r"\1", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def to_post(item: dict[str, Any]) -> RawPost | None:
    if not item or item.get("deleted") or item.get("dead") or not item.get("text"):
        return None
    return RawPost(
        source="hn",
        external_id=str(item["id"]),
        url=f"https://news.ycombinator.com/item?id={item['id']}",
        author=item.get("by"),
        posted_at=datetime.fromtimestamp(item["time"], tz=UTC),
        text=clean_html(item["text"]),
    )


def collect(thread_id: int | None = None, limit: int | None = None) -> tuple[str, list[RawPost]]:
    with httpx.Client(timeout=20, headers={"User-Agent": "signal-agent/0.1"}) as client:
        title = f"thread {thread_id}"
        if thread_id is None:
            thread_id, title = latest_thread_id(client)
        story = client.get(ITEM.format(thread_id)).json()
        kids = story.get("kids", [])[:limit] if limit else story.get("kids", [])

        def fetch(i: int) -> dict[str, Any] | None:
            try:
                return client.get(ITEM.format(i)).json()
            except httpx.HTTPError:
                return None

        with ThreadPoolExecutor(16) as pool:
            items = list(pool.map(fetch, kids))
    posts = [p for p in (to_post(i) for i in items if i) if p]
    return title, posts
