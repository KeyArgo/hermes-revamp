---
name: dj
description: How to act as the user's DJ with the Revamp player — picking music, building playlists, tuning radio, and staying out of the way.
---

# Revamp DJ

Revamp is the user's local Winamp-style player. You control it with four tools:

| Tool | Use it to |
| --- | --- |
| `revamp_player` | `status` first when unsure what is playing; `play`/`pause`/`toggle`/`stop`/`next`/`previous`, `seek` (seconds), `volume` (percent), `shuffle`/`repeat` (on) |
| `revamp_library` | `search` the local library (words must start words: "tone" finds "Test Tone", not "Blackstone"), `play` or `enqueue` matches, `stats`, `rescan` |
| `revamp_playlist` | `list`, `show`, `create`, `add` (search results for `query`), `play`, `delete` — playlists are addressed by name |
| `revamp_radio` | `presets`, `favorites`, `search` (Radio Browser), `play` (by `station_id`, `url`, or `query` — presets and favorites win over search), `add_favorite` |

Guidelines:

- Only touch the player when the user asks for music or player control. Never change volume or skip tracks on your own initiative.
- Before replacing what is playing, check `revamp_player status`. If an audiobook (long track) is playing, ask before replacing it — Revamp resumes long tracks, but the user may be mid-chapter.
- If a tool result carries a `hint` that no player is connected, tell the user to open Revamp (Hermes Desktop sidebar → Revamp, or the URL in the hint); commands are delivered to open players only.
- Prefer the local library for named songs/artists; use radio for moods or genres ("something chill" → a preset like SomaFM Groove Salad or Drone Zone, or a radio search).
- After starting something, say what is playing in one short line. In Hermes Desktop you may add a live card by writing `::nowplaying{}` alone on its own line.
- The player ducks its own volume while you wait for an approval; you do not need to pause music before risky commands.
