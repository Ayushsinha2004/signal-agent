"""Public job-board APIs (Greenhouse, Lever, Ashby) for a curated list of companies in boards.yaml."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from ..models import RawPost
from .hn import clean_html

log = structlog.get_logger()


def greenhouse(client: httpx.Client, board: str) -> list[RawPost]:
    r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        text = f"{board} | {j['title']} | {j.get('location', {}).get('name', '')}\n{clean_html(j.get('content', ''))}"
        out.append(RawPost(source="greenhouse", external_id=f"{board}:{j['id']}", url=j.get("absolute_url"),
                           author=board, posted_at=_iso(j.get("updated_at")), text=text))
    return out


def lever(client: httpx.Client, company: str) -> list[RawPost]:
    r = client.get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"})
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories", {})
        text = f"{company} | {j['text']} | {cat.get('location', '')} | {j.get('workplaceType', '')}\n" \
               f"{j.get('descriptionPlain', '')}"
        posted = datetime.fromtimestamp(j["createdAt"] / 1000, tz=UTC) if j.get("createdAt") else None
        out.append(RawPost(source="lever", external_id=f"{company}:{j['id']}", url=j.get("hostedUrl"),
                           author=company, posted_at=posted, text=text))
    return out


def ashby(client: httpx.Client, org: str) -> list[RawPost]:
    r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}", params={"includeCompensation": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        text = f"{org} | {j['title']} | {j.get('location', '')} | remote={j.get('isRemote')}\n" \
               f"{j.get('descriptionPlain', '')}"
        out.append(RawPost(source="ashby", external_id=f"{org}:{j['id']}", url=j.get("jobUrl"),
                           author=org, posted_at=_iso(j.get("publishedAt")), text=text))
    return out


def _iso(s: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def collect(boards: dict[str, list[str]]) -> list[RawPost]:
    posts: list[RawPost] = []
    with httpx.Client(timeout=20, headers={"User-Agent": "signal-agent/0.1"}) as client:
        for kind, names in boards.items():
            for name in names:
                try:
                    posts += FETCHERS[kind](client, name)
                except httpx.HTTPError as e:  # one dead board must not stop the run, but say which one
                    log.warning("board_fetch_failed", kind=kind, board=name, error=type(e).__name__)
    return posts
