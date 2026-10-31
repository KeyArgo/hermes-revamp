# REVamp roadmap

This describes planned work, not current release behavior. Alpha.5 remains a
reliability and security release; remote controls follow only after its gate.

## Control surfaces

### Agent control

Any supported agent client may control playback only when the person has enabled
agent playback control in REVamp settings. The setting is off-safe, visible in
the player, and revocable at any time. Each action should be attributed to its
source in activity/diagnostic history.

### Voice control

Provide a local voice-control bridge with an explicit push-to-talk or user
controlled wake setting, a visible listening indicator, and a local-only
default. It must support basic transport, volume, and visible-list shuffle
without sending audio or library metadata to a third party unless the person
explicitly configures a provider.

### Phone and ESP32 controls

Provide a paired-device control bridge for a phone web remote and ESP32
hardware controls. Pairing must use a short-lived local pairing code and a
revocable per-device credential. The server must remain loopback-only by
default; an opt-in bridge may expose only authenticated control endpoints on a
local network, never the token-bearing player page or filesystem/media APIs.

Shared controls use the same playback command path as Hermes and agent MCP, so
the queue, volume, and source attribution remain consistent. A visible-list
Shuffle command receives an explicit list snapshot and cannot silently shuffle
the whole library.

## Release order

1. Finish alpha.5 council fixes, gate, CI, and install verification.
2. Complete the required zero verified HIGH/MEDIUM bug-hunt round.
3. Design and test the authenticated control bridge, then add phone/ESP32.
4. Add voice only after the local/privacy and consent behavior is testable.
