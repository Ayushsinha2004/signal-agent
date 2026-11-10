from datetime import UTC, datetime, timedelta

from conftest import facts

from signal_agent.pipeline.rules import post_rules, pre_rules

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def test_pre_rules_pass_worldwide(example_profile):
    v = pre_rules("Acme | AI Engineer | REMOTE (worldwide) | LLM agents", NOW - timedelta(days=3),
                  example_profile.criteria, now=NOW)
    assert v.passed, v.reasons


def test_pre_rules_reject_old(example_profile):
    v = pre_rules("Acme | AI Engineer | REMOTE (worldwide)", NOW - timedelta(days=40), example_profile.criteria, now=NOW)
    assert not v.passed and "older than" in v.reasons[0]


def test_pre_rules_reject_region_locked(example_profile):
    v = pre_rules("Acme | AI Engineer | REMOTE (US) | Python", NOW, example_profile.criteria, now=NOW)
    assert any("region" in r for r in v.reasons)


def test_pre_rules_open_world_wins_over_region(example_profile):
    v = pre_rules("Acme | AI Engineer | Remote (US) or anywhere in the world", NOW, example_profile.criteria, now=NOW)
    assert v.passed, v.reasons


def test_pre_rules_reject_recruiter(example_profile):
    v = pre_rules("Recruiting for a client: AI engineer, remote worldwide", NOW, example_profile.criteria, now=NOW)
    assert any("recruiter" in r for r in v.reasons)


def test_pre_rules_reject_onsite(example_profile):
    v = pre_rules("Acme | ML Engineer | Onsite in Boston", NOW, example_profile.criteria, now=NOW)
    assert "on-site only" in v.reasons


def test_post_rules_pass(example_profile):
    assert post_rules(facts(), example_profile.criteria).passed


def test_post_rules_country_lock(example_profile):
    v = post_rules(facts(countries_allowed=["US", "CA"]), example_profile.criteria)
    assert not v.passed and "exclude IN" in v.reasons[0]


def test_post_rules_years_and_seniority(example_profile):
    v = post_rules(facts(years_min=8, seniority="staff_plus"), example_profile.criteria)
    assert len(v.reasons) == 2


def test_post_rules_flags_unknowns(example_profile):
    v = post_rules(facts(remote="unknown", countries_allowed=[]), example_profile.criteria)
    assert v.passed and "remote policy unclear" in v.flags and "countries not stated" in v.flags


def test_senior_roles_pass_as_stretch(example_profile):
    c = example_profile.criteria
    assert c.stretch_seniority == ["senior"] and c.stretch_max_years == 7
    v = post_rules(facts(seniority="senior", years_min=6), c)
    assert v.passed and v.stretch
    assert "stretch: senior role" in v.flags and "stretch: asks 6+ years" in v.flags
    assert not post_rules(facts(years_min=9), c).passed  # beyond the stretch range
    assert not post_rules(facts(seniority="staff_plus"), c).passed
    assert not post_rules(facts(seniority="mid"), c).stretch
    # without stretch settings, senior years are rejected as before
    plain = c.model_copy(update={"stretch_seniority": [], "stretch_max_years": None})
    assert not post_rules(facts(years_min=6), plain).passed and not post_rules(facts(seniority="senior"), plain).stretch
