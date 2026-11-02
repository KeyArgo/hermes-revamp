# Changelog

Test builds are numbered `1.0.0-alpha.N`. Each build gets a new number that is never reused, so you can tell which one you have: it shows in the corner of the REVamp page.

## 1.0.0-alpha.9 - 2026-11-02 (pre-release)

- Pop out to desktop works on Debian and Ubuntu again. Alpha.8 handed the window its private DevTools connection through a shell command that Debian's and Ubuntu's `/bin/sh` can't run when the server has many connections open, so the window could fail to open.

## 1.0.0-alpha.8 - 2026-11-02 (pre-release)

- Bringing the player back from its desktop window ("Dock it here", Frame, the mini player's "put in frame") no longer leaves a white Frame. Before, it only drew once you went full screen and back.
- Opening Hermes no longer flashes the full library and menus in the Frame before the classic windows appear.
- Security review, four more rounds (six in all, the last with nothing of medium severity or worse), each fix with a regression test:
  - A music file or cover image that is swapped for a link to one of your private files is refused, even if the swap happens at the moment it is opened.
  - Your REVamp key never goes through a proxy or follows a redirect, and a music source's login never follows a redirect to another site or down to plain http.
  - The key file is created safely, can't be read or written through a planted link, and is made private if an older copy was readable by others.
  - On Linux, connections from another account on the same computer are refused; WSL and containers keep working.
  - Playlist and station addresses are checked more strictly, and the Hermes bridge can't shut the player down or touch backups.
- Radio keeps playing stations whose secure link hands over to a plain-http stream (about 1 in 40 of the most-played ones).

## 1.0.0-alpha.7 - 2026-11-02 (pre-release)

- Full-screen MilkDrop is sharp again: with a graphics card it now draws at your screen's resolution instead of stretching a small picture about 7x. Machines without a graphics card keep the lighter stretched picture, so nothing gets slower there.

## 1.0.0-alpha.6 - 2026-11-02 (pre-release)

- Radio works again with today's Radio Browser: it retries dropped connections, connects over IPv4 first (with a fair chance for IPv6), and a failed page offers "Try again" instead of ending the list. Searching a word like "jazz" offers that genre.
- Popped out to the desktop, the player can be put back from Hermes: Frame, "Dock it here" and the mini player's "put in frame" close the desktop window and bring the player back.
- Moving the player no longer plays a moment from the start of the song: it reaches its place silently, then the sound comes back. Pausing, skipping or seeking during a move is respected, and an audiobook keeps its place.
- Playlists keep their own names on macOS and Windows.

## 1.0.0-alpha.5 - 2026-10-31 (pre-release)

- Reliability and security release: seven bug hunts fixed about 71 bugs, each with a regression test.
- Your REVamp key is never sent to other websites, even from an imported playlist or station.
- A music folder that is offline (unplugged drive, sleeping NAS) keeps its songs in the library instead of losing them.
- Backup restore and import undo themselves if they fail partway; the sleep timer and the approval dip no longer fight over the volume; the mini player's queue no longer freezes after a failed edit.
- Radio, server and tag-reading hardening: stricter address checks for stations, safer handling of malformed requests and broken music files.
- New automatic guards run on every change: junk input to every API route, fuzzed music files, and checks for unsafe page code.

## 1.0.0-alpha.4 - 2026-10-27 (pre-release)

- The Frame stays a Frame when hidden; it never silently collapses into the mini player.
- MilkDrop lives in the Winamp top-left menu in every player place, with Ctrl/Cmd+Shift+M as its shortcut.
- One server hands over cleanly during upgrades, and the release gate now covers the complete cross-platform test path.
- Added calmer visualizer defaults, lower hidden-player work, media tags without FFmpeg, Hermes Radio navigation, improved install verification, and player/place controls.

## 1.0.0-alpha.3 - 2026-10-27 (pre-release)

- Narrow-pane layout, pane scaling, theme Looks and animated backdrops (community PR #1 by @Pukujan).
- Library mode: Play, queueing, radio and skin changes on the full page now drive the player in the Frame or pop-out (Play did nothing before).
- The approval dip and its pop-up only happen while music is playing.
- Settings → REVamp → Music and approvals controls the dip and its pop-up.

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
