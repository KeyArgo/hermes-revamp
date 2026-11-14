# LIMITS

What REVamp does not do, stated plainly. If you need one of these, REVamp cannot give it to you
today.

## Not shipped at all

- **No streaming services.** No Spotify, Apple Music, YouTube Music, Tidal or similar. REVamp plays
  your own files and internet radio streams.
- **No media servers yet.** Plex, Jellyfin and Audiobookshelf are not supported. (An Audiobookshelf
  source and a SQLite library exist as unreleased work; neither ships in 1.0.0.)
- **No sync between computers.** Library, playlists, likes and audiobook places live on this
  computer only (with local backups every 10 minutes).
- **No phone app, remote control or voice control.** Those are plans in
  [docs/ROADMAP.md](docs/ROADMAP.md), not features.
- **No downloads or purchases.** REVamp never fetches music for you.

## Works, with conditions

- **Audiobook chapters need `ffprobe`** (part of FFmpeg). Tags, durations, resume and speed work
  without it; chapter lists inside `.m4b` files do not.
- **The desktop pop-out window needs a Chrome-family browser** (Chrome, Edge, Brave or Chromium).
  Every other place the player can live (mini player, Frame, floating in Hermes, full screen) does not.
- **One player per Hermes.** Several Hermes windows share one player; the first window plays and the
  others show its controls.
- **Radio depends on the stations.** A station can go offline, change its address or block some
  countries. Directory results come from Radio Browser, a community-run service.
- **Lyrics come from LRCLIB** and exist only for songs someone has added there.
- **The library index is held in memory.** It has not been measured on very large libraries; expect
  scans and searches to slow down as the collection grows.

## Will disappoint you if you expect it

- **It is not a tag editor or library manager.** REVamp reads tags; it never writes to your files.
- **Skins are classic Winamp 2 skins** (`.wsz`). Modern Winamp 3/5 skins do not load.
- **MilkDrop presets are Butterchurn's.** Some presets that rely on features Butterchurn lacks look
  different from the original Winamp plugin.

## Known test environment notes

- On Windows without Developer Mode, tests that build symlinks are skipped (they still run in CI).
- Without FFmpeg installed, tests that need real audio files are skipped; the rest of the suite
  still checks tags with hand-written ID3 files.
