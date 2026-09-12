# ReliableLight contributor guide

- Keep the integration device-agnostic. Never import a source integration.
- Source identity is the entity registry UUID; entity IDs are runtime addresses.
- Managed sources are `managed_light` config subentries. The parent source list
  is a migration compatibility shadow only; global options never edit sources.
- Optional upstream power identity is also a registry UUID and has one owner.
- Proxy state mirrors observed source state and must never be optimistic.
- Each source owns one serialized worker. Check command generation around every
  await and immediately before removing upstream power.
- Catch HomeAssistantError and ServiceValidationError only where documented; unexpected exceptions must surface.
- Persistent retry is opt-in, requires finite expiry, and persists before dispatch.
- Do not advertise flash. Verify only explicitly requested steady-state attributes.

Verified development commands:

```text
python -m pip install -r requirements_test.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest --cov=custom_components.reliable_light --cov-report=term-missing
```
