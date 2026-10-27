# Changelog

Test builds are numbered `1.0.0-alpha.N`. Each build gets a new number that is never reused, so you can tell which one you have: it shows in the corner of the REVamp page.

## 1.0.0-alpha.2 - 2026-10-27 (pre-release)

- REVamp's desktop part (sidebar entry, player, status-bar controls) is now switched on by default after installing. In alpha.1 it shipped switched off, so testers on macOS and Windows saw no player.
- README explains the two parts in Capabilities → Plugins (agent part and Desktop part).
- Radio: when Radio Browser can't be reached, the built-in stations show within seconds instead of an error.

## 1.0.0-alpha.1 - 2026-10-27 (pre-release)

- First alpha of REVamp for Hermes Agent.
- Radio: endless scrolling like Music; browse by genre, country and language; a Popular list ranked by votes from working, de-duplicated stations, with well-known stations first; fallback Radio Browser servers and an offline list.
- Five player places in one menu: Mini player, Frame (right or left, right by default), Pop into Hermes, Pop out to desktop, Full screen.
- Pop out to desktop: the window fits the player exactly, MilkDrop can be switched on beside it, it uses the graphics card on Linux Wayland, and it shows the REVamp icon in the taskbar.
- The full REVamp page works as a library while the player stays in the Frame or a pop-out.
- REVamp's own menu (Music, Books, Radio, Playlists, Skins, Settings) stays visible inside Hermes, as an icon rail on narrow windows.
- REVamp Nous and Nous Dark skins redrawn as one design: equalizer sliders and graph fixed in every bundled skin.
- New playlists appear on the Playlists page right away.
- Plays in other agent apps too: Claude Code (now-playing bar with play/pause), Codex, opencode, Cursor, Antigravity, pi and omp (via `revampctl mcp` or their hooks); choose which apps show REVamp in Settings.
- MilkDrop sits beside the classic player (or below it when docked) at the same height, opens full screen on double-click, and rests when out of sight.
- Right-click menus: songs in the mini player (play now, play next, add to queue) and selected songs on the REVamp page (play, queue, like, add to playlist).
- Lighter on the CPU under software graphics (common for Hermes on NVIDIA Linux): no always-on blur, idle spectrum hidden, MilkDrop off by default there, smoother pop-out dragging.
- 46 bug-hunt fixes, each with a test (backups kept 14 days, failed saves reported, Webamp settings saved, Focus and media keys, radio paging, accessibility, and more).
- The player updates itself: after an install it reloads on its own when nothing is playing (or says it will once playback stops). Page files are served no-cache. New command "REVamp: Reload Player" (Ctrl+Alt+R).
- Liked is editable: × un-hearts a song and the arrows reorder the list (no more "Could not save playlist").
- "Save as playlist" on Liked makes a normal copy you can edit freely; "＋ New playlist" makes an empty one.
- Music has "Add to playlist…" for the selected songs (or all shown), into an existing or a new playlist.
- Fixed: after any dialog closed, the next one on the page never appeared (a second rename/delete did nothing until reload).
- Popped out, dragging the classic REVAMP title bar (or any bar the classic player drags by) moves the whole floating window, like the Hermes bar above it, instead of sliding the player around inside it.
- REVamp no longer goes "not running" after Hermes restarts: if a short-lived Hermes process (a cron run, a CLI call) briefly owned the player server and exited, the gateway now takes the server back within about 5 seconds.
- Everything the app shows says REVamp; the pop-out command is now "Show / Hide Classic Player".
- Overlong playlist names and wrongly typed settings are rejected with a clear error instead of being saved.

- Public release of REVamp for Hermes Agent.
- Adds separate music and audiobook folders, book resume positions, chapters, and playback speed.
- Adds Radio Browser search, station presets and favourites, editable playlists and queue, likes, sleep timer, Focus drone, and synced lyrics.
- Adds the Winamp Skin Museum, desktop pop-out, media controls, and Hermes agent tools.
- Ships the public install docs, local-first privacy disclosure, extension guide, release artwork, and fresh-install verification.

## 0.1.0 - 2026-10-21

- First preview release. It introduced the Hermes player, local library, playlists, radio, persistent player state, and initial agent integration.
