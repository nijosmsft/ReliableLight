"""Tests for repository and compatibility metadata."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.helpers.device import async_entity_id_to_device

ROOT = Path(__file__).parents[1]


def test_declared_minimum_matches_tested_home_assistant() -> None:
    """Keep the HACS minimum aligned with the pinned test runtime."""
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    requirements = (ROOT / "requirements_test.txt").read_text(encoding="utf-8")
    assert hacs["homeassistant"] == "2026.9.0"
    assert "homeassistant==2026.9.0" in requirements
    assert callable(async_entity_id_to_device)
