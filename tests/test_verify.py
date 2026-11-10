from conftest import POST, POST_URL, FakeLLM, brief, facts, good_draft

from signal_agent.models import Claim, JudgeScores
from signal_agent.pipeline.verify import check, verify


def _kw(profile, **over):
    kw = dict(facts=facts(), brief=brief(), evidence=profile.evidence_by_id(), post_text=POST, post_url=POST_URL,
              candidate_facts={"years_experience": "3"}, denylist=["SecretClient"])
    kw.update(over)
    return kw


def test_good_draft_passes(example_profile):
    grounded, problems = check(good_draft(), **_kw(example_profile))
    assert grounded == 1.0 and problems == []


def test_invented_number_is_caught(example_profile):
    d = good_draft()
    d.body = d.body.replace("12 production workflows", "40 production workflows")
    _, problems = check(d, **_kw(example_profile))
    assert any("number not found" in p and "40" in p for p in problems)


def test_uncited_claim_is_caught(example_profile):
    d = good_draft()
    d.claims.append(Claim(text="led a team of 10"))
    grounded, problems = check(d, **_kw(example_profile))
    assert grounded < 1.0 and any("uncited" in p for p in problems)


def test_unknown_evidence_id_is_caught(example_profile):
    d = good_draft()
    d.claims[1].evidence_id = "made-up"
    grounded, _ = check(d, **_kw(example_profile))
    assert grounded < 1.0


def test_style_rules(example_profile):
    d = good_draft()
    d.body = d.body.replace("I work remotely", "I am passionate — I work remotely")
    _, problems = check(d, **_kw(example_profile))
    assert any("dash" in p for p in problems) and any("passionate" in p for p in problems)


def test_denylist(example_profile):
    d = good_draft()
    d.body += " I built this for SecretClient."
    _, problems = check(d, **_kw(example_profile))
    assert any("denylisted" in p for p in problems)


def test_judge_low_score_fails(example_profile):
    llm = FakeLLM({JudgeScores: JudgeScores(relevance=2, specificity=2, tone=3, honesty=2, feedback="generic")})
    ver, _ = verify(llm, good_draft(), **_kw(example_profile))
    assert not ver.passed and any("judge" in p for p in ver.problems)


def test_judge_skipped_when_rules_fail(example_profile):
    llm = FakeLLM()
    d = good_draft()
    d.body += " —"
    ver, _ = verify(llm, d, **_kw(example_profile))
    assert ver.judge is None and "JudgeScores" not in llm.calls


def test_tidy_fixes_dashes_and_long_note_without_touching_claims():
    from conftest import good_draft

    from signal_agent.pipeline.stages import NOTE_MAX, tidy

    d = good_draft()
    d.subject = "Agentic Python Engineer – 306 PRs"
    d.body = d.body.replace("I work remotely", "I work remotely—from India—and")
    sentence = "I shipped 306 merged PRs in four months with agentic workflows. "
    d.connection_note = sentence * 5
    t = tidy(d)
    assert t.subject == "Agentic Python Engineer: 306 PRs"
    assert "—" not in t.body and "–" not in t.body and "remotely, from India, and" in t.body
    assert len(t.connection_note) <= NOTE_MAX and t.connection_note.endswith(".")
    assert t.connection_note.startswith(sentence.strip())  # cut at a sentence boundary, nothing rewritten
    assert t.claims == d.claims
    assert tidy(good_draft()) == good_draft()  # a clean draft is unchanged
