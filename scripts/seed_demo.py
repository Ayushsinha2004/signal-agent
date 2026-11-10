"""Seed a throwaway `signal_demo` database with fake-model leads, to look at the review UI without an API key.

    uv run python scripts/seed_demo.py
    SIGNAL_DATABASE_URL=postgresql://signal:signal-local-only@127.0.0.1:55433/signal_demo uv run signal serve

It refuses to touch any database whose name does not end in `_demo`, so the real one is never written to.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from conftest import POST, POST_URL, FakeLLM, facts, good_draft  # noqa: E402

from signal_agent.collectors import save_posts  # noqa: E402
from signal_agent.config import REPO_ROOT, get_settings  # noqa: E402
from signal_agent.db import connect, migrate  # noqa: E402
from signal_agent.models import DraftOut, Fit, RawPost  # noqa: E402
from signal_agent.pipeline.runner import create_leads, process, sync_profile  # noqa: E402
from signal_agent.profile import load_profile  # noqa: E402

DEMO = "signal_demo"


def main() -> None:
    base = get_settings().signal_database_url.rsplit("/", 1)[0]
    url = f"{base}/{DEMO}"
    assert url.endswith("_demo")
    with psycopg.connect(f"{base}/signal", autocommit=True) as admin:
        admin.execute(f"drop database if exists {DEMO}")
        admin.execute(f"create database {DEMO}")
    migrate(url)
    p = load_profile("example", root=REPO_ROOT / "profiles")
    now = datetime.now(UTC)
    posts = [
        ("1", POST, {}),
        ("2", POST.replace("Acme AI", "Birch Labs").replace("logistics", "insurance claims"),
         {"facts": facts(company="Birch Labs", company_domain="birchlabs.dev", roles=["AI Engineer"])}),
        ("3", POST.replace("Acme AI", "Cobalt"),
         {"facts": facts(company="Cobalt", company_domain="cobalt.io", roles=["Applied AI Engineer"]),
          "draft": "bad"}),
        ("6", POST.replace("Acme AI", "Elm Robotics").replace("Founding AI Engineer", "Senior AI Engineer"),
         {"facts": facts(company="Elm Robotics", company_domain="elm.dev", roles=["Senior AI Engineer"],
                         seniority="senior", years_min=6)}),
        ("4", "TalentCo | recruiting for our client | AI Engineer | REMOTE", {}),
        ("5", POST.replace("Acme AI", "Dune").replace("worldwide", "US only"),
         {"facts": facts(company="Dune", company_domain="dune.so", location_text="REMOTE (US only)",
                         countries_allowed=["US"])}),
    ]
    with connect(url) as conn:
        sync_profile(conn, p)
        save_posts(conn, [RawPost(source="hn", external_id=e, url=POST_URL, author="founder",
                                  posted_at=now - timedelta(days=int(e)), text=t) for e, t, _ in posts])
        create_leads(conn, p.id)
        for (_e, _t, opts), row in zip(posts, conn.execute("select id from leads order by id").fetchall(), strict=True):
            over: dict[type, object] = {}
            if "facts" in opts:
                over[type(opts["facts"])] = opts["facts"]
            if opts.get("draft") == "bad":
                bad: DraftOut = good_draft()
                bad.body = bad.body.replace("12 production", "40 production")
                over[DraftOut] = bad
            if "Dune" in _t:
                over[Fit] = Fit(role_family="ai_engineer", is_recruiter_or_agency_repost=False, is_direct_hire=True,
                                india_eligible="no", confidence=0.9, rationale="US only")
            state = process(conn, FakeLLM(over), p, row["id"])
            print(f"lead {row['id']}: {state}")
    print(f"\nSIGNAL_DATABASE_URL={url} uv run signal serve")


if __name__ == "__main__":
    main()
