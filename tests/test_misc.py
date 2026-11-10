import pytest

from signal_agent.collectors.hn import clean_html
from signal_agent.llm.pricing import cost_usd
from signal_agent.models import TRANSITIONS, State
from signal_agent.pipeline.runner import IllegalTransition, move, slug


def test_clean_html():
    raw = 'Acme | AI<p>Email <a href="mailto:a@b.co" rel="nofollow">a@b.co</a> &amp; apply'
    assert clean_html(raw) == "Acme | AI\nEmail mailto:a@b.co & apply"


@pytest.mark.parametrize("s,out", [("https://www.Acme.ai/jobs", "acme-ai"), ("Acme AI", "acme-ai"), (None, None)])
def test_slug(s, out):
    assert slug(s) == out


def test_every_state_has_transitions():
    assert set(TRANSITIONS) == set(State)


def test_illegal_transition_raises():
    with pytest.raises(IllegalTransition):
        move(None, 1, State.NEW, State.SENT)  # type: ignore[arg-type]


def test_pricing():
    assert cost_usd("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
    assert cost_usd("claude-opus-5-5", 0, 1_000_000) == pytest.approx(20.0)
