# Changelog

All notable changes are documented here. ReliableLight follows Semantic
Versioning, and the version in `manifest.json` matches each GitHub release tag
without the leading `v`.

## 0.1.0 - Unreleased

- Initial config flow and device-agnostic reliable proxy light platform.
- Independent retry, verification, coalescing, and supersession workers.
- User context propagation through source service calls.
- Overflow-safe bounded backoff and expiry-aware coalescing/wakeups.
- Opt-in pending-command persistence with mandatory expiry and durable readback.
- Diagnostics, translations, tests, and HACS metadata.
