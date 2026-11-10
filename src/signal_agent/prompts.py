from __future__ import annotations

from functools import cache

from .config import REPO_ROOT


@cache
def prompt(name: str, version: str = "v1") -> str:
    return (REPO_ROOT / "prompts" / f"{name}.{version}.md").read_text()
