"""`signal` command line."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import structlog
import typer
import yaml

from .collectors import ats, csv_import, hn, save_posts
from .config import get_settings
from .db import connect
from .db import migrate as run_migrations

app = typer.Typer(no_args_is_help=True, add_completion=False)
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def _llm():  # type: ignore[no-untyped-def]
    from .llm.factory import make_llm

    return make_llm()


@app.command()
def migrate() -> None:
    """Apply new SQL migrations."""
    applied = run_migrations()
    typer.echo(f"applied: {applied or 'nothing new'}")


@app.command()
def collect(source: str = typer.Option("hn", help="hn | ats | csv"),
            path: Path | None = typer.Option(None, help="CSV file for --source csv"),
            thread: int | None = typer.Option(None, help="HN thread id (default: latest Who is hiring)")) -> None:
    """Fetch posts into raw_posts (idempotent)."""
    if source == "hn":
        title, posts = hn.collect(thread)
        typer.echo(f"{title}: {len(posts)} posts")
    elif source == "csv":
        if not path:
            raise typer.BadParameter("--path is required for csv")
        posts = csv_import.collect(path)
    elif source == "ats":
        boards = yaml.safe_load((get_settings().profile_dir / "boards.yaml").read_text())
        posts = ats.collect(boards)
    else:
        raise typer.BadParameter(source)
    with connect() as conn:
        new = save_posts(conn, posts)
    typer.echo(f"stored {new} new posts ({len(posts) - new} already known)")


@app.command()
def run(limit: int = typer.Option(25, help="max leads to advance this run"),
        channel: str = typer.Option("inmail", help="inmail | email"),
        lead: list[int] = typer.Option([], help="advance only these lead ids (repeatable)")) -> None:
    """Create leads for new posts and advance them through the pipeline.

    Leads furthest along go first, so a small daily model budget turns into finished drafts. When the
    provider's limit is reached the run stops cleanly; run it again later and it continues where it left off.
    """
    from .llm import QuotaExhausted
    from .observability import flush
    from .pipeline.runner import ACTIVE_ORDER, create_leads, process, sync_profile
    from .profile import load_profile

    p = load_profile()
    llm = _llm()
    counts: dict[str, int] = {}
    stopped = None
    with connect() as conn:
        sync_profile(conn, p)
        typer.echo(f"new leads: {create_leads(conn, p.id)}")
        ids = [r["id"] for r in conn.execute(
            "select id from leads where profile_id = %s and state = any(%s) and (%s or id = any(%s)) "
            "order by array_position(%s, state), id limit %s",
            (p.id, ACTIVE_ORDER, not lead, lead, ACTIVE_ORDER, limit)).fetchall()]
        for i in ids:
            try:
                s = process(conn, llm, p, i, channel=channel)
            except QuotaExhausted as e:
                stopped = str(e)
                break
            counts[s.value] = counts.get(s.value, 0) + 1
    flush()  # send any buffered Langfuse spans before the process exits (no-op without keys)
    cache = f" (cache: {llm.hits} hits, {llm.misses} misses)" if hasattr(llm, "hits") else ""
    typer.echo(f"advanced {sum(counts.values())} of {len(ids)} leads: {counts}{cache}")
    if stopped:
        typer.echo(f"paused: {stopped}\nrun `signal run` again after the limit resets; finished stages are kept.")


@app.command()
def reverify(lead: list[int] = typer.Option(..., help="lead ids in REVIEW (repeatable)")) -> None:
    """Re-tidy and re-verify the latest draft of leads in REVIEW, without redrafting (costs one judge call each)."""
    from .llm import QuotaExhausted
    from .models import CompanyBrief, DraftOut, PostFacts
    from .pipeline.runner import save_draft
    from .pipeline.stages import tidy
    from .pipeline.verify import verify
    from .profile import load_profile
    from .tracing import step

    p = load_profile()
    llm = _llm()
    with connect() as conn:
        for lid in lead:
            row = conn.execute("""select l.state, l.facts, l.brief, r.text, r.url, d.version, d.channel, d.subject,
                                         d.body, d.connection_note, d.claims
                                  from leads l join raw_posts r on r.id = l.raw_post_id
                                  join lateral (select * from drafts where lead_id = l.id order by version desc limit 1)
                                    d on true where l.id = %s""", (lid,)).fetchone()
            if row is None or row["state"] != "REVIEW":
                typer.echo(f"lead {lid}: skipped (not in REVIEW with a draft)")
                continue
            d = tidy(DraftOut(channel=row["channel"], subject=row["subject"] or "", body=row["body"],
                              connection_note=row["connection_note"] or "", claims=row["claims"]))
            try:
                with step(conn, lid, "reverify") as rec:
                    ver, rec.usage = verify(llm, d, facts=PostFacts(**row["facts"]), brief=CompanyBrief(**row["brief"]),
                                            evidence=p.evidence_by_id(), post_text=row["text"],
                                            post_url=row["url"], denylist=p.denylist,
                                            candidate_facts={k: str(v) for k, v in p.facts.items()})
                    rec.detail = ver.model_dump()
            except QuotaExhausted as e:
                typer.echo(f"paused: {e}\nrun `signal reverify` again after the limit resets.")
                return
            save_draft(conn, lid, row["version"] + 1, d, ver)
            conn.execute("update leads set reject_reason = %s, updated_at = now() where id = %s",
                         (None if ver.passed else "draft failed verification: edit by hand", lid))
            conn.commit()
            typer.echo(f"lead {lid}: draft v{row['version'] + 1} passed={ver.passed} problems={ver.problems}")


@app.command()
def funnel() -> None:
    """Lead counts by state, plus total and per-lead cost."""
    with connect() as conn:
        for r in conn.execute("select state, n from v_funnel order by n desc").fetchall():
            typer.echo(f"{r['state']:<12} {r['n']}")
        c = conn.execute("select count(*) n, coalesce(sum(cost_usd),0) total from step_runs").fetchone()
        assert c is not None
        typer.echo(f"\nLLM spend: ${float(c['total']):.4f} over {c['n']} steps")


@app.command()
def export(out: Path = typer.Option(Path("review.csv"))) -> None:
    """Export leads waiting for review with their latest draft."""
    with connect() as conn:
        rows = conn.execute("""
            select l.id, l.facts->>'company' company, r.url, l.reject_reason note, d.subject, d.body,
                   d.connection_note, d.verification->>'passed' verified
            from leads l join raw_posts r on r.id = l.raw_post_id
            left join lateral (select * from drafts where lead_id = l.id order by version desc limit 1) d on true
            where l.state = 'REVIEW' order by l.id""").fetchall()
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["id"])
        w.writeheader()
        w.writerows(rows)
    typer.echo(f"wrote {len(rows)} leads to {out}")


@app.command()
def serve(port: int = typer.Option(8765),
          host: str = typer.Option("127.0.0.1", help="0.0.0.0 inside a container")) -> None:
    """Run the review UI (localhost by default)."""
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1") and not get_settings().signal_ui_password:
        raise typer.BadParameter("set SIGNAL_UI_PASSWORD before serving on a non-local address", param_hint="--host")
    uvicorn.run("signal_agent.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
