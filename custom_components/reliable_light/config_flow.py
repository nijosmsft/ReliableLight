"""Config flow for ReliableLight."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.storage import Store

from .const import (
    CONF_COMMAND_EXPIRY,
    CONF_DIAGNOSTIC_ATTRIBUTES,
    CONF_EMIT_EVENTS,
    CONF_PERSISTENT_RETRY,
    CONF_RETRY_INITIAL,
    CONF_RETRY_MAX,
    CONF_SOURCES,
    CONF_VERIFICATION_DELAY,
    CONF_VERIFICATION_TOLERANCE,
    DEFAULT_COMMAND_EXPIRY,
    DEFAULT_DIAGNOSTIC_ATTRIBUTES,
    DEFAULT_EMIT_EVENTS,
    DEFAULT_PERSISTENT_RETRY,
    DEFAULT_RETRY_INITIAL,
    DEFAULT_RETRY_MAX,
    DEFAULT_VERIFICATION_DELAY,
    DEFAULT_VERIFICATION_TOLERANCE,
    DOMAIN,
    MAX_COMMAND_EXPIRY,
    MIN_PERSISTENT_EXPIRY,
    STORAGE_VERSION,
)
from .model import TOLERANCE_PROFILES, configured_sources

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
    from homeassistant.core import HomeAssistant


def _group_members(hass: HomeAssistant, entity_id: str) -> list[str]:
    state = hass.states.get(entity_id)
    if state is None:
        return []
    members = state.attributes.get("entity_id")
    if not isinstance(members, (list, tuple)):
        return []
    return [member for member in members if isinstance(member, str)]


def _contains_reliable_light(
    hass: HomeAssistant, entity_id: str, seen: set[str]
) -> bool:
    if entity_id in seen:
        return True
    seen.add(entity_id)
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is not None and entry.platform == DOMAIN:
        return True
    return any(
        _contains_reliable_light(hass, member, seen.copy())
        for member in _group_members(hass, entity_id)
    )


def _validate_sources(
    hass: HomeAssistant, entity_ids: list[str]
) -> tuple[list[str] | None, str | None]:
    registry = er.async_get(hass)
    source_ids: list[str] = []
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is None or entry.domain != Platform.LIGHT:
            return None, "source_not_registered"
        if entry.platform == DOMAIN:
            return None, "reliable_light_source"
        if _contains_reliable_light(hass, entity_id, set()):
            return None, "source_cycle"
        if entry.id in source_ids:
            return None, "duplicate_source"
        source_ids.append(entry.id)
    if not source_ids:
        return None, "no_sources"
    return source_ids, None


def _source_schema(default: list[str] | None = None) -> vol.Schema:
    marker = (
        vol.Required(CONF_SOURCES, default=default)
        if default
        else vol.Required(CONF_SOURCES)
    )
    return vol.Schema(
        {
            marker: selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=Platform.LIGHT,
                    multiple=True,
                )
            )
        }
    )


class ReliableLightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a ReliableLight config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return ReliableLightOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the singleton ReliableLight entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            source_ids, error = _validate_sources(
                self.hass, list(user_input[CONF_SOURCES])
            )
            if error is None:
                return self.async_create_entry(
                    title="ReliableLight",
                    data={CONF_SOURCES: source_ids},
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=_source_schema(),
            errors=errors,
        )


class ReliableLightOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage ReliableLight sources and behavior."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update sources and retry behavior."""
        errors: dict[str, str] = {}
        if user_input is not None:
            source_ids, error = _validate_sources(
                self.hass, list(user_input[CONF_SOURCES])
            )
            if error is not None:
                errors["base"] = error
            elif float(user_input[CONF_RETRY_INITIAL]) > float(
                user_input[CONF_RETRY_MAX]
            ):
                errors["base"] = "invalid_backoff"
            elif bool(user_input[CONF_PERSISTENT_RETRY]) and not (
                MIN_PERSISTENT_EXPIRY
                <= int(user_input[CONF_COMMAND_EXPIRY])
                <= MAX_COMMAND_EXPIRY
            ):
                errors["base"] = "persistent_expiry_required"
            else:
                old_sources = set(configured_sources(self.config_entry))
                new_sources = set(source_ids or [])
                registry = er.async_get(self.hass)
                for removed_source in old_sources - new_sources:
                    if proxy_entity_id := registry.async_get_entity_id(
                        Platform.LIGHT, DOMAIN, removed_source
                    ):
                        registry.async_remove(proxy_entity_id)
                    await Store(
                        self.hass,
                        STORAGE_VERSION,
                        f"{DOMAIN}.{self.config_entry.entry_id}.{removed_source}",
                    ).async_remove()
                return self.async_create_entry(
                    data={**user_input, CONF_SOURCES: source_ids}
                )

        current = {**self.config_entry.data, **self.config_entry.options}
        registry = er.async_get(self.hass)
        current_entity_ids = [
            entry.entity_id
            for source_id in configured_sources(self.config_entry)
            if (entry := registry.async_get(source_id)) is not None
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SOURCES, default=current_entity_ids
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=Platform.LIGHT,
                        multiple=True,
                    )
                ),
                vol.Required(
                    CONF_RETRY_INITIAL,
                    default=current.get(CONF_RETRY_INITIAL, DEFAULT_RETRY_INITIAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.1,
                        max=300,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_RETRY_MAX,
                    default=current.get(CONF_RETRY_MAX, DEFAULT_RETRY_MAX),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=3600,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_VERIFICATION_DELAY,
                    default=current.get(
                        CONF_VERIFICATION_DELAY, DEFAULT_VERIFICATION_DELAY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=60,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_VERIFICATION_TOLERANCE,
                    default=current.get(
                        CONF_VERIFICATION_TOLERANCE,
                        DEFAULT_VERIFICATION_TOLERANCE,
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(TOLERANCE_PROFILES),
                        translation_key="verification_tolerance",
                    )
                ),
                vol.Required(
                    CONF_COMMAND_EXPIRY,
                    default=current.get(CONF_COMMAND_EXPIRY, DEFAULT_COMMAND_EXPIRY),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=MAX_COMMAND_EXPIRY,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PERSISTENT_RETRY,
                    default=current.get(
                        CONF_PERSISTENT_RETRY, DEFAULT_PERSISTENT_RETRY
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_DIAGNOSTIC_ATTRIBUTES,
                    default=current.get(
                        CONF_DIAGNOSTIC_ATTRIBUTES,
                        DEFAULT_DIAGNOSTIC_ATTRIBUTES,
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_EMIT_EVENTS,
                    default=current.get(CONF_EMIT_EVENTS, DEFAULT_EMIT_EVENTS),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
