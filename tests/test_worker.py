"""Tests for the independent ReliableLight command worker."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.reliable_light.const import EVENT_COMMAND_STATUS
from custom_components.reliable_light.model import ReliableLightOptions
from custom_components.reliable_light.worker import ReliableLightWorker

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant


def options(
    *,
    expiry: float = 0,
    persistent: bool = False,
    emit_events: bool = False,
    retry_initial: float = 0.01,
    retry_max: float = 0.02,
) -> ReliableLightOptions:
    """Return fast test options."""
    return ReliableLightOptions(
        retry_initial=retry_initial,
        retry_max=retry_max,
        verification_delay=0,
        tolerance_name="normal",
        command_expiry=expiry,
        persistent_retry=persistent,
        diagnostic_attributes=True,
        emit_events=emit_events,
    )


def make_worker(
    hass: HomeAssistant,
    source_id: str,
    changed: Callable[[], None] = lambda: None,
    worker_options: ReliableLightOptions | None = None,
) -> ReliableLightWorker:
    """Create a worker."""
    return ReliableLightWorker(
        hass,
        "entry",
        source_id,
        worker_options or options(),
        lambda: source_id,
        changed,
        set(),
    )


async def test_equivalent_commands_coalesce(hass: HomeAssistant) -> None:
    """Equivalent pending commands do not create a generation."""
    hass.states.async_set("light.source", STATE_OFF)
    worker = make_worker(hass, "light.source")
    assert not await worker.async_submit("turn_on", {ATTR_BRIGHTNESS: 100}, None)
    generation = worker._generation
    assert await worker.async_submit("turn_on", {ATTR_BRIGHTNESS: 100}, None)
    assert worker._generation == generation
    await worker.async_shutdown()


async def test_newer_off_supersedes_retrying_on(hass: HomeAssistant) -> None:
    """Never retry an obsolete turn-on generation."""
    hass.states.async_set("light.source", STATE_OFF)
    calls: list[str] = []

    async def turn_on(_call) -> None:
        calls.append("turn_on")
        msg = "offline"
        raise HomeAssistantError(msg)

    async def turn_off(_call) -> None:
        calls.append("turn_off")
        hass.states.async_set("light.source", STATE_OFF)

    worker = make_worker(hass, "light.source")
    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("light", "turn_off", turn_off)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await worker.async_submit("turn_off", {}, None)
    await asyncio.sleep(0.05)
    assert calls.count("turn_on") == 1
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_independent_workers(hass: HomeAssistant) -> None:
    """A blocked source does not block another proxy."""
    hass.states.async_set("light.one", STATE_OFF)
    hass.states.async_set("light.two", STATE_OFF)
    gate = asyncio.Event()
    completed_two = asyncio.Event()

    async def turn_on(call) -> None:
        entity_id = call.data["entity_id"]
        if entity_id == "light.one":
            await gate.wait()
        else:
            hass.states.async_set(entity_id, STATE_ON)
            completed_two.set()

    first = make_worker(hass, "light.one")
    second = make_worker(hass, "light.two")
    hass.services.async_register("light", "turn_on", turn_on)
    first.start()
    second.start()
    await first.async_submit("turn_on", {}, None)
    await second.async_submit("turn_on", {}, None)
    await asyncio.wait_for(completed_two.wait(), 1)
    await asyncio.sleep(0)
    gate.set()
    await first.async_shutdown()
    await second.async_shutdown()


async def test_validation_error_is_terminal(hass: HomeAssistant) -> None:
    """Do not retry invalid source service data."""
    hass.states.async_set("light.source", STATE_OFF)

    async def turn_on(_call) -> None:
        msg = "invalid"
        raise ServiceValidationError(msg)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_worker(hass, "light.source")
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.02)
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "invalid"
    await worker.async_shutdown()


async def test_unexpected_error_marks_worker_failed(hass: HomeAssistant) -> None:
    """Surface programming failures instead of retrying them."""
    hass.states.async_set("light.source", STATE_OFF)

    async def turn_on(_call) -> None:
        msg = "programming failure"
        raise RuntimeError(msg)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_worker(hass, "light.source")
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.02)
    assert worker.failed
    assert worker.diagnostics()["last_result"] == "worker_failed"
    await worker.async_shutdown()


async def test_service_failure_retries_until_verified(hass: HomeAssistant) -> None:
    """Retry HomeAssistantError and finish after observed state matches."""
    hass.states.async_set("light.source", STATE_OFF)
    calls = 0

    async def turn_on(_call) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "offline"
            raise HomeAssistantError(msg)
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_worker(hass, "light.source")
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.05)
    assert calls >= 2
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "verified"
    await worker.async_shutdown()


async def test_user_context_propagates_to_source_call(hass: HomeAssistant) -> None:
    """Preserve user identity while creating a distinct child context."""
    hass.states.async_set("light.source", STATE_OFF)
    captured: Context | None = None

    async def turn_on(call) -> None:
        nonlocal captured
        captured = call.context
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("light", "turn_on", turn_on)
    initiating = Context(user_id="user-123")
    worker = make_worker(hass, "light.source")
    worker.start()
    await worker.async_submit("turn_on", {}, initiating)
    await asyncio.sleep(0.02)
    assert captured is not None
    assert captured.user_id == initiating.user_id
    assert captured.parent_id == initiating.id
    assert captured.id != initiating.id
    await worker.async_shutdown()


def test_backoff_saturates_before_exponentiation(hass: HomeAssistant) -> None:
    """Very high attempt counts cannot overflow exponential backoff."""
    worker = make_worker(
        hass,
        "light.source",
        worker_options=options(retry_initial=0.1, retry_max=60),
    )
    delay = worker._backoff_delay(100_000)
    assert 0 <= delay <= 60


async def test_expired_equivalent_command_does_not_coalesce(
    hass: HomeAssistant,
) -> None:
    """A fresh equivalent request replaces an expired pending generation."""
    worker = make_worker(
        hass,
        "light.source",
        worker_options=options(expiry=0.01),
    )
    assert not await worker.async_submit("turn_on", {}, None)
    old_generation = worker._generation
    await asyncio.sleep(0.02)
    assert not await worker.async_submit("turn_on", {}, None)
    assert worker._generation == old_generation + 1
    await worker.async_shutdown()


async def test_expiry_caps_long_backoff(hass: HomeAssistant) -> None:
    """Expiry wakes and clears a command before a long retry delay."""
    hass.states.async_set("light.source", STATE_OFF)

    async def turn_on(_call) -> None:
        msg = "offline"
        raise HomeAssistantError(msg)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_worker(
        hass,
        "light.source",
        worker_options=options(expiry=0.02, retry_initial=60, retry_max=60),
    )
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.06)
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "expired"
    await worker.async_shutdown()


async def test_completion_event_keeps_completed_generation_on_supersession(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A completion event cannot borrow a newer command's generation."""
    events: list[dict] = []
    hass.bus.async_listen(
        EVENT_COMMAND_STATUS,
        lambda event: events.append(dict(event.data)),
    )
    worker = make_worker(
        hass,
        "light.source",
        worker_options=options(emit_events=True),
    )
    await worker.async_submit("turn_on", {}, None)
    completed_generation = worker._generation
    finish_waiting = asyncio.Event()
    release_finish = asyncio.Event()

    async def wait_persisted(_revision: int) -> None:
        finish_waiting.set()
        await release_finish.wait()

    monkeypatch.setattr(worker, "_wait_persisted", wait_persisted)
    finish_task = asyncio.create_task(
        worker._async_finish(completed_generation, "verified")
    )
    await finish_waiting.wait()
    await worker.async_submit("turn_off", {}, None)
    newer_generation = worker._generation
    release_finish.set()
    await finish_task

    completed_event = next(event for event in events if event["status"] == "verified")
    assert completed_event["generation"] == completed_generation
    assert completed_event["action"] == "turn_on"
    assert completed_event["generation"] != newer_generation
    await worker.async_shutdown()
