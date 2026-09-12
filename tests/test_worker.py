"""Tests for the independent ReliableLight command worker."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
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


def make_power_worker(
    hass: HomeAssistant,
    *,
    source_id: str = "light.source",
    power_id: str = "switch.power",
    power_domain: str = "switch",
    worker_options: ReliableLightOptions | None = None,
) -> ReliableLightWorker:
    """Create a worker with an upstream power dependency."""
    return ReliableLightWorker(
        hass,
        "entry",
        "source-registry-id",
        worker_options or options(),
        lambda: source_id,
        lambda: None,
        set(),
        power_registry_id="power-registry-id",
        power_entity=lambda: (power_domain, power_id),
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


async def test_unexpected_error_outside_service_marks_worker_failed(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Surface programming failures outside the source service boundary."""
    hass.states.async_set("light.source", STATE_OFF)

    def broken_source_state() -> None:
        msg = "programming failure"
        raise RuntimeError(msg)

    worker = make_worker(hass, "light.source")
    monkeypatch.setattr(worker, "_source_state", broken_source_state)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.02)
    assert worker.failed
    assert worker.diagnostics()["last_result"] == "worker_failed"
    await worker.async_shutdown()


async def test_source_runtime_exception_retries_and_recovers(
    hass: HomeAssistant, caplog
) -> None:
    """Retry arbitrary source runtime failures without failing the worker."""
    hass.states.async_set("light.source", STATE_OFF)
    calls = 0

    async def turn_on(_call) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            msg = "sensitive source details"
            raise RuntimeError(msg)
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_worker(hass, "light.source")
    with caplog.at_level(logging.WARNING):
        worker.start()
        await worker.async_submit("turn_on", {}, None)
        await asyncio.sleep(0.08)

    assert calls >= 3
    assert not worker.failed
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "verified"
    matching_logs = [
        record
        for record in caplog.records
        if "Source service call failed for registry id" in record.message
    ]
    assert len(matching_logs) == 1
    assert "RuntimeError" in matching_logs[0].message
    assert "sensitive source details" not in caplog.text
    await worker.async_shutdown()


async def test_source_runtime_failure_respects_inflight_supersession(
    hass: HomeAssistant,
) -> None:
    """Do not retry an old generation replaced during a failing source call."""
    hass.states.async_set("light.source", STATE_OFF)
    started = asyncio.Event()
    release = asyncio.Event()
    turn_on_calls = 0

    async def turn_on(_call) -> None:
        nonlocal turn_on_calls
        turn_on_calls += 1
        started.set()
        await release.wait()
        msg = "source runtime failure"
        raise RuntimeError(msg)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_worker(hass, "light.source")
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.wait_for(started.wait(), 1)
    old_generation = worker._generation
    await worker.async_submit("turn_off", {}, None)
    assert worker._generation == old_generation + 1
    release.set()
    await asyncio.sleep(0.04)

    assert turn_on_calls == 1
    assert not worker.failed
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "verified"
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


async def test_switch_power_turn_on_orders_power_before_source(
    hass: HomeAssistant,
) -> None:
    """Power is verified before the source receives its original kwargs."""
    hass.states.async_set("switch.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_UNAVAILABLE)
    calls: list[tuple[str, str, dict]] = []

    async def power_on(call) -> None:
        calls.append(("switch", "turn_on", dict(call.data)))
        hass.states.async_set("switch.power", STATE_ON)
        hass.states.async_set("light.source", STATE_OFF)

    async def source_on(call) -> None:
        calls.append(("light", "turn_on", dict(call.data)))
        hass.states.async_set(
            "light.source",
            STATE_ON,
            {ATTR_BRIGHTNESS: call.data[ATTR_BRIGHTNESS]},
        )

    hass.services.async_register("switch", "turn_on", power_on)
    hass.services.async_register("light", "turn_on", source_on)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_on", {ATTR_BRIGHTNESS: 123}, None)
    await asyncio.sleep(0.05)

    assert [(domain, action) for domain, action, _data in calls] == [
        ("switch", "turn_on"),
        ("light", "turn_on"),
    ]
    assert calls[1][2][ATTR_BRIGHTNESS] == 123
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_light_power_dependency_uses_light_domain(
    hass: HomeAssistant,
) -> None:
    """Registered light dependencies are commanded through the light domain."""
    hass.states.async_set("light.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_OFF)
    calls: list[str] = []

    async def turn_on(call) -> None:
        entity_id = call.data["entity_id"]
        calls.append(entity_id)
        hass.states.async_set(entity_id, STATE_ON)

    hass.services.async_register("light", "turn_on", turn_on)
    worker = make_power_worker(hass, power_id="light.power", power_domain="light")
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.04)

    assert calls == ["light.power", "light.source"]
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_turn_on_waits_for_delayed_source_availability(
    hass: HomeAssistant,
) -> None:
    """Do not command an unavailable source until power makes it available."""
    hass.states.async_set("switch.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_UNAVAILABLE)
    source_calls = 0

    async def power_on(_call) -> None:
        hass.states.async_set("switch.power", STATE_ON)

    async def source_on(_call) -> None:
        nonlocal source_calls
        source_calls += 1
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("switch", "turn_on", power_on)
    hass.services.async_register("light", "turn_on", source_on)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.02)
    assert source_calls == 0
    assert worker.diagnostics()["pending_stage"] == "source_available"

    hass.states.async_set("light.source", STATE_OFF)
    worker.notify_source_change()
    await asyncio.sleep(0.04)
    assert source_calls == 1
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_turn_off_verifies_source_before_power_off(
    hass: HomeAssistant,
) -> None:
    """Never remove power until the source is observed off."""
    hass.states.async_set("switch.power", STATE_ON)
    hass.states.async_set("light.source", STATE_ON)
    source_called = asyncio.Event()
    allow_source_off = asyncio.Event()
    power_calls = 0

    async def source_off(_call) -> None:
        source_called.set()
        await allow_source_off.wait()
        hass.states.async_set("light.source", STATE_OFF)

    async def power_off(_call) -> None:
        nonlocal power_calls
        power_calls += 1
        hass.states.async_set("switch.power", STATE_OFF)

    hass.services.async_register("light", "turn_off", source_off)
    hass.services.async_register("switch", "turn_off", power_off)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_off", {}, None)
    await asyncio.wait_for(source_called.wait(), 1)
    assert power_calls == 0
    allow_source_off.set()
    await asyncio.sleep(0.04)
    assert power_calls == 1
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_power_already_off_succeeds_with_unavailable_source(
    hass: HomeAssistant,
) -> None:
    """An off dependency proves the compound light is off."""
    hass.states.async_set("switch.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_UNAVAILABLE)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_off", {}, None)
    await asyncio.sleep(0.02)
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "verified"
    await worker.async_shutdown()


async def test_power_failures_retry_and_validation_is_terminal(
    hass: HomeAssistant,
) -> None:
    """Retry runtime power failures but terminate invalid power calls."""
    hass.states.async_set("switch.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_OFF)
    calls = 0

    async def power_on(_call) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "temporary"
            raise RuntimeError(msg)
        if calls == 2:
            hass.states.async_set("switch.power", STATE_ON)
            return

    async def source_on(_call) -> None:
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("switch", "turn_on", power_on)
    hass.services.async_register("light", "turn_on", source_on)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.07)
    assert calls >= 2
    assert not worker.has_pending
    await worker.async_shutdown()

    hass.states.async_set("switch.power", STATE_OFF)

    async def invalid_power(_call) -> None:
        msg = "invalid"
        raise ServiceValidationError(msg)

    hass.services.async_register("switch", "turn_on", invalid_power)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.03)
    assert not worker.has_pending
    assert worker.diagnostics()["last_result"] == "invalid"
    await worker.async_shutdown()


async def test_power_verification_mismatch_retries(
    hass: HomeAssistant,
) -> None:
    """A successful power service call still retries until power is observed on."""
    hass.states.async_set("switch.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_OFF)
    calls = 0

    async def power_on(_call) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            hass.states.async_set("switch.power", STATE_ON)

    async def source_on(_call) -> None:
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("switch", "turn_on", power_on)
    hass.services.async_register("light", "turn_on", source_on)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.sleep(0.07)

    assert calls >= 2
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_supersession_during_power_on_recovers_with_new_off(
    hass: HomeAssistant,
) -> None:
    """A stale power-on call is followed by the newer compound off intent."""
    hass.states.async_set("switch.power", STATE_OFF)
    hass.states.async_set("light.source", STATE_OFF)
    power_started = asyncio.Event()
    release_power = asyncio.Event()
    source_on_calls = 0
    power_off_calls = 0

    async def power_on(_call) -> None:
        power_started.set()
        await release_power.wait()
        hass.states.async_set("switch.power", STATE_ON)

    async def source_on(_call) -> None:
        nonlocal source_on_calls
        source_on_calls += 1

    async def power_off(_call) -> None:
        nonlocal power_off_calls
        power_off_calls += 1
        hass.states.async_set("switch.power", STATE_OFF)

    hass.services.async_register("switch", "turn_on", power_on)
    hass.services.async_register("switch", "turn_off", power_off)
    hass.services.async_register("light", "turn_on", source_on)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_on", {}, None)
    await asyncio.wait_for(power_started.wait(), 1)
    await worker.async_submit("turn_off", {}, None)
    release_power.set()
    await asyncio.sleep(0.05)

    assert source_on_calls == 0
    assert power_off_calls == 1
    assert not worker.has_pending
    await worker.async_shutdown()


async def test_supersession_before_power_off_prevents_stale_dispatch(
    hass: HomeAssistant,
) -> None:
    """A new generation arriving in source-off work keeps dependency power on."""
    hass.states.async_set("switch.power", STATE_ON)
    hass.states.async_set("light.source", STATE_ON)
    source_started = asyncio.Event()
    release_source = asyncio.Event()
    power_off_calls = 0

    async def source_off(_call) -> None:
        source_started.set()
        await release_source.wait()
        hass.states.async_set("light.source", STATE_OFF)

    async def source_on(_call) -> None:
        hass.states.async_set("light.source", STATE_ON)

    async def power_off(_call) -> None:
        nonlocal power_off_calls
        power_off_calls += 1

    hass.services.async_register("light", "turn_off", source_off)
    hass.services.async_register("light", "turn_on", source_on)
    hass.services.async_register("switch", "turn_off", power_off)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_off", {}, None)
    await asyncio.wait_for(source_started.wait(), 1)
    await worker.async_submit("turn_on", {}, None)
    release_source.set()
    await asyncio.sleep(0.04)

    assert power_off_calls == 0
    assert not worker.has_pending
    assert hass.states.get("switch.power").state == STATE_ON
    assert hass.states.get("light.source").state == STATE_ON
    await worker.async_shutdown()


async def test_supersession_during_power_off_recovers_with_new_on(
    hass: HomeAssistant,
) -> None:
    """A new on generation restores power after an in-flight stale power-off."""
    hass.states.async_set("switch.power", STATE_ON)
    hass.states.async_set("light.source", STATE_OFF)
    power_off_started = asyncio.Event()
    release_power_off = asyncio.Event()
    calls: list[str] = []

    async def power_off(_call) -> None:
        calls.append("power_off")
        power_off_started.set()
        await release_power_off.wait()
        hass.states.async_set("switch.power", STATE_OFF)

    async def power_on(_call) -> None:
        calls.append("power_on")
        hass.states.async_set("switch.power", STATE_ON)

    async def source_on(_call) -> None:
        calls.append("source_on")
        hass.states.async_set("light.source", STATE_ON)

    hass.services.async_register("switch", "turn_off", power_off)
    hass.services.async_register("switch", "turn_on", power_on)
    hass.services.async_register("light", "turn_on", source_on)
    worker = make_power_worker(hass)
    worker.start()
    await worker.async_submit("turn_off", {}, None)
    await asyncio.wait_for(power_off_started.wait(), 1)
    await worker.async_submit("turn_on", {}, None)
    release_power_off.set()
    await asyncio.sleep(0.06)

    assert calls == ["power_off", "power_on", "source_on"]
    assert not worker.has_pending
    assert hass.states.get("switch.power").state == STATE_ON
    assert hass.states.get("light.source").state == STATE_ON
    await worker.async_shutdown()
