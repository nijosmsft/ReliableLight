"""Independent command worker for one ReliableLight proxy."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ATTR_TRANSITION
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BACKOFF_JITTER,
    BACKOFF_MULTIPLIER,
    DOMAIN,
    EVENT_COMMAND_STATUS,
    STORAGE_VERSION,
)
from .model import DesiredCommand, ReliableLightOptions
from .verification import command_matches

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)


def _frozen(value: Any) -> Any:
    """Convert nested service data to an equality-safe immutable value."""
    if isinstance(value, dict):
        return tuple(sorted((key, _frozen(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _json_stable(value: Any) -> Any:
    """Normalize nested command data to stable JSON-compatible values."""
    if isinstance(value, Mapping):
        return {
            str(key): _json_stable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_stable(item) for item in value]
    return value


class ReliableLightWorker:
    """Serialize retries and supersession for one source light."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        source_registry_id: str,
        options: ReliableLightOptions,
        source_entity_id: Callable[[], str | None],
        state_changed: Callable[[], None],
        active_contexts: set[str],
        power_registry_id: str | None = None,
        power_entity: Callable[[], tuple[str, str] | None] | None = None,
    ) -> None:
        """Initialize an independent source command worker."""
        self._hass = hass
        self._source_registry_id = source_registry_id
        self._power_registry_id = power_registry_id
        self._options = options
        self._source_entity_id = source_entity_id
        self._power_entity = power_entity or (lambda: None)
        self._state_changed = state_changed
        self._active_contexts = active_contexts
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.{source_registry_id}",
            atomic_writes=True,
        )
        self._lock = asyncio.Lock()
        self._command_event = asyncio.Event()
        self._source_event = asyncio.Event()
        self._current: DesiredCommand | None = None
        self._generation = 0
        self._revision = 0
        self._persisted_revision = 0
        self._persist_task: asyncio.Task[None] | None = None
        self._persist_wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._failed = False
        self._last_result = "idle"
        self._last_service_exception: tuple[str, str] | None = None
        self._last_verified_at: str | None = None
        self._next_retry_at: str | None = None
        self._pending_stage: str | None = None

    @property
    def failed(self) -> bool:
        """Return whether the worker encountered an unexpected failure."""
        return self._failed

    @property
    def has_pending(self) -> bool:
        """Return whether a command is pending."""
        return self._current is not None

    async def async_initialize(self) -> None:
        """Load an eligible persisted command."""
        if not self._options.persistent_retry:
            await self._store.async_remove()
            return

        stored = await self._store.async_load()
        if not stored:
            return
        self._generation = max(0, int(stored.get("last_generation", 0)))
        pending = stored.get("pending")
        if not isinstance(pending, dict):
            return
        try:
            accepted_at = float(pending["accepted_at"])
            expires_at = float(pending["expires_at"])
            action = str(pending["action"])
            kwargs = dict(pending["kwargs"])
        except (KeyError, TypeError, ValueError):
            await self._store.async_remove()
            return
        if pending.get("power_registry_id") != self._power_registry_id:
            await self._store.async_remove()
            return
        now = time.time()
        if (
            action not in (SERVICE_TURN_ON, SERVICE_TURN_OFF)
            or accepted_at > now + 300
            or expires_at <= now
        ):
            await self._store.async_remove()
            return

        self._generation += 1
        self._revision += 1
        kwargs.pop(ATTR_TRANSITION, None)
        self._current = DesiredCommand(
            generation=self._generation,
            action=action,
            kwargs=kwargs,
            accepted_at=accepted_at,
            expires_at=expires_at,
            attempt_count=0,
            revision=self._revision,
            context=None,
            restored=True,
        )
        self._last_result = "restored"
        self._pending_stage = "queued"
        self._schedule_persist()
        self._command_event.set()

    def start(self) -> None:
        """Start the command loop."""
        if self._task is not None:
            return
        self._task = self._hass.async_create_background_task(
            self._async_run(), f"{DOMAIN} command worker"
        )
        self._task.add_done_callback(self._task_done)

    async def async_submit(
        self, action: str, kwargs: dict[str, Any], context: Context | None
    ) -> bool:
        """Submit a command and return True when it coalesced."""
        if self._closing or self._failed:
            msg = "ReliableLight worker is not available"
            raise HomeAssistantError(msg)
        command_kwargs = dict(kwargs)
        async with self._lock:
            now = time.time()
            current = self._current
            if (
                current is not None
                and not self._is_expired(current, now)
                and current.action == action
                and _frozen(current.kwargs) == _frozen(command_kwargs)
            ):
                return True

            self._generation += 1
            self._revision += 1
            expires_at = (
                now + self._options.command_expiry
                if self._options.command_expiry > 0
                else None
            )
            self._current = DesiredCommand(
                generation=self._generation,
                action=action,
                kwargs=command_kwargs,
                accepted_at=now,
                expires_at=expires_at,
                attempt_count=0,
                revision=self._revision,
                context=context,
            )
            self._last_result = "accepted"
            self._pending_stage = "queued"
            self._next_retry_at = None
            self._schedule_persist()
            self._command_event.set()
            accepted_command = self._current
        self._state_changed()
        self._emit_status(
            "accepted",
            generation=accepted_command.generation,
            action=accepted_command.action,
            attempt_count=accepted_command.attempt_count,
        )
        return False

    def notify_source_change(self) -> None:
        """Wake retry handling when the source changes."""
        self._source_event.set()

    def is_internal_context(self, context: Context | None) -> bool:
        """Detect a service call recursively generated by ReliableLight."""
        return bool(
            context
            and (
                context.id in self._active_contexts
                or context.parent_id in self._active_contexts
            )
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return bounded diagnostics suitable for entity attributes."""
        command = self._current
        return {
            "pending": command is not None,
            "pending_action": command.action if command else None,
            "pending_stage": self._pending_stage if command else None,
            "pending_since": (
                dt_util.utc_from_timestamp(command.accepted_at).isoformat()
                if command
                else None
            ),
            "attempt_count": command.attempt_count if command else 0,
            "next_retry_at": self._next_retry_at,
            "last_result": self._last_result,
            "last_verified_at": self._last_verified_at,
            "worker_failed": self._failed,
        }

    async def async_shutdown(self) -> None:
        """Stop the worker and finalize persistence."""
        self._closing = True
        self._command_event.set()
        self._source_event.set()
        if self._task is not None:
            if self._task.done():
                self._task.exception()
            else:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            self._task = None
        if self._options.persistent_retry:
            self._revision += 1
            self._schedule_persist()
            await self._wait_persisted(self._revision)
        else:
            await self._store.async_remove()
        if self._persist_task is not None:
            await self._persist_task

    async def async_remove_store(self) -> None:
        """Remove persistent data."""
        await self._store.async_remove()

    def _task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self._closing:
            return
        if (error := task.exception()) is None:
            return
        self._failed = True
        self._last_result = "worker_failed"
        _LOGGER.error(
            "ReliableLight worker for source registry id %s failed",
            self._source_registry_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        self._state_changed()
        command = self._current
        self._emit_status(
            "worker_failed",
            generation=command.generation if command else self._generation,
            action=command.action if command else None,
            attempt_count=command.attempt_count if command else 0,
        )

    def _schedule_persist(self) -> None:
        self._persist_wakeup.set()
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = self._hass.async_create_task(
                self._async_persist_loop(), f"{DOMAIN} persistence writer"
            )
            self._persist_task.add_done_callback(self._persist_done)

    def _persist_done(self, task: asyncio.Task[None]) -> None:
        """Continue writing if a revision arrived while the writer exited."""
        if task.cancelled():
            return
        if error := task.exception():
            self._failed = True
            self._last_result = "persistence_failed"
            _LOGGER.error(
                "ReliableLight persistence for source registry id %s failed",
                self._source_registry_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            self._state_changed()
            return
        if self._persist_task is task:
            self._persist_task = None
        if self._persisted_revision < self._revision and not self._failed:
            self._schedule_persist()

    def _serialize(self, revision: int) -> dict[str, Any]:
        command = self._current
        pending: dict[str, Any] | None = None
        if command is not None:
            kwargs = {
                key: _json_stable(value)
                for key, value in sorted(command.kwargs.items())
                if key != ATTR_TRANSITION
            }
            pending = {
                "action": command.action,
                "kwargs": kwargs,
                "accepted_at": command.accepted_at,
                "expires_at": command.expires_at,
                "power_registry_id": self._power_registry_id,
            }
        return {
            "revision": revision,
            "source_registry_id": self._source_registry_id,
            "last_generation": self._generation,
            "pending": pending,
        }

    async def _async_persist_loop(self) -> None:
        while True:
            self._persist_wakeup.clear()
            revision = self._revision
            payload = self._serialize(revision)
            if await self._async_persist_and_confirm(payload):
                self._persisted_revision = max(self._persisted_revision, revision)
            else:
                self._last_result = "persistence_error"
                self._state_changed()
                command = self._current
                self._emit_status(
                    "persistence_error",
                    generation=command.generation if command else self._generation,
                    action=command.action if command else None,
                    attempt_count=command.attempt_count if command else 0,
                )
            if self._persisted_revision >= self._revision:
                return
            if self._persisted_revision < revision:
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._persist_wakeup.wait(),
                        timeout=min(self._options.retry_initial, 5.0),
                    )

    async def _async_persist_and_confirm(self, payload: dict[str, Any]) -> bool:
        """Write and read back a revision before treating it as durable."""
        if not self._options.persistent_retry:
            await self._store.async_remove()
            return True
        try:
            await self._store.async_save(payload)
            return _json_stable(await self._async_read_persisted()) == payload
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Unable to confirm ReliableLight persistence for source registry "
                "id %s: %s",
                self._source_registry_id,
                err,
            )
            return False

    async def _async_read_persisted(self) -> dict[str, Any] | None:
        """Read storage through a fresh Store instance to bypass write buffers."""
        confirmation_store = Store[dict[str, Any]](
            self._hass,
            STORAGE_VERSION,
            self._store.key,
            atomic_writes=True,
        )
        return await confirmation_store.async_load()

    async def _wait_persisted(
        self, revision: int, expires_at: float | None = None
    ) -> bool:
        if not self._options.persistent_retry:
            return True
        while self._persisted_revision < revision:
            timeout: float | None = None
            if expires_at is not None:
                timeout = expires_at - time.time()
                if timeout <= 0:
                    return False
            self._schedule_persist()
            assert self._persist_task is not None
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._persist_task),
                    timeout=timeout,
                )
            except TimeoutError:
                return False
        return True

    def _is_current(self, generation: int) -> bool:
        return self._current is not None and self._current.generation == generation

    async def _async_run(self) -> None:
        while not self._closing:
            if self._current is None:
                await self._command_event.wait()
                self._command_event.clear()
                continue

            command = self._current
            self._command_event.clear()
            self._source_event.clear()

            if self._is_expired(command):
                await self._async_finish(command.generation, "expired")
                continue

            if not await self._wait_persisted(command.revision, command.expires_at):
                await self._async_finish(command.generation, "expired")
                continue
            if not self._is_current(command.generation):
                continue
            if self._is_expired(command):
                await self._async_finish(command.generation, "expired")
                continue

            outcome = await self._async_attempt(command)
            if outcome == "superseded":
                continue
            if outcome == "verified":
                await self._async_finish(command.generation, "verified")
                continue
            if outcome in ("invalid", "expired"):
                await self._async_finish(command.generation, outcome)
                continue

            async with self._lock:
                if not self._is_current(command.generation):
                    continue
                assert self._current is not None
                attempts = self._current.attempt_count + 1
                self._current = replace(self._current, attempt_count=attempts)
                delay = self._backoff_delay(attempts)
                if self._current.expires_at is not None:
                    delay = min(
                        delay,
                        max(0.0, self._current.expires_at - time.time()),
                    )
                self._next_retry_at = dt_util.utc_from_timestamp(
                    time.time() + delay
                ).isoformat()
                self._last_result = outcome
                retry_command = self._current
            self._state_changed()
            self._emit_status(
                outcome,
                generation=retry_command.generation,
                action=retry_command.action,
                attempt_count=retry_command.attempt_count,
            )
            await self._wait_for_wakeup(delay)

    async def _async_attempt(self, command: DesiredCommand) -> str:
        if self._power_registry_id is None:
            return await self._async_attempt_source_only(command)
        if command.action == SERVICE_TURN_ON:
            return await self._async_attempt_power_on(command)
        return await self._async_attempt_power_off(command)

    async def _async_attempt_source_only(self, command: DesiredCommand) -> str:
        """Run the unchanged source-only command path."""
        if self._is_expired(command):
            return "expired"
        self._set_stage(command, "source")
        state = self._source_state()
        if command_matches(
            state, command.action, command.kwargs, self._options.tolerance
        ):
            return "verified"

        source_entity_id = self._source_entity_id()
        if source_entity_id is None:
            return "source_missing"
        if not self._is_current(command.generation):
            return "superseded"

        outcome = await self._async_service_call(
            command,
            LIGHT_DOMAIN,
            command.action,
            source_entity_id,
            command.kwargs,
            "source",
        )
        if outcome is not None:
            return outcome

        transition = float(command.kwargs.get(ATTR_TRANSITION, 0))
        if outcome := await self._async_settle(
            command, transition + self._options.verification_delay
        ):
            return outcome

        if command_matches(
            self._source_state(),
            command.action,
            command.kwargs,
            self._options.tolerance,
        ):
            return "verified"
        return "verification_failed"

    async def _async_attempt_power_on(self, command: DesiredCommand) -> str:
        """Turn on and verify power before commanding the source."""
        power = self._power_entity()
        if power is None:
            self._set_stage(command, "power_on")
            return "power_missing"
        power_domain, power_entity_id = power
        if self._power_state() != STATE_ON:
            self._set_stage(command, "power_on")
            outcome = await self._async_service_call(
                command,
                power_domain,
                SERVICE_TURN_ON,
                power_entity_id,
                {},
                "power",
            )
            if outcome is not None:
                return outcome
            if outcome := await self._async_settle(
                command, self._options.verification_delay
            ):
                return outcome
            if self._power_state() != STATE_ON:
                return "power_verification_failed"

        if not self._is_current(command.generation):
            return "superseded"
        if self._is_expired(command):
            return "expired"
        self._set_stage(command, "source_available")
        source_state = self._source_state()
        if source_state is None:
            return "source_missing"
        if source_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return "source_unavailable"
        if command_matches(
            source_state,
            command.action,
            command.kwargs,
            self._options.tolerance,
        ):
            return "verified"

        source_entity_id = self._source_entity_id()
        if source_entity_id is None:
            return "source_missing"
        self._set_stage(command, "source_on")
        outcome = await self._async_service_call(
            command,
            LIGHT_DOMAIN,
            SERVICE_TURN_ON,
            source_entity_id,
            command.kwargs,
            "source",
        )
        if outcome is not None:
            return outcome
        transition = float(command.kwargs.get(ATTR_TRANSITION, 0))
        if outcome := await self._async_settle(
            command, transition + self._options.verification_delay
        ):
            return outcome
        if self._power_state() != STATE_ON:
            return "power_verification_failed"
        if command_matches(
            self._source_state(),
            command.action,
            command.kwargs,
            self._options.tolerance,
        ):
            return "verified"
        return "verification_failed"

    async def _async_attempt_power_off(self, command: DesiredCommand) -> str:
        """Verify source off before turning off the dependency."""
        power = self._power_entity()
        if power is None:
            self._set_stage(command, "power_off")
            return "power_missing"
        power_domain, power_entity_id = power
        if self._power_state() == STATE_OFF:
            return "verified"

        source_state = self._source_state()
        if not command_matches(
            source_state,
            SERVICE_TURN_OFF,
            command.kwargs,
            self._options.tolerance,
        ):
            source_entity_id = self._source_entity_id()
            if source_entity_id is None:
                self._set_stage(command, "source_off")
                return "source_missing"
            if source_state is None or source_state.state in (
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            ):
                self._set_stage(command, "source_off")
                return "source_unavailable"
            self._set_stage(command, "source_off")
            outcome = await self._async_service_call(
                command,
                LIGHT_DOMAIN,
                SERVICE_TURN_OFF,
                source_entity_id,
                command.kwargs,
                "source",
            )
            if outcome is not None:
                return outcome
            transition = float(command.kwargs.get(ATTR_TRANSITION, 0))
            if outcome := await self._async_settle(
                command, transition + self._options.verification_delay
            ):
                return outcome
            if not command_matches(
                self._source_state(),
                SERVICE_TURN_OFF,
                command.kwargs,
                self._options.tolerance,
            ):
                return "verification_failed"

        if not self._is_current(command.generation):
            return "superseded"
        if self._is_expired(command):
            return "expired"
        self._set_stage(command, "power_off")
        if not self._is_current(command.generation):
            return "superseded"
        outcome = await self._async_service_call(
            command,
            power_domain,
            SERVICE_TURN_OFF,
            power_entity_id,
            {},
            "power",
        )
        if outcome is not None:
            return outcome
        if outcome := await self._async_settle(
            command, self._options.verification_delay
        ):
            return outcome
        if self._power_state() == STATE_OFF:
            return "verified"
        return "power_verification_failed"

    async def _async_service_call(
        self,
        command: DesiredCommand,
        domain: str,
        action: str,
        entity_id: str,
        kwargs: dict[str, Any],
        target: str,
    ) -> str | None:
        """Dispatch one guarded service call at the integration boundary."""
        if not self._is_current(command.generation):
            return "superseded"
        if self._is_expired(command):
            return "expired"
        call_context = Context(
            user_id=command.context.user_id if command.context is not None else None,
            parent_id=command.context.id if command.context is not None else None,
        )
        self._active_contexts.add(call_context.id)
        try:
            try:
                if not self._is_current(command.generation):
                    return "superseded"
                await self._hass.services.async_call(
                    domain,
                    action,
                    {ATTR_ENTITY_ID: entity_id, **kwargs},
                    blocking=True,
                    context=call_context,
                )
            except ServiceValidationError:
                return "invalid"
            except HomeAssistantError as err:
                self._service_failed(err, target)
                return "service_error"
            except Exception as err:  # noqa: BLE001
                self._service_failed(err, target)
                return "service_error"
            else:
                self._last_service_exception = None
        finally:
            self._active_contexts.discard(call_context.id)
        if not self._is_current(command.generation):
            return "superseded"
        if self._is_expired(command):
            return "expired"
        return None

    async def _async_settle(
        self, command: DesiredCommand, settle_delay: float
    ) -> str | None:
        """Wait for state propagation while remaining supersession-safe."""
        if not self._is_current(command.generation):
            return "superseded"
        if self._is_expired(command):
            return "expired"
        if command.expires_at is not None:
            settle_delay = min(
                settle_delay,
                max(0.0, command.expires_at - time.time()),
            )
        if settle_delay > 0:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._command_event.wait(), timeout=settle_delay)
            if not self._is_current(command.generation):
                return "superseded"
            if self._is_expired(command):
                return "expired"
        return None

    def _set_stage(self, command: DesiredCommand, stage: str) -> None:
        """Update the bounded diagnostic stage for the current generation."""
        if self._is_current(command.generation) and self._pending_stage != stage:
            self._pending_stage = stage
            self._state_changed()

    def _service_failed(self, error: Exception, target: str) -> None:
        """Log a service exception once per consecutive exception type."""
        exception_type = type(error).__name__
        exception_key = (target, exception_type)
        if exception_key == self._last_service_exception:
            return
        self._last_service_exception = exception_key
        _LOGGER.warning(
            (
                "Source service call failed for registry id %s with %s; will retry"
                if target == "source"
                else "Power service call failed for source registry id %s with %s; "
                "will retry"
            ),
            self._source_registry_id,
            exception_type,
        )

    def _source_state(self) -> State | None:
        source_entity_id = self._source_entity_id()
        return (
            self._hass.states.get(source_entity_id)
            if source_entity_id is not None
            else None
        )

    def _power_state(self) -> str | None:
        power = self._power_entity()
        if power is None:
            return None
        return (
            state.state
            if (state := self._hass.states.get(power[1])) is not None
            else None
        )

    async def _async_finish(self, generation: int, result: str) -> None:
        async with self._lock:
            if not self._is_current(generation):
                return
            assert self._current is not None
            completed = self._current
            self._current = None
            self._revision += 1
            revision = self._revision
            self._last_result = result
            self._next_retry_at = None
            self._pending_stage = None
            if result == "verified":
                self._last_verified_at = dt_util.utcnow().isoformat()
            self._schedule_persist()
        await self._wait_persisted(revision)
        self._state_changed()
        self._emit_status(
            result,
            generation=completed.generation,
            action=completed.action,
            attempt_count=completed.attempt_count,
        )

    def _backoff_delay(self, attempts: int) -> float:
        """Return bounded jittered exponential backoff without overflow."""
        initial = self._options.retry_initial
        maximum = self._options.retry_max
        if initial >= maximum:
            base = maximum
        else:
            saturation_attempt = math.ceil(math.log2(maximum / initial)) + 1
            exponent = min(max(0, attempts - 1), saturation_attempt - 1)
            base = min(maximum, initial * (BACKOFF_MULTIPLIER**exponent))
        return min(
            maximum,
            base * random.uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER),
        )

    @staticmethod
    def _is_expired(command: DesiredCommand, now: float | None = None) -> bool:
        """Return whether a command has passed its absolute expiry."""
        return command.expires_at is not None and command.expires_at <= (
            time.time() if now is None else now
        )

    async def _wait_for_wakeup(self, delay: float) -> None:
        command_waiter = asyncio.create_task(self._command_event.wait())
        source_waiter = asyncio.create_task(self._source_event.wait())
        try:
            done, pending = await asyncio.wait(
                (command_waiter, source_waiter),
                timeout=delay,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
        finally:
            self._command_event.clear()
            self._source_event.clear()

    def _emit_status(
        self,
        status: str,
        *,
        generation: int | None = None,
        action: str | None = None,
        attempt_count: int | None = None,
    ) -> None:
        if not self._options.emit_events:
            return
        command = self._current
        self._hass.bus.async_fire(
            EVENT_COMMAND_STATUS,
            {
                "source_registry_id": self._source_registry_id,
                "status": status,
                "generation": (
                    generation
                    if generation is not None
                    else command.generation
                    if command
                    else self._generation
                ),
                "action": action
                if action is not None
                else command.action
                if command
                else None,
                "attempt_count": (
                    attempt_count
                    if attempt_count is not None
                    else command.attempt_count
                    if command
                    else 0
                ),
            },
        )
