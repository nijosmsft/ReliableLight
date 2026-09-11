# Changelog

All notable changes are documented here. ReliableLight follows Semantic
Versioning, and the version in `manifest.json` matches each GitHub release tag
without the leading `v`.

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
