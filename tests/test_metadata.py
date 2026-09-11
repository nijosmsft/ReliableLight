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


def test_manifest_discovery_classification_and_version() -> None:
    """Expose ReliableLight through normal integration discovery metadata."""
    manifest = json.loads(
        (ROOT / "custom_components" / "reliable_light" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert manifest["integration_type"] == "hub"
    assert manifest["single_config_entry"] is True
    assert manifest["version"] == "0.1.1"
    assert 'version = "0.1.1"' in project


def test_single_instance_abort_text_guides_existing_entry_configuration() -> None:
    """Tell users where to manage sources after singleton setup."""
    expected = (
        "ReliableLight is already configured. Manage source lights through "
        "Configure on the existing ReliableLight entry."
    )
    strings = json.loads(
        (ROOT / "custom_components" / "reliable_light" / "strings.json").read_text(
            encoding="utf-8"
        )
    )
    translation = json.loads(
        (
            ROOT / "custom_components" / "reliable_light" / "translations" / "en.json"
        ).read_text(encoding="utf-8")
    )
    assert strings["config"]["abort"]["single_instance_allowed"] == expected
    assert translation["config"]["abort"]["single_instance_allowed"] == expected
