# Changelog

All notable changes are documented here. ReliableLight follows Semantic
Versioning, and the version in `manifest.json` matches each GitHub release tag
without the leading `v`.

## 0.2.0 - 2026-09-12

- Move managed sources to Home Assistant config subentries while preserving
  existing proxy identities, entity IDs, and custom names during migration.
- Add an optional registered light or switch as an exclusive upstream power
  dependency for each source.
- Sequence and verify power-on before source-on, and source-off before
  power-off, with generation-safe supersession at every stage.
- Add compound proxy state handling, power diagnostics, dependency-aware
  persistence, validation, and managed-light add/reconfigure/delete UI.

## 0.1.3 - 2026-09-11

- Generate new proxy entity IDs directly from the source object ID.
- Preserve source-device association and readable, non-duplicated names.

## 0.1.2 - 2026-09-11

- Retry source integration runtime exceptions without failing the proxy worker.
- Rate-limit source exception warnings by consecutive exception type.

## 0.1.1 - 2026-09-11

- Classify ReliableLight as a hub so it appears in normal integration search.
- Clarify how to manage sources when the singleton entry already exists.

## 0.1.0 - 2026-09-11

- Initial config flow and device-agnostic reliable proxy light platform.
- Independent retry, verification, coalescing, and supersession workers.
- User context propagation through source service calls.
- Overflow-safe bounded backoff and expiry-aware coalescing/wakeups.
- Opt-in pending-command persistence with mandatory expiry and durable readback.
- Diagnostics, translations, tests, and HACS metadata.
