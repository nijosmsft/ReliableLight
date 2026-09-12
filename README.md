# ReliableLight

ReliableLight is a device-agnostic Home Assistant custom integration that wraps
unreliable `light` entities with reliable proxy lights. Calls to a proxy return
quickly. An independent worker for that proxy calls the source, verifies the
observed result, and retries recoverable Home Assistant service failures with
bounded exponential backoff. A managed light can optionally own a registered
`light` or `switch` entity that supplies its upstream power.

Requires Home Assistant 2026.9.0 or newer. The minimum matches the oldest
version exercised by the automated integration test environment.

## Behavior

- Select one or more registered source lights during initial setup.
- Add, reconfigure, or delete individual **Managed light** entries from the
  ReliableLight integration page. The source is immutable after creation;
  reconfiguration changes only its optional upstream power dependency.
- One stable proxy is created per source entity registry UUID.
- New commands atomically replace older pending commands for that proxy.
- Equivalent pending commands coalesce.
- Different proxies retry independently.
- State, brightness, color modes, color temperature, colors, effects, and
  transitions mirror and forward where supported. Flash is intentionally
  excluded because it is not safely retryable steady-state intent.
- Source `unknown` or `unavailable` never counts as success. The proxy remains
  callable, reports `unknown`, and exposes clearly labeled pending diagnostics.
- With upstream power configured, turn-on verifies power before waiting for the
  source to become available and forwarding the original light command.
  Turn-off verifies the source is off before removing power. If power is
  already off, turn-off succeeds even when the source is unavailable.

ReliableLight is a proxy boundary. Calling the raw source entity bypasses its
retry worker. For groups, wrap leaf lights and place the ReliableLight proxies
in a normal Home Assistant light group. Wrapping an aggregate group only
verifies that group's aggregate state and may hide a failing member.

## Installation

### HACS

Add `https://github.com/nijosmsft/ReliableLight` as a custom HACS integration
repository, install ReliableLight, restart Home Assistant, then add
**ReliableLight** under **Settings > Devices & services**.

### Manual

Copy `custom_components/reliable_light` into the Home Assistant configuration
directory's `custom_components` folder and restart Home Assistant.

## Configuration

The initial config flow accepts one or more source `light` entities. After
setup, use **Add managed light** on the integration page for additional
sources. Each managed light accepts an optional upstream power entity from the
`light` or `switch` domain.

ReliableLight rejects its own proxies, duplicate sources, a source used as
power, a power entity used by more than one managed light, and detectable group
recursion between the source and power. Options under **Configure** control:

- Initial and maximum retry delay.
- Verification delay and strict, normal, or relaxed tolerances.
- Command expiry.
- Opt-in persistent pending retry.
- Diagnostic state attributes and diagnostic events.

Example: add `light.cabinet_strip` as the source and
`switch.cabinet_strip_power` as upstream power. Automations should then call the
generated ReliableLight proxy, such as `light.cabinet_strip_reliable`, rather
than either raw entity. A proxy without upstream power continues to behave like
the v0.1.x source-only proxy.

Persistent retry requires a finite expiry between 30 seconds and 24 hours.
The default is disabled. When enabled, the latest command is persisted before
dispatch and read back to confirm durability. It is restored only while
unexpired, reverified before replay, and cleared with a read-back-confirmed
tombstone after successful verification. Transition timing is not replayed
after restart.

Upgrading from v0.1.3 automatically converts the effective source UUID list to
one managed-light subentry per source. Existing proxy unique IDs, entity IDs,
custom names, and source-only behavior are preserved. The parent entry keeps a
compatibility shadow of source UUIDs, but subentries are authoritative.

Pending storage includes the configured power registry UUID as a fingerprint.
If the dependency changes while Home Assistant is stopped, the old intent is
discarded rather than replayed against the new power topology.

## Supersession and retries

A proxy has one current desired command and one worker. A newer non-equivalent
command replaces the complete older command. The worker checks its generation
before dispatch and after every await, so obsolete generations are never
retried. Home Assistant cannot cancel a source service call that is already
executing; if a newer command arrives during that call, it executes next and
the obsolete command receives no further attempts.

Retryable results are source integration runtime exceptions, a
missing/unavailable source, and a verification mismatch. Invalid service data
terminates that command. Unexpected programming exceptions outside the source
service-call boundary stop the affected worker and are surfaced in logs and
diagnostics rather than silently retried.

## Diagnostics

Optional attributes show pending action, age, attempt count, next retry,
current command stage, power state/availability, last bounded result category,
verification time, and worker health. Optional
`reliable_light_command_status` events provide the same bounded status without
raw exception text. Downloadable diagnostics redact source and power identifiers.

## Limitations

- A service call already executing in another integration cannot be recalled.
  ReliableLight does not cancel timed-out service tasks because Home Assistant
  source service handlers are not guaranteed to be cancellation-safe.
- Source devices may quantize colors; verification tolerances compensate for
  normal rounding but cannot guarantee identical physical output.
- Some template or custom group structures do not publish enough membership
  data for static cycle detection. Runtime context recursion detection provides
  a second guard.
- One upstream power entity can currently be owned by only one managed light.
- Brand images are not included as generated placeholders. Proper assets should
  be submitted to the Home Assistant brands repository when available.

## Development

```text
python -m pip install -r requirements_test.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest --cov=custom_components.reliable_light --cov-report=term-missing
```

## Releases

ReliableLight follows Semantic Versioning. Before publishing a GitHub release,
update `custom_components/reliable_light/manifest.json` and `CHANGELOG.md`, then
create a release tag such as `v0.2.0`. HACS uses published GitHub releases when
they exist and otherwise installs the default branch.

## License

MIT
