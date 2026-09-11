"""Tests for safe pending-command persistence."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from homeassistant.components.light import ATTR_HS_COLOR, ATTR_RGB_COLOR
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.storage import Store

from custom_components.reliable_light.const import (
    DOMAIN,
    EVENT_COMMAND_STATUS,
    STORAGE_VERSION,
)
from custom_components.reliable_light.model import ReliableLightOptions
from custom_components.reliable_light.worker import ReliableLightWorker

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def persistent_options(expiry: int = 300) -> ReliableLightOptions:
    """Return persistent test options."""
    return ReliableLightOptions(
        retry_initial=0.01,
        retry_max=0.02,
        verification_delay=0,
        tolerance_name="normal",
        command_expiry=expiry,
        persistent_retry=True,
        diagnostic_attributes=True,
        emit_events=False,
    )


async def test_restores_unexpired_command_and_strips_transition(
    hass: HomeAssistant,
) -> None:
    """Restore safe steady-state intent only."""
    source = "registry-source"
    store = Store[dict](
        hass, STORAGE_VERSION, f"{DOMAIN}.entry.{source}", atomic_writes=True
    )
    now = time.time()
    await store.async_save(
        {
            "source_registry_id": source,
            "last_generation": 4,
            "pending": {
                "action": "turn_on",
                "kwargs": {"brightness": 100, "transition": 15},
                "accepted_at": now,
                "expires_at": now + 300,
            },
        }
    )
    hass.states.async_set("light.source", STATE_OFF)
    worker = ReliableLightWorker(
        hass,
        "entry",
        source,
        persistent_options(),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    await worker.async_initialize()
    assert worker.has_pending
    assert worker._current is not None
    assert "transition" not in worker._current.kwargs
    await worker.async_shutdown()
    stored = await store.async_load()
    assert stored is not None
    assert "transition" not in stored["pending"]["kwargs"]


async def test_discards_expired_command(hass: HomeAssistant) -> None:
    """Do not replay expired commands."""
    source = "expired-source"
    store = Store[dict](
        hass, STORAGE_VERSION, f"{DOMAIN}.entry.{source}", atomic_writes=True
    )
    now = time.time()
    await store.async_save(
        {
            "source_registry_id": source,
            "last_generation": 1,
            "pending": {
                "action": "turn_off",
                "kwargs": {},
                "accepted_at": now - 100,
                "expires_at": now - 1,
            },
        }
    )
    worker = ReliableLightWorker(
        hass,
        "entry",
        source,
        persistent_options(),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    await worker.async_initialize()
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_failed_readback_blocks_first_dispatch(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Do not dispatch until the pending revision is durably readable."""
    hass.states.async_set("light.source", STATE_OFF)
    calls = 0
    durable = False
    backing: dict | None = None

    async def save(payload: dict) -> None:
        nonlocal backing
        if durable:
            backing = deepcopy(payload)

    async def read() -> dict | None:
        return deepcopy(backing)

    async def turn_on(_call) -> None:
        nonlocal calls
        calls += 1
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = ReliableLightWorker(
        hass,
        "entry",
        "readback-source",
        persistent_options(),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    monkeypatch.setattr(worker._store, "async_save", save)
    monkeypatch.setattr(worker, "_async_read_persisted", read)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.04)
    assert calls == 0
    assert worker.has_pending
    assert worker._persisted_revision == 0

    durable = True
    worker._persist_wakeup.set()
    await asyncio.sleep(0.05)
    assert calls == 1
    assert not worker.has_pending
    assert backing is not None
    assert backing["pending"] is None
    await worker.async_shutdown()


async def test_failed_tombstone_is_not_reported_complete(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Wait for a confirmed tombstone before emitting command completion."""
    hass.states.async_set("light.source", STATE_OFF)
    backing: dict | None = None
    allow_tombstone = False
    statuses: list[dict] = []

    async def save(payload: dict) -> None:
        nonlocal backing
        if payload["pending"] is not None or allow_tombstone:
            backing = deepcopy(payload)

    async def read() -> dict | None:
        return deepcopy(backing)

    async def turn_on(_call) -> None:
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("light", "turn_on", turn_on)
    hass.bus.async_listen(
        EVENT_COMMAND_STATUS,
        lambda event: statuses.append(dict(event.data)),
    )
    worker = ReliableLightWorker(
        hass,
        "entry",
        "tombstone-source",
        replace(persistent_options(), emit_events=True),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    monkeypatch.setattr(worker._store, "async_save", save)
    monkeypatch.setattr(worker, "_async_read_persisted", read)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.04)
    assert backing is not None
    assert backing["pending"] is not None
    assert not any(status["status"] == "verified" for status in statuses)

    allow_tombstone = True
    worker._persist_wakeup.set()
    await asyncio.sleep(0.04)
    assert backing["pending"] is None
    assert any(status["status"] == "verified" for status in statuses)
    await worker.async_shutdown()


async def test_stale_persisted_command_is_preverified_before_replay(
    hass: HomeAssistant,
) -> None:
    """A stale command left by a failed tombstone does not repeat side effects."""
    source = "stale-tombstone-source"
    store = Store[dict](
        hass, STORAGE_VERSION, f"{DOMAIN}.entry.{source}", atomic_writes=True
    )
    now = time.time()
    await store.async_save(
        {
            "revision": 4,
            "source_registry_id": source,
            "last_generation": 4,
            "pending": {
                "action": "turn_on",
                "kwargs": {},
                "accepted_at": now,
                "expires_at": now + 300,
            },
        }
    )
    hass.states.async_set("light.source", STATE_ON)
    calls = 0

    async def turn_on(_call) -> None:
        nonlocal calls
        calls += 1

    hass.services.async_register("light", "turn_on", turn_on)
    worker = ReliableLightWorker(
        hass,
        "entry",
        source,
        persistent_options(),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    await worker.async_initialize()
    worker.start()
    await asyncio.sleep(0.04)
    assert calls == 0
    assert not worker.has_pending
    stored = await store.async_load()
    assert stored is not None
    assert stored["pending"] is None
    await worker.async_shutdown()


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        (ATTR_HS_COLOR, (120.0, 55.0)),
        (ATTR_RGB_COLOR, (12, 34, 56)),
    ],
)
async def test_color_tuple_persistence_confirms_before_dispatch(
    hass: HomeAssistant,
    attribute: str,
    value: tuple,
) -> None:
    """JSON sequence normalization must not block persistent color commands."""
    source = f"color-{attribute}"
    hass.states.async_set("light.source", STATE_OFF)
    called = asyncio.Event()
    captured: object | None = None

    async def turn_on(call) -> None:
        nonlocal captured
        captured = call.data[attribute]
        hass.states.async_set("light.source", STATE_ON, {attribute: value})
        called.set()

    hass.services.async_register("light", "turn_on", turn_on)
    worker = ReliableLightWorker(
        hass,
        "entry",
        source,
        persistent_options(),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    worker.start()
    await worker.async_submit("turn_on", {attribute: value}, None)
    await asyncio.wait_for(called.wait(), 1)

    assert captured == value
    assert isinstance(captured, tuple)
    await worker.async_shutdown()


@pytest.mark.parametrize(
    ("attribute", "stored_value", "expected"),
    [
        (ATTR_HS_COLOR, [210.0, 40.0], (210.0, 40.0)),
        (ATTR_RGB_COLOR, [4, 5, 6], (4, 5, 6)),
    ],
)
async def test_restored_color_lists_are_accepted_by_service_and_verification(
    hass: HomeAssistant,
    attribute: str,
    stored_value: list,
    expected: tuple,
) -> None:
    """Restored JSON lists remain valid light service inputs and verification data."""
    source = f"restore-{attribute}"
    store = Store[dict](
        hass, STORAGE_VERSION, f"{DOMAIN}.entry.{source}", atomic_writes=True
    )
    now = time.time()
    await store.async_save(
        {
            "revision": 1,
            "source_registry_id": source,
            "last_generation": 1,
            "pending": {
                "action": "turn_on",
                "kwargs": {attribute: stored_value},
                "accepted_at": now,
                "expires_at": now + 300,
            },
        }
    )
    hass.states.async_set("light.source", STATE_OFF)
    called = asyncio.Event()
    captured: object | None = None

    async def turn_on(call) -> None:
        nonlocal captured
        captured = call.data[attribute]
        hass.states.async_set("light.source", STATE_ON, {attribute: expected})
        called.set()

    hass.services.async_register("light", "turn_on", turn_on)
    worker = ReliableLightWorker(
        hass,
        "entry",
        source,
        persistent_options(),
        lambda: "light.source",
        lambda: None,
        set(),
    )
    await worker.async_initialize()
    worker.start()
    await asyncio.wait_for(called.wait(), 1)

    assert tuple(captured) == expected
    await asyncio.sleep(0.02)
    assert not worker.has_pending
    await worker.async_shutdown()
