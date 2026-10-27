/**
 * Revamp — desktop half of the Revamp Hermes plugin (a Winamp clone).
 *
 * The player plays INSIDE Hermes: this plugin owns one live Revamp player (the
 * web app served by the Python half on 127.0.0.1, default :8114) for as long
 * as Hermes is open, and moves that same player between three places without
 * reloading it (Element.moveBefore keeps an iframe's state):
 *
 *   dock    — off-screen; music keeps playing, status-bar controls work
 *   page    — the full Revamp UI on the /revamp page (sidebar → Revamp)
 *   winamp  — "pop out": just the classic Winamp windows, floating over
 *             Hermes, draggable, everything else stays clickable
 *
 * The status bar is the everyday surface, laid out like the bundled Radio
 * plugin so Revamp can replace it: an audio-reactive braille waveform, a
 * picker (radio stations, your library, playlists — with search and pins),
 * now-playing, and transport with volume. Only one Hermes window runs the
 * player (a Web Lock elects it; the next window takes over when it closes),
 * and Revamp and the bundled Radio pause each other instead of playing over
 * one another.
 *
 * No side pane on purpose: closing a plugin's only pane disables the plugin,
 * which would stop the music.
 *
 * Data path: ctx.rest('/info' | '/state' | '/command' | '/library/…' |
 * '/radio/…' | '/playlists…') → dashboard/plugin_api.py, which proxies to the
 * local Revamp server with its token. The renderer never sees the token.
 *
 * Plain ESM, loaded uncompiled: jsx() calls only; imports limited to
 * @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import {
  atom,
  Button,
  createBudgetedLoop,
  EmptyState,
  GlyphSpinner,
  host,
  icons,
  KEYBINDS_AREA,
  PANES_AREA,
  PALETTE_AREA,
  Popover,
  PopoverContent,
  PopoverTrigger,
  ROUTES_AREA,
  RowButton,
  SearchField,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  StatusDot,
  Tip,
  TRANSCRIPT_DIRECTIVE_AREA,
  usePluginI18n,
  useQuery,
  useValue
} from '@hermes/plugin-sdk'
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'revamp'
const ROUTE = '/revamp'
// The page renders at this size wherever it is not on the full page; the
// winamp overlay shows only the part that holds the classic windows.
const DOCK_W = 900
const DOCK_H = 1000
// Research spike only: prove a keep-alive Hermes pane can be the classic
// player's home. Leave this true for the manual test; remove the registration
// (or set false) before shipping a different pane design.
const PANE_SPIKE = true
// The bundled Radio plugin's cross-window channel: it stops itself when any
// other sender announces {type: 'play'}, and announces its own plays there.
const RADIO_BUS = 'hermes:radio:playback'

const EN = {
  title: 'REVamp',
  navLabel: 'REVamp',
  offline: 'REVamp is not running',
  speed: 'Playback speed',
  reconnect: 'Reconnect',
  reconnectHint: 'Reloads the player. Your queue and place are kept.',
  offlineHint:
    'Enable the REVamp Python plugin (add "revamp" to plugins.enabled in config.yaml) and restart Hermes. The player is served on 127.0.0.1.',
  starting: 'Starting the player…',
  inWinamp: 'The player is popped out as Winamp.',
  elsewhere: 'The player is running in another Hermes window. The controls here still work.',
  bringBack: 'Bring it back here',
  nothing: 'Nothing playing',
  browse: 'Choose music',
  play: 'Play',
  pause: 'Pause',
  prev: 'Previous',
  next: 'Next',
  stop: 'Stop',
  mute: 'Mute',
  unmute: 'Unmute',
  volume: 'Volume',
  popOut: 'Pop out as Winamp (Shift+Ctrl+click: its own desktop window)',
  onDesktop: 'REVamp is playing in its own desktop window. Close that window, or use the button, to bring it back.',
  bringBackDesktop: 'Bring REVamp back into Hermes',
  cmdDesktop: 'REVamp: Open on Desktop / Bring Back',
  popIn: 'Hide player: music keeps playing',
  openFull: 'Open the full player',
  dock: 'Dock player',
  popOutPlayer: 'Pop out player',
  dockedHere: 'Dock it here',
  playingIn: 'REVamp is playing in the',
  showHere: 'Show the player here',
  radio: 'Radio',
  library: 'Music',
  books: 'Books',
  playlists: 'Playlists',
  searchRadio: 'Find a station, or pick a genre…',
  searchLibrary: 'Search your music…',
  searchBooks: 'Search your audiobooks…',
  searchPlaylists: 'Filter playlists…',
  folders: 'Music folders',
  bookFolders: 'Book folders',
  foldersHint: 'REVamp plays what is in these folders (and their subfolders).',
  bookFoldersHint: 'Audiobooks in these folders remember where you stopped.',
  foldersDefault: 'Using the default from config.yaml.',
  foldersNone: 'No folders yet.',
  inMusic: 'Already a Music folder',
  inBooks: 'Already a Books folder',
  chooseFolders: 'Choose music folders',
  chooseBookFolders: 'Choose book folders',
  addFolder: 'Add a folder…',
  addMusic: 'Add to Music',
  addBooks: 'Add to Books',
  removeFolder: 'Stop using this folder',
  leftOff: 'left off at {time}',
  upLevel: 'Up one level',
  done: 'Done',
  scanning: 'Scanning your music…',
  scanningOf: 'Scanning your music… {done} of {total} files (songs appear as they are read)',
  goTo: 'Go',
  pathPlaceholder: 'Type a folder path, e.g. /mnt/nas/music',
  home: 'Home',
  missing: 'folder not found',
  emptyBooks: 'No audiobooks yet. Use the folder button to choose the folder that holds them.',
  backendOld: 'REVamp’s server is running an older version. Restart Hermes (or run: hermes gateway restart) to finish the update.',
  failed: 'That didn’t work',
  genre: 'Genre',
  searching: 'Searching',
  loadingMore: 'Loading more…',
  noResults: 'Nothing found',
  emptyLibrary: 'No music here yet. Use the folder button to choose where your music is.',
  emptyPlaylists: 'No playlists yet. Save the queue from the full player.',
  searchError: 'Station search is unavailable. Your presets still work.',
  pin: 'Pin station',
  unpin: 'Unpin station',
  rowMenu: 'Actions',
  playNow: 'Play now',
  playNext: 'Play next',
  addToQueue: 'Add to queue',
  addedToQueue: 'Added to queue',
  addedNext: 'Will play next',
  live: 'Live',
  paused: 'Paused',
  stopped: 'Stopped',
  tracks: 'tracks',
  focus: 'Focus',
  focusStart: 'Start Focus',
  focusStop: 'Stop Focus',
  focusPulse: 'Pulse',
  focusTone: 'Tone',
  focusNoise: 'Noise',
  focusTimer: 'Timer',
  focusTimerOff: 'Off',
  focusMinutes: '{n} min',
  focusIdle: 'A steady drone with no melody or beat.',
  focusNote: 'The pulse may help some people focus; turn it down if you notice it.',
  cmdToggle: 'REVamp: Play / Pause',
  cmdNext: 'REVamp: Next Track',
  cmdPrev: 'REVamp: Previous Track',
  cmdStop: 'REVamp: Stop',
  cmdOpen: 'REVamp: Open Player',
  cmdWinamp: 'REVamp: Pop Out Player',
  cmdDock: 'REVamp: Show Docked Player',
  cmdMini: 'REVamp: Mini Player Only',
  location: 'Player location',
  locDocked: 'Frame',
  locFloating: 'Pop into Hermes',
  locMini: 'Mini player',
  locDesktop: 'Pop out to desktop',
  locFull: 'Full screen',
  dockedPlayerSide: 'Docked player side',
  dockedPlayerRight: 'Right',
  dockedPlayerLeft: 'Left',
  duckTitle: 'Music and approvals',
  duckOn: 'Lower the music when Hermes needs your approval',
  duckLevel: 'Lower it to',
  duckToast: 'Show a pop-up saying why',
  on: 'On',
  off: 'Off',
  showDocked: 'Show the docked player',
  hideDocked: 'Hide the docked player (mini only)',
  cmdReload: 'REVamp: Reload Player',
  cmdVisualizer: 'REVamp: Show / Hide Visualizer',
  cmdBrowse: 'REVamp: Choose Music',
  category: 'REVamp',
  like: 'Like',
  unlike: 'Unlike',
  liked: 'Liked',
  likedEmpty: 'Tap the heart on a playing track to add it here',
  shuffle: 'Shuffle',
  repeat: 'Repeat',
  back10: 'Back 10 seconds',
  fwd10: 'Forward 10 seconds',
  queueSleep: 'Queue and sleep timer',
  upNext: 'Queue',
  back: 'Back',
  emptyQueue: 'The queue is empty. Pick something to play.',
  playFrom: 'Play from here',
  moveUp: 'Move up',
  moveDown: 'Move down',
  removeItem: 'Remove from queue',
  clearQueue: 'Clear',
  clearAsk: 'Clear the whole queue?',
  sleep: 'Sleep timer',
  sleepOff: 'Off',
  sleepMin: '{n} min',
  sleepEnd: 'End of track',
  sleepLeft: 'sleep {time}',
  sleepTrack: 'sleep: end of track',
  rename: 'Rename',
  deletePl: 'Delete',
  deleteAsk: 'Delete “{name}”?',
  yes: 'Yes',
  no: 'No',
  save: 'Save',
  cancel: 'Cancel'
}

// Disk plugins are not scanned by Tailwind. Only plugin layout lives here;
// Button, SearchField and Popover own their own chrome. Theme tokens only.
const CSS = `
.revamp-bar{display:flex;align-items:center;gap:2px;height:100%;color:var(--ui-text-tertiary)}
.revamp-bar .revamp-name{max-width:150px;min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}
.revamp-bar[data-playing=true] .revamp-name{color:var(--ui-text-secondary)}
.revamp-action{width:16px;height:16px;padding:0;flex-shrink:0}
.revamp-action-icon{display:flex;align-items:center;justify-content:center;width:12px;height:12px;flex-shrink:0;overflow:hidden}
.revamp-skip{display:flex;align-items:center;width:12px;height:12px}
.revamp-skip[data-dir=prev]{transform:scaleX(-1)}
.revamp-wave{position:relative;height:24px;width:100%;font-family:var(--font-mono,monospace);font-size:12px;line-height:12px;color:var(--ui-text-tertiary);opacity:.45;overflow:hidden;flex-shrink:0}
.revamp-wave-layer{position:absolute;inset:0;display:grid;grid-template-columns:repeat(var(--revamp-cols),minmax(0,1fr));grid-auto-rows:12px}
.revamp-wave-layer>span{text-align:center;min-width:0}
.revamp-wave-layer:nth-child(1){opacity:.12}
.revamp-wave-layer:nth-child(2){opacity:.28}
.revamp-wave[data-compact=true]{width:40px;height:12px;margin-inline:2px}
.revamp-wave[data-active=true]{color:var(--ui-accent);opacity:1}
.revamp-panel{width:320px;max-width:calc(100vw - 24px)}
.revamp-tabs{padding:4px 4px 2px}
.revamp-search{display:flex;align-items:center;height:32px;padding:0 4px 4px}
.revamp-search-field{flex:1;min-width:0}
.revamp-search-field input{flex:1;width:0;min-width:0}
.revamp-search .revamp-search-field{border:0}
.revamp-loader{display:flex;align-items:center;justify-content:center;width:16px;flex-shrink:0;font-size:12px;color:var(--ui-text-tertiary)}
.revamp-list{height:196px;max-height:calc(100dvh - 220px);min-height:80px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}
.revamp-row{display:flex;align-items:center;gap:4px;border-radius:4px}
.revamp-row:hover,.revamp-row[data-current=true]{background:var(--chrome-action-hover)}
.revamp-row[data-current=true] .revamp-row-name{color:var(--ui-accent)}
.revamp-row [aria-pressed=true]{color:var(--ui-accent)}
.revamp-pin{opacity:0;pointer-events:none}
.revamp-row:hover .revamp-pin,.revamp-row:focus-within .revamp-pin,.revamp-row[data-pinned=true] .revamp-pin{opacity:1;pointer-events:auto}
@media(hover:none){.revamp-pin{opacity:1;pointer-events:auto}}
.revamp-row-main{display:flex;align-items:center;gap:8px;flex:1;min-width:0;text-align:left;height:28px;padding:2px 6px;cursor:pointer;border-radius:4px}
.revamp-row-main:focus-visible{outline:1px solid var(--ui-accent);outline-offset:-1px}
.revamp-row-icon{display:flex;align-items:center;justify-content:center;width:14px;flex-shrink:0;color:var(--ui-text-tertiary);opacity:0}
.revamp-row:hover .revamp-row-icon,.revamp-row:focus-within .revamp-row-icon,.revamp-row[data-current=true] .revamp-row-icon{opacity:1}
.revamp-row[data-current=true] .revamp-row-icon{color:var(--ui-accent)}
.revamp-row-copy{display:flex;flex-direction:column;min-width:0;flex:1}
.revamp-row-name{display:block;font-size:12px;line-height:15px;color:var(--ui-text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.revamp-row-sub{display:block;font-size:10px;line-height:12px;color:var(--ui-text-tertiary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.revamp-menu-pop{padding:0;width:auto;min-width:0}.revamp-row{position:relative}.revamp-menu{display:flex;flex-direction:column;min-width:150px;padding:4px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:var(--ui-bg-elevated,var(--ui-surface-primary,#1c1c1c));box-shadow:0 6px 20px rgba(0,0,0,.35)}
.revamp-menu-item{display:block;width:100%;text-align:left;font-size:12px;line-height:24px;padding:0 8px;border:0;border-radius:4px;color:var(--ui-text-primary);background:none;cursor:pointer}
.revamp-menu-item:hover,.revamp-menu-item:focus-visible{background:var(--chrome-action-hover);outline:none}
.revamp-load-more{height:1px}
.revamp-signal{height:48px;padding:5px 6px 0;min-width:0}
.revamp-track{display:flex;align-items:center;gap:5px;height:19px;min-width:0;font-size:11px;line-height:16px;color:var(--ui-text-secondary)}
.revamp-track-text{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;min-width:0}
.revamp-track[data-track=true]{color:var(--ui-text-primary)}
.revamp-transport{display:flex;align-items:center;gap:4px;height:28px;padding:2px 2px 0}
.revamp-space{flex:1;min-width:6px}
.revamp-transport input{width:76px;min-width:0;height:3px;accent-color:var(--ui-accent);cursor:pointer}
.revamp-note{font-size:11px;line-height:17px;color:var(--ui-text-secondary);padding:4px 6px 12px}
.revamp-focus{padding:6px 8px 4px;display:flex;flex-direction:column;gap:6px}
.revamp-focus-row{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--ui-text-secondary)}
.revamp-focus-row>span{width:42px;flex-shrink:0}
.revamp-focus-row input{flex:1;min-width:0;height:3px;accent-color:var(--ui-accent);cursor:pointer}
.revamp-chips{display:flex;flex-wrap:wrap;gap:4px;padding:2px 6px 6px}
.revamp-chip{font-size:11px;line-height:18px;padding:0 8px;border-radius:9px;border:1px solid var(--ui-stroke-secondary);color:var(--ui-text-secondary);cursor:pointer;background:none}
.revamp-chip:hover,.revamp-chip[aria-pressed=true]{color:var(--ui-accent);border-color:var(--ui-accent)}
.revamp-folders-head{display:flex;align-items:center;gap:4px;height:32px;padding:0 4px 4px 8px;font-size:12px;color:var(--ui-text-primary)}
.revamp-folders-head .revamp-space{flex:1}
.revamp-path{font-size:10px;line-height:14px;color:var(--ui-text-tertiary);padding:0 8px 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:rtl;text-align:left}
.revamp-path bdi{direction:ltr;unicode-bidi:isolate}
.revamp-row[data-missing=true] .revamp-row-sub{color:var(--ui-danger,var(--ui-text-tertiary))}
.revamp-row-actions{display:flex;align-items:center;gap:2px;padding-right:4px}
.revamp-goto{display:flex;gap:4px;padding:0 6px 4px}
.revamp-goto input{flex:1;min-width:0;height:24px;padding:0 6px;font:11px/24px var(--font-mono,monospace);color:var(--ui-text-primary);background:transparent;border:1px solid var(--ui-stroke-secondary);border-radius:4px}
.revamp-goto input:focus{outline:1px solid var(--ui-accent);outline-offset:-1px}
.revamp-jumps{display:flex;gap:4px;padding:0 6px 4px}
.revamp-acts{display:flex;align-items:center;gap:1px;opacity:0;pointer-events:none}
.revamp-row:hover .revamp-acts,.revamp-row:focus-within .revamp-acts{opacity:1;pointer-events:auto}
@media(hover:none){.revamp-acts{opacity:1;pointer-events:auto}}
.revamp-ask{display:flex;align-items:center;gap:4px;flex:1;min-width:0;height:28px;padding:2px 6px;font-size:12px;color:var(--ui-text-primary)}
.revamp-ask>span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.revamp-ask input{flex:1;min-width:0;height:22px;padding:0 6px;font:12px/22px inherit;color:var(--ui-text-primary);background:transparent;border:1px solid var(--ui-stroke-secondary);border-radius:4px}
.revamp-ask input:focus{outline:1px solid var(--ui-accent);outline-offset:-1px}
.revamp-ask button{font-size:11px;line-height:18px;padding:0 8px;border-radius:9px;border:1px solid var(--ui-stroke-secondary);color:var(--ui-text-secondary);background:none;cursor:pointer}
.revamp-ask button:hover,.revamp-ask button[data-danger=true]{color:var(--ui-accent);border-color:var(--ui-accent)}
.revamp-track .revamp-track-side{display:flex;align-items:center;gap:2px;margin-left:auto;flex-shrink:0}
.revamp-sleep-left{font-size:10px;font-family:var(--font-mono,monospace);color:var(--ui-accent);white-space:nowrap}
.revamp-like[aria-pressed=true]{color:var(--ui-accent)}
.revamp-transport[data-extended=true] input{width:52px}
.revamp-qhead{display:flex;align-items:center;gap:4px;height:32px;padding:0 4px 0 8px;font-size:12px;color:var(--ui-text-primary)}
.revamp-qhead .revamp-space{flex:1}
.revamp-num{font-size:10px;color:var(--ui-text-tertiary);width:18px;text-align:right;flex-shrink:0;font-family:var(--font-mono,monospace)}
.revamp-select{height:20px;max-width:110px;font-size:11px;color:var(--ui-text-secondary);background:transparent;border:1px solid var(--ui-stroke-secondary);border-radius:4px}
`

// ---------------------------------------------------------------- module state
// Reset by register() so a hot reload starts clean.
let ctxRef = null
let engine = null
const $engineVersion = atom(0)
let lastPetTitle = ''
let lastPetAt = 0
// The latest waveform samples from the player page (hot path: not an atom).
let waveSamples = null
// Where the live player is: 'none' (not started) | 'dock' | 'page' | 'winamp' | 'elsewhere'
// (another Hermes window runs it) | 'desktop' (its own desktop window runs it; this one is parked).
const $where = atom('none')
// Legacy view of the location above, kept for existing UI: true only while the
// player lives in the floating overlay. Derived from $playerLocation.
const $winampOpen = atom(false)
// The player's home, chosen in the pane header / mini menu / palette / settings / agent:
// 'docked' (the right-hand pane, default) | 'floating' (the pop-out) | 'mini' (bottom bar only).
// Desktop is a temporary action, never a saved home.
const $playerLocation = atom('docked')
// An explicit “Bring the player here” borrow. Opening the page normally respects
// docked/floating/desktop placement; mini remains the page-first location.
const $pageBorrow = atom(true)
let pageBorrowRequested = false
// The player is in its own desktop window (server-launched app window).
const $desktop = atom(false)
// GET /state, polled once for every surface; null while the server is down.
const $live = atom(null)
const $open = atom(false)
// Bumped when a library rescan finishes so track lists refetch; true while scanning.
const $libVersion = atom(0)
const $scanning = atom(false)
const $scanProgress = atom(null) // { done, total } while a scan runs
// The popover shows the queue + sleep timer instead of the picker.
const $queueOpen = atom(false)
// Latest toast from the player page (duck notices while docked to mini, where the
// iframe's own toasts are invisible): { text, kind, seq } or null.
const $barToast = atom(null)
let barToastSeq = 0
// Liked local track ids (GET /likes); null until loaded.
const $liked = atom(null)

// Two ways to reach the Revamp server: through the live player iframe (it has its own
// direct, token-authenticated connection) or through the Hermes gateway (ctx.rest). The
// gateway's connection can go stale while the player keeps playing, so the bridge goes
// first. Reads fall back to the other route; writes don't (a command that timed out may
// already have run, and sending it twice would skip two tracks).
let bridge = null // set by createEngine; { ready, request(path, opts) }
const $reachable = atom(true) // false after several failed refreshes: show Reconnect
async function rest(path, opts = {}) {
  if (!ctxRef) throw new Error('revamp: not registered')
  const read = !opts.method || opts.method === 'GET'
  if (bridge?.ready && path !== '/info') {
    try {
      return await bridge.request(path, opts)
    } catch (err) {
      if (err?.answered || !read) throw err // the server itself said no, or a write: don't resend
    }
  }
  return ctxRef.rest(path, opts)
}

function command(action, extra = {}) {
  return rest('/command', { method: 'POST', body: { action, ...extra }, timeoutMs: 5000 }).catch(err => {
    host.notify({ kind: 'error', message: `REVamp: ${err?.message || 'command failed'}` })
  })
}

// ---------------------------------------------------------------- player location
const PLAYER_LOCATIONS = ['docked', 'floating', 'mini']
// Pane placement is persisted by Hermes against the contribution id.  Keep one
// id per requested edge so changing this setting gets the new edge's initial
// dock instead of reviving the user's old layout for the other edge.
const DOCKED_PLAYER_SIDES = ['right', 'left']
const PANE_IDS = { right: 'classic-pane-right', left: 'classic-pane-left' }
const scopedPaneId = side => `${ID}:${PANE_IDS[side]}`
const $dockedPlayerSide = atom('right')
let disposeDockedPlayerPane = null
// Below this width the docked pane would squeeze the chat: mini behaves as docked
// (placement only — the saved choice never changes).
const DOCKED_MIN_WIDTH = 1100

// 'docked' collapses to 'mini' on narrow windows, for placement only.
function placementLocation() {
  const loc = $playerLocation.get()
  // The user's pick always wins: a narrow window still gets its frame (at the pane's min width).
  return loc
}

// The one way every control changes the player's home. Validates, persists, re-places.
// The iframe itself is never recreated: place() moves the same node (moveBefore).
const LOC_BUS = 'revamp:location' // every window hears picks made in any other

function setPlayerLocation(loc, fromBus = false) {
  if (!PLAYER_LOCATIONS.includes(loc)) return
  // Picking a place away from the page releases an explicit page borrow immediately.
  if ($where.get() === 'page' && loc !== 'mini') $pageBorrow.set(false)
  const changed = loc !== $playerLocation.get()
  if (changed) {
    ctxRef?.storage.set('playerLocation', loc)
    $playerLocation.set(loc)
    $winampOpen.set(loc === 'floating') // legacy view, kept in sync for existing UI
  }
  // Other windows follow the pick (they have no engine: engine?.place() is a no-op there).
  // The sender echoes too: its own atoms are already set, the echo is a cheap no-op.
  try { locBus?.postMessage({ loc, from: locSender }) } catch { /* channel closed */ }
  if (loc === 'docked') {
    // Even when already docked the pane may be hidden: showing it again is the point.
    try { host.revealPane?.(scopedPaneId($dockedPlayerSide.get())) } catch { /* older hosts: no revealPane */ }
  }
  if (!fromBus) engine?.place()
  else if (changed || engine) engine?.place()
}

function dockedPlayerPane(side) {
  return {
    id: PANE_IDS[side],
    area: PANES_AREA,
    title: 'REVamp Player',
    data: {
      placement: side,
      dock: { pane: 'workspace', pos: side },
      width: '289px',
      minWidth: '279px',
      maxWidth: '600px',
      headerContent: () => jsx(PaneHeaderButtons, {}),
      lifecycleKeepAlive: true,
      // Closing a plugin's only pane disables the whole plugin (and stops the music): offer
      // Hermes's show/hide instead of close (pane-shell track-model.ts hideOnly).
      hideOnly: true
    },
    render: () => jsx(ClassicPaneSpike, {})
  }
}

// A contribution registration has a public disposer. Removing the old side
// first makes its unregistered layout branch collapse; the iframe belongs to
// the engine, not the pane React tree, so its audio keeps playing while the new
// keep-alive pane mounts and adopts it.
function applyDockedPlayerSide(side) {
  if (!DOCKED_PLAYER_SIDES.includes(side) || !ctxRef) return
  const changed = side !== $dockedPlayerSide.get()
  if (changed) {
    ctxRef.storage.set('dockedPlayerSide', side)
    $dockedPlayerSide.set(side)
  }
  if (!changed || !PANE_SPIKE) return
  disposeDockedPlayerPane?.()
  disposeDockedPlayerPane = ctxRef.register(dockedPlayerPane(side))
  // The new pane mounts asynchronously. Its showPane() calls place() again;
  // this immediate pass only parks the same iframe safely in the mini dock.
  if ($playerLocation.get() === 'docked') {
    try { host.revealPane?.(scopedPaneId(side)) } catch { /* older hosts: no revealPane */ }
    engine?.place()
  }
}

// A status the user just clicked, held until the server agrees or 4 s pass,
// so a poll already in flight can't flip the button back.
let heldStatus = null

function withHeld(state) {
  state = withHeldExtras(state)
  const np = state?.live?.now_playing
  if (!heldStatus || !np) return state
  if (Date.now() > heldStatus.until || np.status === heldStatus.status) {
    heldStatus = null
    return state
  }
  return { ...state, live: { ...state.live, now_playing: { ...np, status: heldStatus.status } } }
}

// The same for shuffle/repeat (now_playing fields) and a queue edit (the saved playlist):
// what was clicked shows at once and stays until the player reports it, or 3 s pass.
let heldNp = {} // field -> { value, until }
let heldQueue = null // { playlist, index, until }
let queueEdits = Promise.resolve()
const queueKey = list => JSON.stringify((list || []).map(it => it?.id || it?.track_id || it?.url))
function withHeldExtras(state) {
  const np = state?.live?.now_playing
  if (!state || !np) return state
  const now = Date.now()
  let outNp = np
  for (const [k, h] of Object.entries(heldNp)) {
    if (now > h.until || np[k] === h.value) delete heldNp[k]
    else outNp = { ...outNp, [k]: h.value }
  }
  let playlist = state.playlist
  if (heldQueue) {
    if (now > heldQueue.until || queueKey(playlist) === queueKey(heldQueue.playlist)) heldQueue = null
    else {
      playlist = heldQueue.playlist
      outNp = { ...outNp, index: heldQueue.index }
    }
  }
  return outNp === np && playlist === state.playlist ? state : { ...state, playlist, live: { ...state.live, now_playing: outNp } }
}

function holdNp(field, value) {
  heldNp[field] = { value, until: Date.now() + 3000 }
  $live.set(withHeld($live.get()))
}

let likesVersion = 0
async function loadLikes() {
  const version = likesVersion
  try {
    const out = await rest('/likes', { timeoutMs: 4000 })
    if (version === likesVersion) $liked.set(new Set(out?.ids || []))
  } catch {}
}

async function setLiked(trackId, liked) {
  if (!trackId) return
  const version = ++likesVersion
  const before = $liked.get()
  const next = new Set(before || [])
  if (liked) next.add(trackId); else next.delete(trackId)
  $liked.set(next)
  try {
    const out = await rest('/likes', { method: 'POST', body: { track_id: trackId, liked }, timeoutMs: 5000 })
    if (version === likesVersion) $liked.set(new Set(out?.ids || []))
  } catch (err) {
    if (version === likesVersion) $liked.set(before)
    host.notify({ kind: 'error', message: `REVamp: ${err?.message || 'like failed'}` })
  }
}

let failedRefreshes = 0
async function reconnectPlayer() {
  failedRefreshes = 0
  engine?.reload()
  wakeEngineRetry?.()
  await new Promise(resolve => setTimeout(resolve, 2500))
  await refreshLive()
}
let refreshGeneration = 0
async function refreshLive() {
  const generation = ++refreshGeneration
  try {
    const state = await rest('/state', { timeoutMs: 4000 })
    if (generation === refreshGeneration) {
      $live.set(withHeld(state))
      failedRefreshes = 0
      if (!$reachable.get()) $reachable.set(true)
    }
  } catch {
    if (generation === refreshGeneration) {
      $live.set(null)
      if (++failedRefreshes >= 3 && $reachable.get()) $reachable.set(false)
    }
  }
  // Polls overlap; only the newest one (started after the last open/close) may act.
  const mine = ++deskSeq
  try {
    const win = await rest('/window', { timeoutMs: 4000 })
    if (mine === deskSeq && !deskBusy && typeof win?.open === 'boolean' && win.open !== $desktop.get()) $desktop.set(win.open)
  } catch {
    // Older server without desktop windows: nothing to track.
  }
}
let deskSeq = 0
let deskBusy = false // an open/close is in flight: polls must not decide meanwhile

// Shift+Ctrl+click on Pop out (or the palette): the player moves to its own
// desktop window and this Hermes player parks. Closing that window brings it back.
async function openOnDesktop() {
  const s = $live.get()
  deskBusy = true
  try {
    await rest('/window/open', {
      method: 'POST', timeoutMs: 10000,
      body: { position: Number(s?.live?.now_playing?.position) || 0, playing: nowPlaying(s).playing }
    })
    $desktop.set(true)
  } catch (err) {
    host.notify({ kind: 'error', message: `REVamp: ${err?.message || 'could not open the desktop window'}` })
  } finally {
    deskSeq++ // polls begun during the open are stale
    deskBusy = false
  }
}

async function bringBackFromDesktop() {
  deskBusy = true
  try {
    await rest('/window/close', { method: 'POST', timeoutMs: 10000 })
    const win = await rest('/window', { timeoutMs: 4000 })
    // Only resume here once the window is really gone: never two players.
    if (win?.open === false) { $desktop.set(false); engine?.place(); return }
  } catch {
  } finally {
    deskSeq++ // polls begun during the close are stale
    deskBusy = false
  }
  host.notify({ kind: 'error', message: 'REVamp: could not close the desktop window. Close it by hand and REVamp comes back here.' })
}

// Play now: replace the queue and start at `index`. Two commands on one SSE
// stream arrive in order, so play_index lands after the new queue.
async function playItems(items, index = 0) {
  if (!items.length) return
  await command('load', { items, mode: 'replace', play: index === 0 })
  if (index > 0) await command('play_index', { index })
  setTimeout(refreshLive, 600)
}

function stationItem(s) {
  return { kind: 'stream', title: s.name, artist: 'Radio', url: s.url, duration: null, track_id: null, station_id: s.id }
}

function trackItem(t) {
  const item = { kind: 'track', title: t.title, artist: t.artist, url: t.url, duration: t.duration, track_id: t.id, station_id: null }
  return t.kind ? { ...item, book: t.kind === 'audiobook' } : item
}

const clock = sec => {
  const s = Math.max(0, Math.floor(sec)), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60
  return `${h ? `${h}:${String(m).padStart(2, '0')}` : m}:${String(s % 60).padStart(2, '0')}`
}

// ---------------------------------------------------------------- the live player
// One sandboxed iframe, created once, moved between containers. moveBefore()
// keeps it running; appendChild() would reload it (state is server-side, so
// even the fallback only costs a short gap).
// moveBefore keeps the iframe (and the music) alive, but it throws unless both
// ends are in the document. A plain append reloads the player, but never crashes.
function moveInto(target, node) {
  if (node.parentNode === target) return
  if (typeof target.moveBefore === 'function' && node.isConnected && target.isConnected) {
    try {
      target.moveBefore(node, null)
      return
    } catch {}
  }
  target.appendChild(node)
}

function createEngine(ctx, url, bridgeKey = '') {
  const frame = document.createElement('iframe')
  frame.title = 'REVamp player'
  frame.src = `${url}/?embed=1`
  // Same posture as the SDK's SandboxedFrame: opaque origin, no popups, no top navigation.
  frame.setAttribute('sandbox', 'allow-scripts allow-forms allow-downloads')
  frame.setAttribute('referrerpolicy', 'no-referrer')
  frame.setAttribute('allow', 'autoplay; fullscreen')
  frame.setAttribute('allowfullscreen', '')
  frame.style.cssText = `border:0;display:block;width:${DOCK_W}px;height:${DOCK_H}px;background:transparent`

  // The route gets a second, deliberately audio-free copy of the full library
  // only when the live iframe is intentionally in Frame/Pop-out/Desktop.
  // `?control=1` boots the shell but never imports Webamp or creates audio.
  const controlFrame = document.createElement('iframe')
  controlFrame.title = 'REVamp library controls'
  controlFrame.src = `${url}/?embed=1&control=1`
  controlFrame.setAttribute('sandbox', 'allow-scripts allow-forms allow-downloads')
  controlFrame.setAttribute('referrerpolicy', 'no-referrer')
  controlFrame.setAttribute('allow', 'autoplay; fullscreen')
  controlFrame.style.cssText = 'border:0;display:none;width:100%;height:100%;background:transparent'

  const dock = document.createElement('div')
  dock.setAttribute('aria-hidden', 'true')
  dock.style.cssText = `position:fixed;left:-${DOCK_W + 4000}px;top:0;width:${DOCK_W}px;height:${DOCK_H}px;overflow:hidden;pointer-events:none`
  dock.append(frame, controlFrame)
  document.body.append(dock)

  const overlay = buildOverlay(ctx)
  let pageHost = null
  let paneHost = null
  let box = { x: 0, y: 0, w: 275, h: 348 }

  const send = msg => frame.contentWindow?.postMessage(msg, '*')

  // ---- the bridge to the player page (see rest()). Requests are matched by id; a page
  // reload drops its pending requests, so they time out and reads retry via the gateway.
  const pending = new Map()
  let seq = 0
  const bridgeApi = {
    ready: false,
    request(path, opts = {}) {
      return new Promise((resolve, reject) => {
        const id = `r${++seq}-${Date.now().toString(36)}`
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('player did not answer')) }, opts.timeoutMs || 5000)
        pending.set(id, { resolve, reject, timer })
        send({ type: 'revamp:rest', id, key: bridgeKey, method: opts.method || 'GET', path, body: opts.body })
      })
    }
  }
  bridge = bridgeApi
  const dropPending = () => {
    bridgeApi.ready = false
    for (const [id, p] of pending) { clearTimeout(p.timer); p.reject(new Error('player reloaded')); pending.delete(id) }
  }
  frame.addEventListener('load', dropPending) // the page is (re)loading: not ready until it says so
  const readyWaiters = []
  let sawRevamp = false // the page answered with Revamp's handshake (ready + bridge)

  // The pop-out's iframe is only as big as the classic windows it shows (plus a little room
  // for MilkDrop to open beside them). A 900x1000 out-of-process iframe seen through a small
  // clip was composited whole on every frame: under software graphics (Hermes on many NVIDIA
  // hosts) that alone cost about 1.5 CPU cores, even with the music stopped.
  const POP_SLACK = 300
  function fitOverlay() {
    overlay.clip.style.width = `${box.w}px`
    overlay.clip.style.height = `${box.h}px`
    frame.style.width = `${Math.min(DOCK_W, box.x + box.w + POP_SLACK)}px`
    frame.style.height = `${Math.min(DOCK_H, box.y + box.h + POP_SLACK)}px`
    frame.style.transform = `translate(${-box.x}px, ${-box.y}px)`
  }
  // The docked pane's clip box hugs the classic windows exactly (any skin/size).
  // Whole-number scale (pixel art stays crisp): the classic stack grows to fill a wide or
  // tall pane instead of sitting at 275px in the middle of a big screen.
  function paneScale() {
    const area = paneHost?.parentElement
    if (!area || !box.w || !box.h) return 1
    const fit = Math.min((area.clientWidth - 8) / box.w, (area.clientHeight - 14) / box.h)
    return Math.max(1, Math.min(4, Math.floor(fit)))
  }
  function fitPane() {
    if (!paneHost) return
    const s = paneScale()
    paneHost.style.width = `${box.w * s}px`
    paneHost.style.height = `${box.h * s}px`
    frame.style.transformOrigin = '0 0'
    frame.style.transform = `scale(${s}) translate(${-box.x}px, ${-box.y}px)`
  }
  let paneWatch = null

  let parked = false

  function place() {
    // Keep the library route useful without creating a second player. Its own
    // page uses token-authenticated commands; this plugin continues polling the
    // same state for the ticker, queue, and now-playing controls.
    const controlPage = pageHost && (parked || !$pageBorrow.get())
    if (controlPage) {
      controlFrame.style.display = 'block'
      moveInto(pageHost, controlFrame)
      sendControlPlace()
    } else {
      controlFrame.style.display = 'none'
      moveInto(dock, controlFrame)
    }
    // Parked while the desktop window plays: stay blank in the dock.
    if (parked) {
      moveInto(dock, frame)
      overlay.root.style.display = 'none'
      $where.set('desktop')
      return
    }
    // The page only borrows the single live iframe for mini (or an explicit
    // “Bring the player here”). Docked and floating keep their player in place,
    // while the route displays its audio-free control shell.
    const loc = placementLocation()
    if (pageHost && $pageBorrow.get()) {
      frame.style.transform = ''
      frame.style.width = '100%'
      frame.style.height = '100%'
      moveInto(pageHost, frame)
      overlay.root.style.display = 'none'
      send({ type: 'revamp:mode', mode: 'full', placement: 'page' })
      $where.set('page')
    } else if (loc === 'docked' && paneHost) {
      frame.style.width = `${DOCK_W}px`
      frame.style.height = `${DOCK_H}px`
      frame.style.transform = `translate(${-box.x}px, ${-box.y}px)`
      moveInto(paneHost, frame)
      fitPane()
      overlay.root.style.display = 'none'
      send({ type: 'revamp:mode', mode: 'winamp', placement: 'docked' })
      $where.set('pane')
    } else if (loc === 'floating') {
      frame.style.transformOrigin = ''
      frame.style.width = `${DOCK_W}px`
      frame.style.height = `${DOCK_H}px`
      moveInto(overlay.clip, frame)
      overlay.root.style.display = 'block'
      send({ type: 'revamp:mode', mode: 'winamp', placement: 'floating' })
      fitOverlay()
      $where.set('winamp')
    } else {
      frame.style.transformOrigin = ''
      frame.style.transform = ''
      frame.style.width = `${DOCK_W}px`
      frame.style.height = `${DOCK_H}px`
      moveInto(dock, frame)
      overlay.root.style.display = 'none'
      send({ type: 'revamp:mode', mode: 'full', placement: 'mini' })
      $where.set('dock')
    }
  }

  function sendControlPlace() {
    const place = parked ? 'desktop' : placementLocation()
    controlFrame.contentWindow?.postMessage({ type: 'revamp:control-place', place }, '*')
  }

  ctx.addEventListener(window, 'message', e => {
    if (!e.data || typeof e.data !== 'object') return
    if (e.source === controlFrame.contentWindow) {
      if (e.data.type === 'revamp:bring-player-here') {
        $pageBorrow.set(true)
        if (parked) void bringBackFromDesktop()
        place()
      }
      return
    }
    if (e.source !== frame.contentWindow) return
    if (e.data.type === 'revamp:rest-result' && typeof e.data.id === 'string') {
      const p = pending.get(e.data.id)
      if (!p) return
      pending.delete(e.data.id); clearTimeout(p.timer)
      if (e.data.ok) p.resolve(e.data.body)
      else p.reject(Object.assign(new Error(e.data.error || `HTTP ${e.data.status}`), { status: e.data.status, answered: e.data.status >= 400 }))
      return
    }
    if (e.data.type === 'revamp:ready') {
      // While parked the page runs in control mode (no player): still a bridge.
      bridgeApi.ready = Boolean(bridgeKey) && e.data.bridge === true && (!parked || e.data.control === true)
      // Only Revamp's own page says ready with a bridge (it has the key in its meta tag).
      if (e.data.bridge === true) { sawRevamp = true; for (const f of readyWaiters.splice(0)) f(true) }
      if (e.data.control) return
      place() // re-send the current mode after a (re)load
      send({ type: 'revamp:wave', on: true })
    }
    if (e.data.type === 'revamp:bbox') {
      box = { x: e.data.x | 0, y: e.data.y | 0, w: Math.max(40, e.data.w | 0), h: Math.max(14, e.data.h | 0) }
      if ($where.get() === 'winamp') fitOverlay()
      if ($where.get() === 'pane') fitPane()
    }
    if (e.data.type === 'revamp:drag' && $where.get() === 'winamp') {
      overlay.dragFromPage(e.data.phase, Number(e.data.x), Number(e.data.y))
    }
    if (e.data.type === 'revamp:wave') {
      waveSamples = Array.isArray(e.data.samples) ? e.data.samples.slice(0, 96).map(Number) : null
    }
    // The page relays the server's player_location command (it owns the SSE stream):
    // move the same iframe, never recreate it.
    if (e.data.type === 'revamp:location' && PLAYER_LOCATIONS.includes(e.data.location)) {
      setPlayerLocation(e.data.location)
    }
    // Duck/unduck notices while docked to mini: the iframe's own toasts are invisible
    // there (docked off-screen), so surface them through the host instead.
    if (e.data.type === 'revamp:toast' && typeof e.data.text === 'string' && e.data.text) {
      $barToast.set({ text: e.data.text.slice(0, 140), kind: e.data.kind === 'error' ? 'error' : e.data.kind === 'ok' ? 'ok' : 'info', seq: ++barToastSeq })
      let petted = false
      try {
        if (ctxRef.pet?.visible?.get?.()) { ctxRef.pet.say(`♪ ${e.data.text.slice(0, 120)}`, { id: 'revamp-toast', ttlMs: 5000 }); petted = true }
      } catch { /* pet is optional */ }
      if (!petted) {
        const kind = e.data.kind === 'error' ? 'error' : 'success'
        try { host.notify({ kind, message: `REVamp: ${e.data.text.slice(0, 120)}` }) } catch { /* never break the listener */ }
      }
    }
  })

  // Narrow windows squeeze the chat: docked placement collapses to mini until it widens again
  // (the saved choice stays 'docked', so this is placement only).
  let wasNarrow = null
  ctx.addEventListener(window, 'resize', () => {
    const narrow = innerWidth < DOCKED_MIN_WIDTH
    if (narrow === wasNarrow) return
    wasNarrow = narrow
    place()
  })

  function remove() {
    dropPending()
    if (bridge === bridgeApi) bridge = null
    dock.remove()
    overlay.root.remove()
  }
  ctx.onDispose(remove)

  $where.set('dock')
  return {
    remove,
    place,
    showPage(el) {
      pageHost = el
      // Opening the full route respects an intentional Frame/Pop-out choice.
      // Mini is the compact surface, so its full-screen route still owns player.
      $pageBorrow.set(pageBorrowRequested || placementLocation() === 'mini')
      pageBorrowRequested = false
      place()
    },
    hidePage(el) { if (pageHost === el) { pageHost = null; $pageBorrow.set(true); place() } },
    borrowPage() { $pageBorrow.set(true); place() },
    showPane(el) {
      paneHost = el
      paneWatch?.disconnect()
      if (typeof ResizeObserver !== 'undefined' && el.parentElement) {
        paneWatch = new ResizeObserver(() => { if ($where.get() === 'pane') fitPane() })
        paneWatch.observe(el.parentElement)
      }
      place()
    },
    hidePane(el) { if (paneHost === el) { paneWatch?.disconnect(); paneWatch = null; paneHost = null; place() } },
    // Thin wrapper over the locations: the floating overlay or no player at all.
    // $winampOpen stays in sync as a derived value (see setPlayerLocation listeners).
    setWinamp(open) { setPlayerLocation(open ? 'floating' : 'mini') },
    // Show / hide the MilkDrop visualizer window in the player page.
    visualizer() { send({ type: 'revamp:visualizer' }) },
    // Stop this player (the desktop window took over) / start it again.
    // Parked: the desktop window plays. The page here runs in control mode (no audio) so
    // the mini player can still reach the server when the Hermes gateway is stale.
    park() { if (parked) return; parked = true; dropPending(); frame.src = `${url}/?control=1`; place() },
    unpark() { if (!parked) return; parked = false; frame.src = `${url}/?embed=1`; place() },
    // Reconnect: reload the player page (the server keeps the queue and position).
    reload() { dropPending(); frame.src = parked ? `${url}/?control=1` : `${url}/?embed=1`; place() },
    // Resolves true once the page says it is Revamp's player, false after ms.
    whenReady(ms) {
      return new Promise(resolve => {
        if (sawRevamp) return resolve(true)
        const timer = setTimeout(() => resolve(false), ms)
        readyWaiters.push(ok => { clearTimeout(timer); resolve(ok) })
      })
    }
  }
}

// The floating Winamp window: a thin grip bar (drag, full player, close) over
// a clip box sized to the classic windows. Plain DOM: it lives outside the
// React tree so it survives every navigation. Colors are theme tokens.
function buildOverlay(ctx) {
  const root = document.createElement('div')
  root.setAttribute('role', 'dialog')
  root.setAttribute('aria-label', 'REVamp classic player')
  const pos = ctx.storage.get('winampPos', { right: 24, bottom: 48 })
  root.style.cssText =
    // No soft drop shadow: under software graphics (Hermes on many NVIDIA hosts) a 32px blur
    // around a box whose player redraws all the time was recomputed on every frame.
    'position:fixed;z-index:60;display:none;border-radius:4px;overflow:hidden;' +
    'border:1px solid var(--ui-stroke-secondary)'
  const placeAt = p => {
    root.style.left = p.left != null ? `${p.left}px` : ''
    root.style.top = p.top != null ? `${p.top}px` : ''
    root.style.right = p.left == null ? `${p.right ?? 24}px` : ''
    root.style.bottom = p.top == null ? `${p.bottom ?? 48}px` : ''
  }
  placeAt(pos)

  const grip = document.createElement('div')
  grip.style.cssText =
    'height:16px;display:flex;align-items:center;gap:4px;padding:0 4px;cursor:grab;user-select:none;' +
    'font:10px/16px var(--font-sans, system-ui);color:var(--ui-text-quaternary);background:var(--ui-surface-secondary, transparent)'
  const label = document.createElement('span')
  label.textContent = '♪ REVamp'
  label.style.flex = '1'
  const mkBtn = (text, title, onClick) => {
    const b = document.createElement('button')
    b.type = 'button'
    b.textContent = text
    b.title = title
    b.setAttribute('aria-label', title)
    b.style.cssText = 'all:unset;cursor:pointer;padding:0 4px;color:var(--ui-text-secondary)'
    ctx.addEventListener(b, 'click', e => { e.stopPropagation(); onClick() })
    return b
  }
  grip.append(
    label,
    mkBtn('⤢', EN.openFull, () => host.navigate(ROUTE)),
    mkBtn('⇲', EN.dock, () => setPlayerLocation('docked')),
    mkBtn('✕', EN.popIn, () => setPlayerLocation('mini'))
  )

  // Drag by the grip, or by a classic title bar inside the player (the page streams
  // those drags to us as screen coordinates); remember where it was dropped.
  // While dragging, the box slides with a GPU transform, at most once per frame: moving a
  // box that holds an out-of-process iframe by left/top relaid the page, repainted the
  // shadow and resynced the iframe on every pointer event, and the drag crawled.
  // left/top are written once, when the drag ends.
  const clampPos = (left, top) => ({
    left: Math.max(0, Math.min(innerWidth - 60, left)),
    top: Math.max(0, Math.min(innerHeight - 20, top))
  })
  let slide = null // { left, top } where the drag started; the transform is relative to it
  let want = null
  let frameReq = 0
  const paintSlide = () => {
    frameReq = 0
    if (!slide || !want) return
    root.style.transform = `translate3d(${want.left - slide.left}px, ${want.top - slide.top}px, 0)`
  }
  const beginSlide = () => {
    const r = root.getBoundingClientRect()
    placeAt({ left: r.left, top: r.top })
    slide = { left: r.left, top: r.top }
    want = null
    root.style.willChange = 'transform'
  }
  const moveTo = (left, top) => {
    want = clampPos(left, top)
    if (!slide) { placeAt(want); return }
    if (!frameReq) frameReq = requestAnimationFrame(paintSlide)
  }
  const endSlide = () => {
    if (frameReq) { cancelAnimationFrame(frameReq); frameReq = 0 }
    if (slide && want) placeAt(want)
    root.style.transform = ''
    root.style.willChange = ''
    slide = null
    want = null
  }
  const savePos = () => {
    const r = root.getBoundingClientRect()
    ctx.storage.set('winampPos', { left: Math.round(r.left), top: Math.round(r.top) })
  }
  let drag = null
  ctx.addEventListener(grip, 'pointerdown', e => {
    // A press on the strip's buttons (⤢ ⇲ ✕) is a click, not a drag: capturing the pointer here
    // sent the click to the strip and the buttons never fired.
    if (e.button !== 0 || e.target?.closest?.('button')) return
    const r = root.getBoundingClientRect()
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top }
    beginSlide()
    grip.setPointerCapture(e.pointerId)
    grip.style.cursor = 'grabbing'
  })
  ctx.addEventListener(grip, 'pointermove', e => {
    if (drag) moveTo(e.clientX - drag.dx, e.clientY - drag.dy)
  })
  ctx.addEventListener(grip, 'pointerup', () => {
    if (!drag) return
    drag = null
    grip.style.cursor = 'grab'
    endSlide()
    savePos()
  })
  let pageDrag = null
  const dragFromPage = (phase, x, y) => {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return
    if (phase === 'start') {
      const r = root.getBoundingClientRect()
      pageDrag = { left: r.left, top: r.top, x, y }
      beginSlide()
    } else if (pageDrag && phase === 'move') {
      moveTo(pageDrag.left + x - pageDrag.x, pageDrag.top + y - pageDrag.y)
    } else if (pageDrag && phase === 'end') {
      moveTo(pageDrag.left + x - pageDrag.x, pageDrag.top + y - pageDrag.y)
      pageDrag = null
      endSlide()
      savePos()
    }
  }

  const clip = document.createElement('div')
  clip.style.cssText = 'position:relative;overflow:hidden;width:275px;height:348px'
  root.append(grip, clip)
  document.body.append(root)
  return { root, clip, dragFromPage }
}

// Only the window holding the 'revamp:engine' lock runs the player; the
// others wait in the lock queue and take over when that window closes.
function claimEngine(ctx) {
  const ac = new AbortController()
  let release = null
  ctx.onDispose(() => { ac.abort(); release?.() })
  $where.set('elsewhere')
  if (!navigator.locks) return void runEngine(ctx, ac.signal)
  navigator.locks
    .request('revamp:engine', { signal: ac.signal }, () => new Promise(resolve => {
      release = resolve
      void runEngine(ctx, ac.signal)
    }))
    .catch(() => {})
}

// Can the browser reach the player page at all? (An opaque response is enough to know.)
async function reachable(url) {
  try { await fetch(`${url}/`, { mode: 'no-cors', cache: 'no-store', signal: AbortSignal.timeout(3000) }); return true } catch { return false }
}

let wakeEngineRetry = null
function waitForEngineRetry(ms, signal) {
  return new Promise(resolve => {
    const done = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      if (wakeEngineRetry === done) wakeEngineRetry = null
      resolve()
    }
    const timer = setTimeout(done, ms)
    wakeEngineRetry = done
    signal.addEventListener('abort', done, { once: true })
  })
}

async function runEngine(ctx, signal) {
  let misses = 0
  while (!engine && !signal.aborted && ctxRef === ctx) {
    let url = null
    try {
      const info = await rest('/info', { timeoutMs: 4000 })
      if (info?.ok) {
        url = info.url
        ctx.storage.set('playerUrl', url)
        if (typeof info.bridge_key === 'string') ctx.storage.set('bridgeKey', info.bridge_key)
      }
    } catch {
      // Server not up yet, or the Hermes gateway can't answer right now.
    }
    // The gateway may be stale while Revamp's own server is fine: start from the last url.
    const known = ctx.storage.get('playerUrl', null)
    let remembered = false
    if (!url && ++misses >= 2 && typeof known === 'string' && /^http:\/\/127\.0\.0\.1:\d+$/.test(known) && await reachable(known)) {
      url = known
      remembered = true
    }
    try {
      if (url && !engine && !signal.aborted && ctxRef === ctx) {
        const created = createEngine(ctx, url, String(ctx.storage.get('bridgeKey', '') || ''))
        // A remembered port may now belong to another program: it must answer as Revamp.
        if (remembered && !(await created.whenReady(20_000))) {
          created.remove()
          ctx.storage.set('playerUrl', null) // not Revamp any more: wait for a fresh /info
          throw new Error('not revamp')
        }
        if (engine || signal.aborted || ctxRef !== ctx) { created.remove(); return }
        engine = created
        $engineVersion.set($engineVersion.get() + 1)
        // The legacy flag is derived now; place() follows $playerLocation alone.
        $winampOpen.set($playerLocation.get() === 'floating')
        engine.place()
        if ($desktop.get()) engine.park()
        return
      }
    } catch {
      // Could not start the player; try again shortly.
    }
    await waitForEngineRetry(misses >= 2 ? 10_000 : 3_000, signal)
  }
}

// ---------------------------------------------------------------- derived state
const RATES = [0.75, 1, 1.25, 1.5, 1.75, 2, 2.5]
const nextRate = r => RATES[(RATES.findIndex(x => Math.abs(x - r) < 0.01) + 1) % RATES.length] || 1

function nowPlaying(state) {
  const live = state?.live || {}
  const np = live.now_playing || {}
  const status = String(np.status || '').toUpperCase()
  return {
    online: Boolean(state),
    connected: Boolean(live.player_connected),
    playing: status === 'PLAYING',
    status: status === 'PLAYING' ? 'live' : status === 'PAUSED' ? 'paused' : 'stopped',
    title: np.title || '',
    artist: np.artist || '',
    url: np.url || '',
    // `kind` is legacy track|stream for old status bars; media_kind is the
    // canonical playback value and preserves Focus for controls/tab state.
    kind: np.media_kind || np.kind || 'track',
    volume: Number.isFinite(state?.volume) ? state.volume : 75,
    index: Number.isInteger(np.index) ? np.index : -1,
    trackId: np.kind === 'stream' ? null : np.track_id || null,
    shuffle: typeof np.shuffle === 'boolean' ? np.shuffle : Boolean(state?.shuffle),
    repeat: typeof np.repeat === 'boolean' ? np.repeat : Boolean(state?.repeat),
    sleep: np.sleep && typeof np.sleep === 'object' ? np.sleep : null,
    // A live stream has no timeline: no ±10 s there.
    seekable: Boolean(np.title) && (np.media_kind || np.kind) !== 'stream' && (np.media_kind || np.kind) !== 'focus',
    rate: Number(np.rate) || 1,
    book: (np.media_kind || np.kind) === 'audiobook'
  }
}

function label(np) {
  if (!np.title) return ''
  return np.artist ? `${np.title} — ${np.artist}` : np.title
}

// One pet line per new track, at most every 30 s.
function maybeAnnounce(np) {
  const text = label(np)
  if (!ctxRef || !np.playing || !text || text === lastPetTitle) return
  const now = Date.now()
  if (now - lastPetAt < 30_000) return
  lastPetTitle = text
  lastPetAt = now
  try {
    if (ctxRef.pet?.visible?.get?.()) ctxRef.pet.say(`♪ ${text}`, { id: 'revamp-np', ttlMs: 5000 })
  } catch {
    // The pet is optional; never let it break the bar.
  }
}

// ---------------------------------------------------------------- waveform
// Braille oscilloscope with two dim previous traces, fed by the player page.
// Same visual language as the bundled Radio meter so the status bar reads the
// same; silence stays flat and reduced motion freezes it.
function brailleTrace(samples, rows) {
  const width = samples.length / 2
  const cells = Array(width * rows).fill(0)
  const dots = [[1, 2, 4, 64], [8, 16, 32, 128]]
  const center = (rows * 4 - 1) / 2
  const row = value => Math.max(0, Math.min(rows * 4 - 1, Math.round(center - value * center)))
  let previous = row(samples[0])
  samples.forEach((value, x) => {
    const y = row(value)
    for (let point = Math.min(previous, y); point <= Math.max(previous, y); point++) {
      cells[Math.floor(point / 4) * width + Math.floor(x / 2)] |= dots[x % 2][point % 4]
    }
    previous = y
  })
  return cells.map(cell => String.fromCharCode(0x2800 | cell))
}

function resample(samples, n) {
  if (!samples?.length) return Array(n).fill(0)
  return Array.from({ length: n }, (_, i) => samples[Math.floor((i * samples.length) / n)] || 0)
}

function Waveform({ compact = false }) {
  const width = compact ? 6 : 36
  const rows = compact ? 1 : 2
  const ref = useRef(null)
  const active = nowPlaying(useValue($live)).playing
  const baseline = brailleTrace(Array(width * 2).fill(0), rows)
  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    const blank = Array(width * rows).fill('⠀')
    const flat = brailleTrace(Array(width * 2).fill(0), rows)
    const layers = [...node.children]
    const paint = (layer, cells) => cells.forEach((cell, i) => {
      if (layers[layer].children[i].textContent !== cell) layers[layer].children[i].textContent = cell
    })
    let history = [blank, blank]
    const reset = () => { history = [blank, blank]; paint(0, blank); paint(1, blank); paint(2, flat) }
    reset()
    const motion = matchMedia('(prefers-reduced-motion: reduce)')
    const loop = createBudgetedLoop(() => {
      if (!active || motion.matches) return
      const cells = brailleTrace(resample(waveSamples, width * 2), rows)
      paint(0, history[0]); paint(1, history[1]); paint(2, cells)
      history = [history[1], cells]
    }, { fps: 12, pauseWhenUnfocused: false, idleWhen: () => !active || motion.matches })
    const updateMotion = () => { reset(); loop.wake() }
    motion.addEventListener('change', updateMotion)
    return () => { loop.dispose(); motion.removeEventListener('change', updateMotion) }
  }, [active, rows, width])
  return jsx('div', {
    ref, className: 'revamp-wave', style: { '--revamp-cols': width }, 'data-compact': compact, 'data-active': active, 'aria-hidden': true,
    children: [0, 1, 2].map(layer => jsx('span', {
      className: 'revamp-wave-layer',
      children: baseline.map((cell, i) => jsx('span', { children: layer === 2 ? cell : '⠀' }, i))
    }, layer))
  })
}

// ---------------------------------------------------------------- small UI
function Skip({ dir }) {
  return jsxs('span', { className: 'revamp-skip', 'data-dir': dir, 'aria-hidden': true, children: [
    jsx(icons.Play, { style: { width: 7, height: 12, flexShrink: 0 }, fill: 'currentColor' }),
    jsx(icons.Play, { style: { width: 7, height: 12, flexShrink: 0, marginLeft: -2 }, fill: 'currentColor' })
  ] })
}
const PrevIcon = () => jsx(Skip, { dir: 'prev' })
const NextIcon = () => jsx(Skip, { dir: 'next' })
// A window with a right-hand panel: the docked player.
const DockIcon = svgIcon(['M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z', 'M15 3v18'])

// Small line icons drawn here (lucide shapes), so nothing depends on which names the
// host's icon set happens to export.
function svgIcon(paths, filled = false) {
  return () => jsx('svg', {
    viewBox: '0 0 24 24', width: 12, height: 12, fill: filled ? 'currentColor' : 'none', stroke: 'currentColor',
    strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
    children: paths.map((d, i) => jsx('path', { d }, i))
  })
}
const HEART = 'M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z'
const HeartOff = svgIcon([HEART])
const HeartOn = svgIcon([HEART], true)
const ShuffleIcon = svgIcon(['M2 18h1.4c1.3 0 2.5-.6 3.3-1.7l6.1-8.6c.7-1.1 2-1.7 3.3-1.7H22', 'm18 2 4 4-4 4', 'M2 6h1.9c1.5 0 2.9.9 3.6 2.2', 'M22 18h-5.9c-1.3 0-2.6-.7-3.3-1.8l-.5-.8', 'm18 14 4 4-4 4'])
const RepeatIcon = svgIcon(['m17 2 4 4-4 4', 'M3 11v-1a4 4 0 0 1 4-4h14', 'm7 22-4-4 4-4', 'M21 13v1a4 4 0 0 1-4 4H3'])
const Back10Icon = svgIcon(['M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 'M3 3v5h5'])
const Fwd10Icon = svgIcon(['M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8', 'M21 3v5h-5'])
const QueueIcon = svgIcon(['M21 15V6', 'M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z', 'M12 12H3', 'M16 6H3', 'M12 18H3'])
const PencilIcon = svgIcon(['M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z'])
const TrashIcon = svgIcon(['M3 6h18', 'M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6', 'M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2'])
const UpIcon = svgIcon(['m5 12 7-7 7 7', 'M12 19V5'])
const DownIcon = svgIcon(['M12 5v14', 'm19 12-7 7-7-7'])

function SmallAction({ label, icon, onClick, size = 'micro', pressed, className, busy = false }) {
  return jsx(Tip, { label, children: jsx(Button, {
    variant: 'ghost', size, className: `revamp-action ${className ?? ''}`, title: label, 'aria-label': label, 'aria-pressed': pressed, onClick,
    // A missing icon (a name this Hermes doesn't export) shows the label instead of crashing the host.
    children: jsx('span', { className: 'revamp-action-icon', children: busy ? jsx(GlyphSpinner, { ariaLabel: label }) : icon ? jsx(icon, {}) : label })
  }) })
}

function toggle() {
  command('toggle')
  // Optimistic: flip the cached status now; the next poll confirms it.
  const s = $live.get()
  const np = s?.live?.now_playing
  if (np) {
    heldStatus = { status: String(np.status).toUpperCase() === 'PLAYING' ? 'PAUSED' : 'PLAYING', until: Date.now() + 4000 }
    $live.set(withHeld(s))
  }
  setTimeout(refreshLive, 400)
}

function setVolume(percent) {
  const v = Math.max(0, Math.min(100, Math.round(percent)))
  const s = $live.get()
  if (s) $live.set({ ...s, volume: v })
  command('volume', { percent: v })
}

// ---------------------------------------------------------------- picker
function Row({ name, sub, current, playing, onPlay, pin, extra, item }) {
  const t = usePluginI18n(ID)
  const [menu, setMenu] = useState(null)
  const menuRef = useRef(null)
  const rowRef = useRef(null)
  const activate = () => (current ? toggle() : onPlay())

  // The menu is a Hermes Popover (it renders above everything): a plain fixed-position box
  // inside the mini player's panel was clipped by the panel and never showed. Escape, a click
  // outside and focus handling come from the Popover; it is anchored where the user clicked.
  useEffect(() => {
    if (!menu) return undefined
    const timer = setTimeout(() => menuRef.current?.querySelector('button')?.focus(), 0)
    return () => clearTimeout(timer)
  }, [menu])

  function openMenu(x, y) {
    const box = rowRef.current?.getBoundingClientRect()
    if (!box) return
    setMenu({ x: Math.max(0, x - box.left), y: Math.max(0, y - box.top) })
  }

  function choose(action) {
    setMenu(null)
    if (action === 'now') {
      activate()
      return
    }
    if (!item) return
    if (action === 'next') {
      command('load', { items: [item], mode: 'next' })
      host.notify({ kind: 'success', message: t('addedNext') })
      return
    }
    command('load', { items: [item], mode: 'append' })
    host.notify({ kind: 'success', message: t('addedToQueue') })
  }

  const menuItem = (action, label) => jsx('button', {
    type: 'button', role: 'menuitem', className: 'revamp-menu-item',
    onClick: () => choose(action), children: label
  }, action)

  return jsxs('div', {
    ref: rowRef, className: 'revamp-row', 'data-current': current, 'data-pinned': Boolean(pin?.pinned),
    // Hermes catches every right-click on the window (capture phase) and stops it, unless the
    // target sits inside an element with this marker: then the row's own menu gets the click.
    'data-hermes-context-menu-trigger': item ? '' : undefined,
    onContextMenu: item ? e => {
      e.preventDefault()
      openMenu(e.clientX, e.clientY)
    } : undefined,
    onKeyDown: item ? e => {
      if ((e.shiftKey && e.key === 'F10') || e.key === 'ContextMenu') {
        e.preventDefault()
        const box = rowRef.current?.getBoundingClientRect()
        if (box) openMenu(box.left + 24, box.bottom)
      }
    } : undefined,
    children: [
      jsxs(RowButton, {
        className: 'revamp-row-main', 'aria-label': `${t(current && playing ? 'pause' : 'play')}: ${name}`,
        'aria-current': current ? 'true' : undefined,
        onClick: activate,
        children: [
          jsx('span', { className: 'revamp-row-icon', children: jsx(current && playing ? icons.Pause : icons.Play, { size: 12 }) }),
          jsxs('span', { className: 'revamp-row-copy', children: [
            jsx('span', { className: 'revamp-row-name', children: name }),
            sub ? jsx('span', { className: 'revamp-row-sub', children: sub }) : null
          ] })
        ]
      }),
      pin ? jsx(SmallAction, {
        label: `${t(pin.pinned ? 'unpin' : 'pin')}: ${name}`, icon: icons.Pin, size: 'icon-xs',
        pressed: pin.pinned, className: 'revamp-pin', onClick: pin.toggle
      }) : null,
      extra || null,
      menu ? jsxs(Popover, {
        open: true,
        onOpenChange: open => { if (!open) setMenu(null) },
        children: [
          jsx(PopoverTrigger, { asChild: true, children: jsx('span', {
            'aria-hidden': true,
            style: { position: 'absolute', left: menu.x, top: menu.y, width: 1, height: 1, pointerEvents: 'none' }
          }) }),
          jsx(PopoverContent, {
            side: 'bottom', align: 'start', sideOffset: 2, className: 'revamp-menu-pop',
            children: jsxs('div', {
              ref: menuRef, className: 'revamp-menu', role: 'menu', 'aria-label': t('rowMenu'),
              onContextMenu: e => e.preventDefault(),
              children: [
                menuItem('now', t('playNow')),
                menuItem('next', t('playNext')),
                menuItem('queue', t('addToQueue'))
              ]
            })
          })
        ]
      }, 'menu') : null
    ]
  })
}

function useDebounced(text, ms = 350) {
  const [value, setValue] = useState(text)
  useEffect(() => {
    const timer = setTimeout(() => setValue(text), ms)
    return () => clearTimeout(timer)
  }, [text, ms])
  return value
}

// One page at a time, appended to what is already listed. A new key (a new
// search, folder kind or library version) starts again at page one; a page is
// never requested twice for the same key.
function usePagedList({ queryKey, pageSize, fetchPage, enabled = true, maxOffset = Infinity }) {
  const key = JSON.stringify(queryKey)
  const fresh = () => ({ key, items: [], total: null, next: 0, loading: false, error: null, wanted: true })
  const [state, setState] = useState(fresh)
  const seen = useRef(new Set())
  const run = useRef(0)

  useEffect(() => {
    if (state.key === key) return
    seen.current = new Set()
    run.current += 1
    setState(fresh())
  }, [key, state.key])

  useEffect(() => {
    if (state.key !== key || !enabled || !state.wanted || state.loading) return
    const offset = state.next
    if (offset == null) return
    if (state.total != null && offset >= state.total) return
    const tag = `${key}:${offset}`
    if (seen.current.has(tag)) return
    seen.current.add(tag)
    const token = ++run.current
    setState(prev => (prev.key === key ? { ...prev, wanted: false, loading: true, error: null } : prev))
    fetchPage(offset, pageSize)
      .then(out => {
        if (token !== run.current) return
        const list = Array.isArray(out) ? out : (out?.tracks || out?.stations || out?.results || [])
        const unlistedTotal = Array.isArray(out) ? null : Number(out?.total)
        const total = Number.isFinite(unlistedTotal) ? unlistedTotal : null
        // Without a total (radio): the server may drop a few entries from a full page (e.g. HLS
        // streams), so step by the page size and stop only on an empty page or the offset cap.
        const next = total != null ? offset + list.length : offset + pageSize
        const more = list.length > 0 && next <= maxOffset && (total != null ? next < total : true)
        setState(prev => (prev.key === key
          ? { ...prev, items: [...prev.items, ...list], total, next: more ? next : null, loading: false }
          : prev))
      })
      .catch(error => {
        if (token !== run.current) return
        setState(prev => (prev.key === key ? { ...prev, loading: false, error } : prev))
      })
  }, [key, state.key, state.wanted, state.loading, state.next, state.total, enabled])

  function loadMore() {
    setState(prev => (prev.key === key && !prev.loading && prev.next != null ? { ...prev, wanted: true } : prev))
  }

  return {
    items: state.key === key ? state.items : [],
    total: state.key === key ? state.total : null,
    loading: state.key === key && state.loading,
    error: state.key === key ? state.error : null,
    hasMore: state.key === key && state.next != null,
    loadMore
  }
}

// The end-of-list marker: the observer asks for the next page when it scrolls
// into view, and the loading row stands in for it while that page arrives.
function LoadMore({ hasMore, loading, onLoad, label }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!hasMore || loading) return undefined
    const el = ref.current
    if (!el || typeof IntersectionObserver !== 'function') return undefined
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) onLoad()
    }, { rootMargin: '0px 0px 120px 0px' })
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasMore, loading, onLoad])
  if (loading) return jsx('div', { className: 'revamp-note', role: 'status', children: label }, 'more')
  return hasMore ? jsx('div', { ref, className: 'revamp-load-more', 'aria-hidden': true }, 'sentinel') : null
}

function sameUrl(a, b) {
  return Boolean(a && b) && a === b
}

// Genre chips search Radio Browser by tag ("#jazz" in the search field).
const GENRES = ['jazz', 'lofi', 'ambient', 'chillout', 'rock', 'indie', 'classical', 'electronic',
  'hip hop', 'metal', '80s', 'country', 'reggae', 'soul', 'news', 'talk']

function RadioList({ text, np, onPending, onText }) {
  const t = usePluginI18n(ID)
  const query = useDebounced(text)
  const tag = text.startsWith('#') ? text.slice(1).trim().toLowerCase() : ''
  // The most-played stations first (Radio Browser); the built-in presets if that fails.
  const presets = useQuery({
    queryKey: [ID, 'popular'],
    queryFn: () => rest('/radio/popular', { timeoutMs: 12000 }).catch(() => rest('/radio/presets')),
    staleTime: 3_600_000
  })
  const favorites = useQuery({ queryKey: [ID, 'favorites'], queryFn: () => rest('/radio/favorites'), staleTime: 30_000 })
  const searchActive = (tag ? tag.length >= 2 : query.length >= 2) && query === text
  const search = usePagedList({
    queryKey: [ID, 'radio-search', tag, query],
    pageSize: tag ? 60 : 40,
    maxOffset: 1000, // the server's cap on Radio Browser offsets
    enabled: searchActive,
    fetchPage: (offset, limit) => {
      const params = tag
        ? { tag, limit: String(limit), offset: String(offset) }
        : { q: query, limit: String(limit), offset: String(offset) }
      return rest(`/radio/search?${new URLSearchParams(params)}`, { timeoutMs: 12000 })
    }
  })
  const searchItems = searchActive ? search.items : []
  const pinned = Array.isArray(favorites.data) ? favorites.data : []
  const isPinned = s => pinned.some(p => p.id === s.id || p.url === s.url)
  // Same fold the server applies (NFKD, strip marks): 'cafe' must find 'Café' even
  // when only the local favourites/presets are reachable (offline path).
  const fold = v => String(v ?? '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  const needle = fold(text)
  const matches = tag
    ? s => (s.tags || []).some(v => fold(v).includes(fold(tag)))
    : s => !needle || [s.name, ...(s.tags || [])].some(v => fold(v).includes(needle))
  const seen = new Set()
  const rows = [...pinned, ...(Array.isArray(presets.data) ? presets.data : []), ...searchItems]
    .filter(s => s?.url && (matches(s) || searchItems.includes(s)) && !seen.has(s.url) && seen.add(s.url))
  async function pinToggle(s) {
    try {
      if (isPinned(s)) await rest(`/radio/favorites?${new URLSearchParams({ id: s.id })}`, { method: 'DELETE' })
      else await rest('/radio/favorites', { method: 'POST', body: s })
      favorites.refetch()
    } catch (err) {
      host.notify({ kind: 'error', message: `REVamp: ${err?.message || t('failed')}` })
    }
  }
  const pending = text.length >= 2 && (text !== query || (search.loading && !searchItems.length))
  useEffect(() => onPending(pending), [pending])
  const chips = !text || tag ? jsx('div', { className: 'revamp-chips', role: 'group', 'aria-label': t('genre'), children: GENRES.map(g => jsx('button', {
    type: 'button', className: 'revamp-chip', 'aria-pressed': tag === g,
    onClick: () => onText(tag === g ? '' : `#${g}`), children: g
  }, g)) }, 'chips') : null
  return jsx(Fragment, { children: [
    chips,
    ...rows.map(s => jsx(Row, {
      name: s.name, sub: (s.tags || []).slice(0, 3).join(' · ') || s.source,
      current: np.kind === 'stream' && sameUrl(np.url, s.url), playing: np.playing,
      // The whole list is queued, so next/previous move between stations.
      onPlay: () => playItems(rows.map(stationItem), rows.indexOf(s)),
      item: stationItem(s),
      pin: { pinned: isPinned(s), toggle: () => void pinToggle(s) }
    }, s.url)),
    searchActive ? jsx(LoadMore, {
      hasMore: search.hasMore, loading: search.loading && Boolean(searchItems.length),
      onLoad: search.loadMore, label: t('loadingMore')
    }, 'more') : null,
    text.length >= 2 && search.error && jsx('div', { className: 'revamp-note', role: 'status', children: t('searchError') }, 'err'),
    !pending && text && !rows.length && jsx('div', { className: 'revamp-note', role: 'status', children: t('noResults') }, 'none')
  ] })
}

function LibraryList({ text, np, onPending, kind = 'music' }) {
  const t = usePluginI18n(ID)
  const query = useDebounced(text, 250)
  const version = useValue($libVersion)
  const scanning = useValue($scanning)
  const progress = useValue($scanProgress)
  const result = usePagedList({
    queryKey: [ID, 'tracks', kind, query, version],
    pageSize: 100,
    fetchPage: (offset, limit) => rest(`/library/tracks?${new URLSearchParams({
      q: query, limit: String(limit), offset: String(offset), kind
    })}`)
  })
  const tracks = result.items
  const pending = text !== query || (result.loading && !tracks.length)
  useEffect(() => onPending(pending), [pending])
  return jsx(Fragment, { children: [
    scanning && jsx('div', { className: 'revamp-note', role: 'status', children: progress?.total
      ? t('scanningOf').replace('{done}', progress.done.toLocaleString()).replace('{total}', progress.total.toLocaleString())
      : t('scanning') }, 'scan'),
    ...tracks.map((tr, i) => jsx(Row, {
      name: tr.title || tr.path?.split('/').pop() || '?',
      sub: [tr.artist, tr.album, tr.resume > 0 && t('leftOff').replace('{time}', clock(tr.resume))].filter(Boolean).join(' · '),
      current: np.kind !== 'stream' && np.kind !== 'focus' && sameUrl(np.url, tr.url), playing: np.playing,
      // Play from here, with the rest of the results queued behind it.
      onPlay: () => playItems(tracks.map(trackItem), i),
      item: trackItem(tr)
    }, tr.id)),
    jsx(LoadMore, {
      hasMore: result.hasMore, loading: result.loading && Boolean(tracks.length),
      onLoad: result.loadMore, label: t('loadingMore')
    }, 'more'),
    result.error && jsx('div', { className: 'revamp-note', role: 'alert', children: t('failed') }, 'err'),
    !pending && !scanning && !tracks.length && jsx('div', { className: 'revamp-note', role: 'status', children: t(query ? 'noResults' : kind === 'audiobook' ? 'emptyBooks' : 'emptyLibrary') }, 'none')
  ] })
}

const BooksList = props => jsx(LibraryList, { ...props, kind: 'audiobook' })

// After the folders change: poll the scan, refreshing the track lists every few
// seconds (a big network folder takes minutes and fills in as it goes).
let scanWatch = 0
async function watchScan() {
  const mine = ++scanWatch
  $scanning.set(true)
  const until = Date.now() + 3_600_000
  let lastRefresh = Date.now()
  let failures = 0
  try {
    while (mine === scanWatch && Date.now() < until) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      const status = await rest('/library/status').catch(() => null)
      if (mine !== scanWatch) return
      // Server down or too old to report: stop saying "Scanning" after a few tries.
      if (!status || typeof status.scanning !== 'boolean') {
        if (++failures >= 5) break
        continue
      }
      failures = 0
      if (status.progress?.total) $scanProgress.set(status.progress)
      if (!status.scanning) break
      if (Date.now() - lastRefresh > 4000) { lastRefresh = Date.now(); $libVersion.set($libVersion.get() + 1) }
    }
  } finally {
    if (mine === scanWatch) { $scanning.set(false); $scanProgress.set(null); $libVersion.set($libVersion.get() + 1) }
  }
}

const baseName = path => path.replace(/\/+$/, '').split('/').pop() || path

// Music folders: list, remove, and add one by browsing (the renderer has no
// native folder dialog for plugins, so the Revamp server lists directories).
// Each folder is Music or Books, never both. The panel lists the open tab's
// folders; removing one here never touches the other tab.
const folderKind = f => (f.kind === 'books' ? 'books' : 'music')

function FolderPanel({ kind = 'music', onClose }) {
  const t = usePluginI18n(ID)
  const [browse, setBrowse] = useState(null) // null: the folder list; '' = home; else a path
  const [busy, setBusy] = useState(false)
  const [typed, setTyped] = useState('')
  const folders = useQuery({ queryKey: [ID, 'folders'], queryFn: () => rest('/library/folders'), staleTime: 0 })
  const dirs = useQuery({
    queryKey: [ID, 'dirs', browse],
    queryFn: () => rest(`/fs/dirs${browse ? `?${new URLSearchParams({ path: browse })}` : ''}`),
    enabled: browse !== null, staleTime: 10_000, retry: false
  })
  // The box follows the folder being browsed, so it can be edited from there.
  useEffect(() => { if (dirs.data?.path) setTyped(dirs.data.path) }, [dirs.data?.path])
  const all = (folders.data?.folders || []).map(f => ({ ...f, kind: folderKind(f) }))
  const list = all.filter(f => f.kind === kind)
  const other = kind === 'books' ? 'music' : 'books'
  async function save(next) {
    setBusy(true)
    try {
      await rest('/library/folders', { method: 'PUT', body: { folders: next.map(f => ({ path: f.path, kind: f.kind })) }, timeoutMs: 10000 })
      await folders.refetch()
      $libVersion.set($libVersion.get() + 1) // the tabs (Books shows with a Books folder)
      void watchScan()
      setBrowse(null)
    } catch (err) {
      host.notify({ kind: 'error', message: `REVamp: ${err?.message || 'could not save folders'}` })
    } finally {
      setBusy(false)
    }
  }
  const custom = Boolean(folders.data?.custom)
  // Removing a folder from the config default turns the rest into a custom list.
  const remove = path => save(all.filter(f => f.path !== path))
  const taken = path => all.find(f => f.path === path) // already Music or Books
  const add = (path, as) => save([...all, { path, kind: as }])
  const head = jsxs('div', { className: 'revamp-folders-head', children: [
    browse !== null && jsx(SmallAction, { label: t('folders'), icon: icons.ChevronLeft, size: 'icon-xs', onClick: () => setBrowse(null) }),
    jsx('span', { children: t(browse === null ? kind === 'books' ? 'bookFolders' : 'folders' : 'addFolder') }),
    jsx('span', { className: 'revamp-space' }),
    jsx(Button, { variant: 'ghost', size: 'sm', onClick: onClose, children: t('done') })
  ] })
  const backendError = err => /unknown route|HTTP 404|not found/i.test(String(err?.message || err)) ? t('backendOld') : String(err?.message || t('failed'))
  if (folders.isError) {
    return jsxs('div', { children: [head, jsx('div', { className: 'revamp-note', role: 'alert', children: backendError(folders.error) })] })
  }
  if (browse === null) {
    return jsxs('div', { children: [
      head,
      jsx('div', { className: 'revamp-list', children: jsxs(Fragment, { children: [
        jsx('div', { className: 'revamp-note', children: `${t(kind === 'books' ? 'bookFoldersHint' : 'foldersHint')}${custom ? '' : ` ${t('foldersDefault')}`}${list.length ? '' : ` ${t('foldersNone')}`}` }),
        ...list.map(f => jsxs('div', { className: 'revamp-row', 'data-missing': !f.exists, children: [
          jsxs('span', { className: 'revamp-row-main', children: [
            jsx('span', { className: 'revamp-row-icon', style: { opacity: 1 }, children: jsx(icons.FolderOpen, { size: 12 }) }),
            jsxs('span', { className: 'revamp-row-copy', children: [
              jsx('span', { className: 'revamp-row-name', children: baseName(f.path) }),
              jsx('span', { className: 'revamp-row-sub', children: [f.path, f.exists ? `${f.tracks} ${t('tracks')}` : t('missing')].filter(Boolean).join(' · ') })
            ] })
          ] }),
          jsx('span', { className: 'revamp-row-actions', children: jsx(SmallAction, {
            label: `${t('removeFolder')}: ${f.path}`, icon: icons.X, size: 'icon-xs', busy,
            onClick: () => void remove(f.path)
          }) })
        ] }, f.path)),
        jsxs(RowButton, { className: 'revamp-row-main', onClick: () => setBrowse(''), children: [
          jsx('span', { className: 'revamp-row-icon', style: { opacity: 1 }, children: jsx(icons.Plus, { size: 12 }) }),
          jsx('span', { className: 'revamp-row-name', children: t('addFolder') })
        ] }, 'add')
      ] }) })
    ] })
  }
  const here = dirs.data
  const jumps = [{ label: t('home'), path: '' }, { label: '/', path: '/' }, { label: '/mnt', path: '/mnt' }]
  return jsxs('div', { children: [
    head,
    jsxs('form', {
      className: 'revamp-goto',
      onSubmit: e => { e.preventDefault(); const v = typed.trim(); if (v) setBrowse(v) },
      children: [
        jsx('input', { value: typed, onChange: e => setTyped(e.target.value), placeholder: t('pathPlaceholder'), 'aria-label': t('pathPlaceholder'), spellCheck: false }),
        jsx(Button, { type: 'submit', size: 'sm', variant: 'ghost', children: t('goTo') })
      ]
    }),
    jsx('div', { className: 'revamp-jumps', children: jumps.map(j => jsx('button', {
      type: 'button', className: 'revamp-chip', 'aria-pressed': (j.path || null) === (browse || null), onClick: () => setBrowse(j.path), children: j.label
    }, j.label)) }),
    jsx('div', { className: 'revamp-path', title: here?.path, children: jsx('bdi', { children: here?.path || '…' }) }),
    jsx('div', { className: 'revamp-list', children: jsxs(Fragment, { children: [
      here?.parent && jsxs(RowButton, { className: 'revamp-row-main', 'aria-label': t('upLevel'), onClick: () => setBrowse(here.parent), children: [
        jsx('span', { className: 'revamp-row-icon', style: { opacity: 1 }, children: jsx(icons.ChevronUp, { size: 12 }) }),
        jsx('span', { className: 'revamp-row-name', children: '..' })
      ] }, 'up'),
      ...(here?.dirs || []).map(d => jsxs(RowButton, { className: 'revamp-row-main', onClick: () => setBrowse(d.path), children: [
        jsx('span', { className: 'revamp-row-icon', style: { opacity: 1 }, children: jsx(icons.FolderOpen, { size: 12 }) }),
        jsx('span', { className: 'revamp-row-name', children: d.name })
      ] }, d.path)),
      dirs.isError && jsx('div', { className: 'revamp-note', role: 'alert', children: backendError(dirs.error) }, 'err')
    ] }) }),
    jsxs('div', { className: 'revamp-transport', children: [
      here?.path && taken(here.path) && jsx('span', { className: 'revamp-note', children: t(taken(here.path).kind === 'books' ? 'inBooks' : 'inMusic') }, 'taken'),
      jsx('span', { className: 'revamp-space' }),
      // The open tab's kind first; the other one is how the first book folder gets added.
      ...[kind, other].map((as, i) => jsx(Button, {
        size: 'sm', variant: i ? 'ghost' : undefined, disabled: busy || !here?.path || Boolean(taken(here.path)),
        onClick: () => void add(here.path, as), children: t(as === 'books' ? 'addBooks' : 'addMusic')
      }, as))
    ] })
  ] })
}

function PlaylistList({ text, onPending }) {
  const t = usePluginI18n(ID)
  const result = useQuery({ queryKey: [ID, 'playlists'], queryFn: () => rest('/playlists'), staleTime: 30_000 })
  const lower = text.toLowerCase()
  const lists = (Array.isArray(result.data) ? result.data : []).filter(p => !lower || p.name.toLowerCase().includes(lower))
  async function play(p) {
    try {
      const full = await rest(`/playlists/${encodeURIComponent(p.id)}`)
      await playItems(full?.items || [])
    } catch (err) {
      host.notify({ kind: 'error', message: `REVamp: ${err?.message || t('failed')}` })
    }
  }
  const pending = result.isFetching && !result.data
  useEffect(() => onPending(pending), [pending])
  // "Liked" first: every liked track, oldest like first (follows the likes, not a saved file).
  const liked = useValue($liked)
  const likes = useQuery({
    queryKey: [ID, 'likes', liked ? [...liked].join(',') : ''], queryFn: () => rest('/likes'), staleTime: 0, retry: false
  })
  const likedTracks = likes.data?.tracks || []
  const showLiked = !lower || t('liked').toLowerCase().includes(lower)
  return jsx(Fragment, { children: [
    showLiked && jsx(Row, {
      name: `♥ ${t('liked')}`, sub: likedTracks.length ? `${likedTracks.length} ${t('tracks')}` : t('likedEmpty'),
      current: false, playing: false, onPlay: () => void playItems(likedTracks.map(trackItem))
    }, 'liked'),
    ...lists.map(p => jsx(PlaylistRow, { p, onPlay: () => void play(p), onChanged: () => result.refetch() }, p.id)),
    !result.isFetching && !lists.length && jsx('div', { className: 'revamp-note', role: 'status', children: t(text ? 'noResults' : 'emptyPlaylists') }, 'none')
  ] })
}

// A saved playlist: play it, or rename / delete it in place (an inline "Delete? Yes / No",
// never a browser dialog: those block the whole Hermes window).
function PlaylistRow({ p, onPlay, onChanged }) {
  const t = usePluginI18n(ID)
  const [mode, setMode] = useState(null) // null | 'rename' | 'delete'
  const [name, setName] = useState(p.name)
  const [busy, setBusy] = useState(false)
  const path = `/playlists/${encodeURIComponent(p.id)}`
  async function act(fn) {
    setBusy(true)
    try {
      await fn()
      await onChanged()
      setMode(null)
    } catch (err) {
      host.notify({ kind: 'error', message: `REVamp: ${err?.message || t('failed')}` })
    } finally {
      setBusy(false)
    }
  }
  const cancel = () => { setMode(null); setName(p.name) }
  const onKeyDown = e => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel() } }
  if (mode === 'rename') {
    return jsx('div', { className: 'revamp-row', children: jsxs('form', {
      className: 'revamp-ask', onKeyDown,
      onSubmit: e => {
        e.preventDefault()
        const v = name.trim()
        if (!v || v === p.name) return cancel()
        void act(() => rest(path, { method: 'PUT', body: { name: v } }))
      },
      children: [
        jsx('input', { value: name, autoFocus: true, onChange: e => setName(e.target.value), 'aria-label': `${t('rename')}: ${p.name}`, disabled: busy }),
        jsx('button', { type: 'submit', disabled: busy, children: t('save') }),
        jsx('button', { type: 'button', onClick: cancel, children: t('cancel') })
      ]
    }) })
  }
  if (mode === 'delete') {
    return jsx('div', { className: 'revamp-row', children: jsxs('div', { className: 'revamp-ask', role: 'group', onKeyDown, children: [
      jsx('span', { children: t('deleteAsk').replace('{name}', p.name) }),
      jsx('button', { type: 'button', 'data-danger': true, disabled: busy, autoFocus: true, onClick: () => void act(() => rest(path, { method: 'DELETE' })), children: t('yes') }),
      jsx('button', { type: 'button', onClick: cancel, children: t('no') })
    ] }) })
  }
  return jsx(Row, {
    name: p.name, sub: `${p.count} ${t('tracks')}`, current: false, playing: false, onPlay,
    extra: jsxs('span', { className: 'revamp-acts', children: [
      jsx(SmallAction, { label: `${t('rename')}: ${p.name}`, icon: PencilIcon, size: 'icon-xs', onClick: () => { setName(p.name); setMode('rename') } }),
      jsx(SmallAction, { label: `${t('deletePl')}: ${p.name}`, icon: TrashIcon, size: 'icon-xs', onClick: () => setMode('delete') })
    ] })
  })
}

// ---------------------------------------------------------------- queue + sleep timer
let sleepChoice = null // the minutes last picked here, so the chip stays lit

function setSleep(value) {
  sleepChoice = value
  command('sleep', value === 'track' ? { end_of_track: true } : { minutes: Number(value) || 0 })
  setTimeout(refreshLive, 400)
}

function fmtLeft(sec) {
  if (!Number.isFinite(sec)) return ''
  if (sec < 60) return `${Math.max(0, Math.round(sec))}s`
  const m = Math.ceil(sec / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`
}

function sleepText(sleep, t) {
  if (!sleep) return ''
  return sleep.mode === 'track' ? t('sleepTrack') : t('sleepLeft').replace('{time}', fmtLeft(sleep.remaining))
}

const SLEEP_CHOICES = ['0', '15', '30', '45', '60', 'track']
const sleepValue = sleep => (!sleep ? '0' : sleep.mode === 'track' ? 'track' : sleepChoice && sleepChoice !== 'track' && sleepChoice !== '0' ? sleepChoice : '')
const sleepLabel = (v, t) => (v === '0' ? t('sleepOff') : v === 'track' ? t('sleepEnd') : t('sleepMin').replace('{n}', v))

// The full page's header: a compact select (the popover has chips).
function SleepSelect({ np }) {
  const t = usePluginI18n(ID)
  const value = sleepValue(np.sleep)
  return jsxs('select', {
    className: 'revamp-select', value, 'aria-label': t('sleep'), title: sleepText(np.sleep, t) || t('sleep'),
    onChange: e => setSleep(e.target.value),
    children: [
      value === '' && jsx('option', { value: '', children: sleepText(np.sleep, t) }, 'cur'),
      ...SLEEP_CHOICES.filter(v => v !== 'track' || np.seekable).map(v => jsx('option', { value: v, children: v === '0' ? `☾ ${t('sleep')}: ${t('sleepOff')}` : sleepLabel(v, t) }, v))
    ]
  })
}

function LikeButton({ np }) {
  const t = usePluginI18n(ID)
  const liked = useValue($liked)
  if (!np.trackId) return null
  const on = Boolean(liked?.has(np.trackId))
  return jsx(SmallAction, {
    label: t(on ? 'unlike' : 'like'), icon: on ? HeartOn : HeartOff, size: 'icon-xs', pressed: on,
    className: 'revamp-like', onClick: () => void setLiked(np.trackId, !on)
  })
}

// The queue (Webamp's playlist as last saved, with the live position) and the sleep timer.
// Edits are commands to the player, which keeps Webamp and the saved queue in step.
function QueuePanel({ np }) {
  const t = usePluginI18n(ID)
  const live = useValue($live)
  const queue = Array.isArray(live?.playlist) ? live.playlist : []
  const [asking, setAsking] = useState(false)
  const sleepNow = sleepValue(np.sleep)
  function edit(change) {
    const playlist = heldQueue?.playlist || queue
    const index = heldQueue?.index ?? np.index
    const next = change(playlist, index)
    if (!next) return
    heldQueue = { ...next, until: Date.now() + 3000 } // shown now; the player confirms it
    $live.set(withHeld($live.get()))
    queueEdits = queueEdits
      .then(() => command(next.action, next.extra))
      .then(() => setTimeout(refreshLive, 500))
  }
  const move = (item, direction) => edit((playlist, index) => {
    const from = playlist.indexOf(item)
    const to = from + direction
    if (from < 0 || to < 0 || to >= playlist.length) return null
    const next = [...playlist]
    next.splice(to, 0, next.splice(from, 1)[0])
    let nextIndex = index
    if (index === from) nextIndex = to
    else if (from < index && to >= index) nextIndex--
    else if (from > index && to <= index) nextIndex++
    return { action: 'queue_move', extra: { from, to }, playlist: next, index: nextIndex }
  })
  const remove = item => edit((playlist, index) => {
    const removeIndex = playlist.indexOf(item)
    if (removeIndex < 0) return null
    const next = playlist.filter((_, i) => i !== removeIndex)
    let nextIndex = index
    if (removeIndex < index) nextIndex--
    else if (removeIndex === index) nextIndex = Math.min(index, next.length - 1)
    return { action: 'queue_remove', extra: { index: removeIndex }, playlist: next, index: nextIndex }
  })
  return jsxs('div', { children: [
    jsxs('div', { className: 'revamp-qhead', children: [
      jsx(SmallAction, { label: t('back'), icon: icons.ChevronLeft, size: 'icon-xs', onClick: () => $queueOpen.set(false) }),
      asking
        ? jsxs('div', { className: 'revamp-ask', role: 'group', children: [
          jsx('span', { children: t('clearAsk') }),
          jsx('button', {
            type: 'button', 'data-danger': true, autoFocus: true,
            onClick: () => {
              setAsking(false)
              edit(() => ({ action: 'queue_clear', extra: {}, playlist: [], index: -1 }))
            },
            children: t('yes')
          }),
          jsx('button', { type: 'button', onClick: () => setAsking(false), children: t('no') })
        ] })
        : jsxs(Fragment, { children: [
          jsx('span', { children: `${t('upNext')} · ${queue.length}` }),
          jsx('span', { className: 'revamp-space' }),
          queue.length ? jsx(Button, { variant: 'ghost', size: 'sm', onClick: () => setAsking(true), children: t('clearQueue') }) : null
        ] })
    ] }),
    jsx('div', { className: 'revamp-chips', role: 'group', 'aria-label': t('sleep'), children: [
      jsx('span', { className: 'revamp-row-sub', style: { lineHeight: '20px' }, children: `☾ ${t('sleep')}` }, 'l'),
      ...SLEEP_CHOICES.filter(v => v !== 'track' || np.seekable).map(v => jsx('button', {
        type: 'button', className: 'revamp-chip', 'aria-pressed': sleepNow === v, 'data-sleep': v,
        onClick: () => setSleep(v), children: sleepLabel(v, t)
      }, v))
    ] }),
    jsx('div', { className: 'revamp-list', children: jsx(Fragment, { children: [
      ...queue.map((it, i) => {
        const name = it?.title || '?'
        const current = i === np.index
        return jsxs('div', { className: 'revamp-row', 'data-current': current, children: [
          jsxs(RowButton, {
            className: 'revamp-row-main', 'aria-label': `${t('playFrom')}: ${name}`, 'aria-current': current ? 'true' : undefined,
            onClick: () => { if (current) toggle(); else { command('play_index', { index: i }); setTimeout(refreshLive, 500) } },
            children: [
              jsx('span', { className: 'revamp-num', children: i + 1 }),
              jsxs('span', { className: 'revamp-row-copy', children: [
                jsx('span', { className: 'revamp-row-name', children: name }),
                it?.artist ? jsx('span', { className: 'revamp-row-sub', children: it.artist }) : null
              ] })
            ]
          }),
          jsxs('span', { className: 'revamp-acts', children: [
            i > 0 ? jsx(SmallAction, { label: `${t('moveUp')}: ${name}`, icon: UpIcon, size: 'icon-xs', onClick: () => move(it, -1) }) : null,
            i < queue.length - 1 ? jsx(SmallAction, { label: `${t('moveDown')}: ${name}`, icon: DownIcon, size: 'icon-xs', onClick: () => move(it, 1) }) : null,
            jsx(SmallAction, { label: `${t('removeItem')}: ${name}`, icon: icons.X, size: 'icon-xs', onClick: () => remove(it) })
          ] })
        ] }, `${i}:${it?.id || it?.track_id || it?.url}`)
      }),
      !queue.length && jsx('div', { className: 'revamp-note', role: 'status', children: t('emptyQueue') }, 'none')
    ] }) })
  ] })
}

// ---------------------------------------------------------------- focus
// The mini player's Focus tab. It only talks through focusRemote, so it can
// follow Focus wherever it ends up being played (today: SSE commands that the
// player page routes to its Focus engine).
// On: the player page runs Focus as the "focus" playback backend.
const FOCUS_TAB = true
const FOCUS_DEFAULTS = { warmth: 0.6, noise: 0.35, depth: 0.35, rate: 16, timer: 0 }
const focusRemote = {
  start: () => command('focus_start'),
  stop: () => command('focus_stop'),
  set: params => command('focus_set', { params })
}

function FocusPanel({ np }) {
  const t = usePluginI18n(ID)
  const live = useValue($live)
  const saved = { ...FOCUS_DEFAULTS, ...(live?.focus || {}) }
  const [local, setLocal] = useState({}) // values being dragged, ahead of the server
  const timers = useRef({})
  const localTimers = useRef({})
  const value = k => local[k] ?? saved[k]
  const active = np.kind === 'focus' && np.status !== 'stopped'
  const change = (k, v) => {
    setLocal(cur => ({ ...cur, [k]: v }))
    clearTimeout(timers.current[k])
    clearTimeout(localTimers.current[k])
    timers.current[k] = setTimeout(() => {
      void focusRemote.set({ [k]: v })
      // Let the next poll catch up before showing the server's value again.
      localTimers.current[k] = setTimeout(() => {
        setLocal(cur => {
          const { [k]: _, ...rest } = cur
          return rest
        })
      }, 2500)
    }, 150)
  }
  useEffect(() => () => {
    Object.values(timers.current).forEach(clearTimeout)
    Object.values(localTimers.current).forEach(clearTimeout)
  }, [])
  const slider = (k, key, max) => jsxs('label', { className: 'revamp-focus-row', children: [
    jsx('span', { children: t(key) }),
    jsx('input', { type: 'range', min: 0, max, step: 0.01, value: value(k), 'aria-label': t(key), 'data-focus': k, onChange: e => change(k, Number(e.target.value)) })
  ] }, k)
  return jsxs('div', { className: 'revamp-focus', children: [
    jsx(Button, {
      variant: active ? 'secondary' : 'default', size: 'sm', 'data-focus-start': true,
      onClick: () => { void (active ? focusRemote.stop() : focusRemote.start()); setTimeout(refreshLive, 600) },
      children: t(active ? 'focusStop' : 'focusStart')
    }),
    slider('depth', 'focusPulse', 0.6),
    slider('warmth', 'focusTone', 1),
    slider('noise', 'focusNoise', 1),
    jsxs('div', { className: 'revamp-focus-row', children: [
      jsx('span', { children: t('focusTimer') }),
      jsx('div', { className: 'revamp-chips', children: [0, 25, 50, 90].map(m => jsx('button', {
        className: 'revamp-chip', 'aria-pressed': value('timer') === m, 'data-timer': m,
        onClick: () => change('timer', m), children: m ? t('focusMinutes').replace('{n}', m) : t('focusTimerOff')
      }, m)) })
    ] }),
    jsx('div', { className: 'revamp-note', style: { padding: '0 0 4px' }, children: t('focusNote') })
  ] })
}

const SECTIONS = { radio: RadioList, library: LibraryList, books: BooksList, focus: FocusPanel, playlists: PlaylistList }
if (!FOCUS_TAB) delete SECTIONS.focus
const PLACEHOLDER = { radio: 'searchRadio', library: 'searchLibrary', books: 'searchBooks', playlists: 'searchPlaylists' }

function Picker({ np }) {
  const t = usePluginI18n(ID)
  const [section, setSection] = useState(() => {
    // First open: the tab for what's playing (kind is music | audiobook | stream | focus; track on old pages).
    const saved = ctxRef?.storage.get('section', { audiobook: 'books', stream: 'radio', focus: 'focus' }[np.kind] || 'library')
    return Object.hasOwn(SECTIONS, saved) ? saved : 'radio'
  })
  const [text, setText] = useState('')
  const listRef = useRef(null)
  const trimmed = text.trim()
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0 }, [trimmed, section])
  const [pending, setPending] = useState(false)
  const [foldersOpen, setFoldersOpen] = useState(false)
  // Music, Books, Focus, Radio, Playlists: same order as the full player.
  const shown = section
  const options = [
    { id: 'library', label: t('library') }, { id: 'books', label: t('books') }, { id: 'focus', label: t('focus') },
    { id: 'radio', label: t('radio') }, { id: 'playlists', label: t('playlists') }
  ]
  const local = shown === 'library' || shown === 'books'
  return jsxs('div', { children: [
    jsx('div', { className: 'revamp-tabs', children: jsx(SegmentedControl, {
      options, value: shown, sizing: 'equal',
      onChange: id => { setSection(id); setText(''); setFoldersOpen(false); ctxRef?.storage.set('section', id) }
    }) }),
    shown === 'focus' ? jsx(FocusPanel, { np }) : foldersOpen && local
      ? jsx(FolderPanel, { kind: shown === 'books' ? 'books' : 'music', onClose: () => setFoldersOpen(false) }, shown)
      : jsxs(Fragment, { children: [
        jsxs('div', { className: 'revamp-search', children: [
          jsx(SearchField, {
            value: text, onChange: setText, placeholder: t(PLACEHOLDER[shown]), containerClassName: 'revamp-search-field',
            trailingAction: pending ? jsx('span', { className: 'revamp-loader', children: jsx(GlyphSpinner, { ariaLabel: t('searching') }) }) : null
          }),
          local && jsx(SmallAction, { label: t(shown === 'books' ? 'chooseBookFolders' : 'chooseFolders'), icon: icons.FolderOpen, size: 'icon-xs', onClick: () => setFoldersOpen(true) })
        ] }),
        jsx('div', { ref: listRef, className: 'revamp-list', children: jsx(SECTIONS[shown], { text: trimmed, np, onPending: setPending, onText: setText }, shown) })
      ] })
  ] })
}

function Signal({ np }) {
  const t = usePluginI18n(ID)
  // Streams: title = station, artist = the ICY "now playing" song.
  const main = np.kind === 'stream' ? np.artist && np.artist !== 'Radio' ? np.artist : '' : np.title
  const sub = np.kind === 'stream' ? np.title : np.artist
  const text = np.status === 'live' ? main || sub || t('live') : np.title ? t(np.status) : t('nothing')
  return jsxs('div', { className: 'revamp-signal', children: [
    jsx(Waveform, {}),
    jsxs('div', { className: 'revamp-track', 'data-track': np.status === 'live' && Boolean(main), role: 'status', children: [
      np.status === 'live' && sub && main ? jsx('span', { className: 'revamp-track-text', style: { flexShrink: 1, maxWidth: '45%' }, children: sub }) : null,
      np.status === 'live' && sub && main ? jsx('span', { 'aria-hidden': true, style: { color: 'var(--ui-text-quaternary)' }, children: '—' }) : null,
      jsx(Tip, { label: text, children: jsx('span', { className: 'revamp-track-text', children: text }) }),
      jsx(SignalSide, { np })
    ] })
  ] })
}

// Right of the now-playing line: the sleep countdown, the heart, the queue/sleep toggle.
function SignalSide({ np }) {
  const t = usePluginI18n(ID)
  const queueOpen = useValue($queueOpen)
  const left = sleepText(np.sleep, t)
  return jsxs('span', { className: 'revamp-track-side', children: [
    left ? jsx('span', { className: 'revamp-sleep-left', role: 'status', children: left }) : null,
    jsx(LikeButton, { np }),
    jsx(SmallAction, { label: t('queueSleep'), icon: QueueIcon, size: 'icon-xs', pressed: queueOpen, onClick: () => $queueOpen.set(!$queueOpen.get()) })
  ] })
}

function Transport({ np, compact = false, extended = false }) {
  const t = usePluginI18n(ID)
  const where = useValue($where)
  const winamp = useValue($winampOpen)
  const lastVolume = useRef(np.volume || 75)
  const desktop = useValue($desktop)
  const canPop = where !== 'none' && where !== 'elsewhere'
  const size = 'icon-xs'
  // The popover's transport adds ±10 s (not for live radio), shuffle and repeat.
  const seek = extended && np.seekable
  return jsxs('div', { className: 'revamp-transport', 'data-extended': extended, children: [
    jsx(SmallAction, { label: t('prev'), icon: PrevIcon, size, onClick: () => command('prev') }),
    seek ? jsx(SmallAction, { label: t('back10'), icon: Back10Icon, size, onClick: () => command('seek_by', { seconds: -10 }) }) : null,
    jsx(SmallAction, { label: t(np.playing ? 'pause' : 'play'), icon: np.playing ? icons.Pause : icons.Play, size, onClick: toggle }),
    seek ? jsx(SmallAction, { label: t('fwd10'), icon: Fwd10Icon, size, onClick: () => command('seek_by', { seconds: 10 }) }) : null,
    // Books: tap to step the speed (1× → 1.25× … 2.5× → 0.75×); the player keeps it for books.
    extended && np.book ? jsx(Button, {
      variant: 'ghost', size: 'micro', className: 'revamp-rate', title: t('speed'), 'aria-label': `${t('speed')}: ${np.rate}×`,
      onClick: () => command('rate', { rate: nextRate(np.rate) }), children: `${np.rate}×`
    }) : null,
    jsx(SmallAction, { label: t('next'), icon: NextIcon, size, onClick: () => command('next') }),
    extended ? jsx(SmallAction, {
      label: t('shuffle'), icon: ShuffleIcon, size, pressed: np.shuffle,
      onClick: () => { holdNp('shuffle', !np.shuffle); command('shuffle', { on: !np.shuffle }) }
    }) : null,
    extended ? jsx(SmallAction, {
      label: t('repeat'), icon: RepeatIcon, size, pressed: np.repeat,
      onClick: () => { holdNp('repeat', !np.repeat); command('repeat', { on: !np.repeat }) }
    }) : null,
    jsx('span', { className: 'revamp-space' }),
    compact ? null : jsx(SmallAction, {
      label: t(np.volume ? 'mute' : 'unmute'), icon: np.volume ? icons.Volume2 : icons.VolumeX, size,
      onClick: () => {
        if (np.volume) { lastVolume.current = np.volume; setVolume(0) } else setVolume(lastVolume.current || 75)
      }
    }),
    compact ? null : jsx('input', {
      type: 'range', min: 0, max: 100, step: 1, value: np.volume, 'aria-label': t('volume'),
      onChange: e => setVolume(Number(e.target.value))
    }),
    desktop ? jsx(SmallAction, {
      label: t('bringBackDesktop'), icon: icons.AppWindow, size, pressed: true,
      onClick: () => void bringBackFromDesktop()
    }) : canPop ? jsx(SmallAction, {
      label: t(winamp ? 'popIn' : 'popOut'), icon: icons.AppWindow, size, pressed: winamp,
      onClick: e => (e?.shiftKey && (e.ctrlKey || e.metaKey)) ? void openOnDesktop() : engine?.setWinamp(!$winampOpen.get())
    }) : null,
    jsx(SmallAction, { label: t('openFull'), icon: icons.Maximize, size, onClick: () => { $open.set(false); host.navigate(ROUTE) } })
  ] })
}


// Menu order the user asked for: Mini player · Frame (side) · Pop into Hermes · Pop out to desktop · Full screen.
function placeItems(item, extra, t) {
  const side = $dockedPlayerSide.get()
  return [
    item('mini', 'locMini'),
    item('docked', 'locDocked', ` (${side})`),
    item('floating', 'locFloating'),
    extra('desktop', t('locDesktop'), $desktop.get(), () => { $open?.set?.(false); openOnDesktop() }),
    extra('full', t('locFull'), false, () => { pageBorrowRequested = true; $open?.set?.(false); host.navigate(ROUTE) })
  ]
}

// Where the player lives; the dot marks the active one.
function LocationRow() {
  const t = usePluginI18n(ID)
  const location = useValue($playerLocation)
  useValue($dockedPlayerSide); useValue($desktop)
  const active = loc => loc === location && !$desktop.get()
  const item = (loc, key, suffix = '') => jsxs(Button, {
    variant: 'ghost', size: 'micro',
    'data-active': active(loc),
    'aria-pressed': active(loc),
    onClick: () => setPlayerLocation(loc),
    children: [active(loc) ? '● ' : '', t(key) + suffix]
  }, loc)
  const extra = (key, label, on, onClick) => jsxs(Button, {
    variant: 'ghost', size: 'micro', 'data-active': on, 'aria-pressed': on, onClick,
    children: [on ? '● ' : '', label]
  }, key)
  return jsxs('div', { style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4, padding: '2px 8px' }, 'aria-label': t('location'), children: placeItems(item, extra, t) })
}

function BarToast() {
  const toast = useValue($barToast)
  const [shown, setShown] = useState(null)
  useEffect(() => {
    if (!toast) return undefined
    setShown(toast)
    const t = setTimeout(() => setShown(null), 4000)
    return () => clearTimeout(t)
  }, [toast?.seq])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!shown) return null
  return jsx('div', {
    role: 'status',
    style: {
      position: 'absolute', bottom: 'calc(100% + 6px)', right: 8, zIndex: 50,
      maxWidth: 320, padding: '6px 10px', borderRadius: 8, fontSize: 12,
      border: '1px solid var(--ui-stroke-secondary)',
      background: 'var(--ui-bg-solid, var(--ui-bg, #222))', color: 'var(--ui-text-primary)',
      boxShadow: '0 8px 24px rgba(0,0,0,0.35)'
    },
    children: shown.text
  })
}

function RevampBar() {
  const t = usePluginI18n(ID)
  const live = useValue($live)
  const open = useValue($open)
  const queueOpen = useValue($queueOpen)
  const docked = useValue($playerLocation) === 'docked'
  const np = nowPlaying(live)
  const triggerRef = useRef(null)
  const [browseWidth, setBrowseWidth] = useState(null)
  maybeAnnounce(np)
  const name = np.kind === 'stream' ? np.title : np.title || ''
  const onOpenChange = value => {
    if (value) setBrowseWidth(triggerRef.current?.getBoundingClientRect().width ?? null)
    if (value) void loadLikes()
    $open.set(value)
  }
  return jsxs('div', { className: 'revamp-bar', 'data-playing': np.playing, style: { position: 'relative' }, children: [
    jsx(BarToast, {}),
    jsx(Waveform, { compact: true }),
    jsxs(Popover, { open, onOpenChange, children: [
      jsx(PopoverTrigger, { asChild: true, children: jsx(Button, {
        ref: triggerRef, variant: 'ghost', size: 'micro', style: { width: open ? browseWidth : undefined },
        'aria-label': `${t('browse')}: ${label(np) || t('title')}`, title: label(np) || undefined,
        children: jsx('span', { className: 'revamp-name', children: name || t('title') })
      }) }),
      jsx(PopoverContent, { side: 'top', align: 'end', className: 'revamp-panel', 'aria-label': t('title'), children:
        np.online
          ? jsxs('div', { children: [
              queueOpen ? jsx(QueuePanel, { np }) : jsx(Picker, { np }),
              jsx(LocationRow, {}),
              jsx(Signal, { np }),
              jsx(Transport, { np, extended: true })
            ] })
          : jsxs('div', { style: { padding: 8 }, children: [
            jsx(EmptyState, { title: t('offline'), description: t('offlineHint') }),
            // The gateway or the player page got stuck: reload the player and look again.
            jsx('div', { style: { display: 'flex', justifyContent: 'center', paddingTop: 6 }, children: jsx(Button, {
              size: 'sm', variant: 'outline', title: t('reconnectHint'), onClick: () => void reconnectPlayer(), children: t('reconnect')
            }) })
          ] })
      })
    ] }),
    np.online ? jsx(SmallAction, { label: t(np.playing ? 'pause' : 'play'), icon: np.playing ? icons.Pause : icons.Play, onClick: toggle }) : null,
    np.online ? jsx(SmallAction, { label: t('next'), icon: NextIcon, onClick: () => command('next') }) : null,
    // One click between the docked player and the mini player alone (also Ctrl+Alt+D / Ctrl+Alt+M).
    jsx(SmallAction, {
      label: t(docked ? 'hideDocked' : 'showDocked'), icon: DockIcon, pressed: docked, className: 'revamp-dock-toggle',
      onClick: () => setPlayerLocation(docked ? 'mini' : 'docked')
    })
  ] })
}

// ---------------------------------------------------------------- page + card
// The pane header's two location buttons. Rendered both as the in-pane row (below) and
// via the registration's data.headerContent, so hosts either way get the same controls.
function PaneHeaderButtons() {
  const L = k => EN[k] || k
  return jsxs(Fragment, { children: [
    jsx(SmallAction, { label: L('popOutPlayer'), icon: icons.AppWindow, onClick: () => setPlayerLocation('floating') }),
    jsx(SmallAction, { label: L('popIn'), icon: icons.PanelBottom, onClick: () => setPlayerLocation('mini') })
  ] })
}

function ClassicPaneSpike() {
  // `lifecycleKeepAlive` gives this ref one stable DOM parent. Re-run when the
  // asynchronously claimed engine becomes available, not on chat navigation.
  const version = useValue($engineVersion)
  const where = useValue($where)
  const location = useValue($playerLocation)
  const hostRef = useRef(null)
  // Always mounted while the pane is: the host div exists before the engine does and
  // before the player moves here, so the hand-off (showPane) can never be skipped.
  const mounted = useRef(false)
  useLayoutEffect(() => {
    const el = hostRef.current
    if (!el) return undefined
    mounted.current = true
    engine?.showPane(el)
    return () => { mounted.current = false; engine?.hidePane(el) }
  }, [version])
  const t = usePluginI18n(ID)
  const L = k => EN[k] || k
  // First-run tip, dismissed once (per Hermes data dir).
  const [tipSeen, setTipSeen] = useState(() => {
    try { return Boolean(ctxRef?.storage.get('paneTipSeen', false)) } catch { return true }
  })
  const dismissTip = () => { setTipSeen(true); try { ctxRef?.storage.set('paneTipSeen', true) } catch { /* ignore */ } }
  const playerHere = where === 'pane'
  const locationName =
    where === 'page' ? 'REVamp page' :
    where === 'desktop' ? 'desktop window' :
    location === 'mini' ? 'mini player' :
    location === 'floating' ? 'pop-out' : 'mini player'
  // Outer: the pane, in the theme's colours, the player centred at the top. Inner: a clip box
  // the engine sizes to the classic windows, so no page background shows around them.
  return jsxs('div', {
    style: { height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxSizing: 'border-box', background: 'var(--ui-surface-primary, var(--background, transparent))' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', borderBottom: '1px solid var(--ui-stroke-secondary)', flex: 'none' },
        children: [
          jsx('span', { style: { flex: 1, color: 'var(--ui-text-secondary)', fontSize: 12 }, children: '♪ REVamp' }),
          jsx(PaneHeaderButtons, {})
        ]
      }),
      jsxs('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 },
        children: [
          jsx('div', { ref: hostRef, style: playerHere
            ? { position: 'relative', overflow: 'hidden', width: '275px', height: '348px', flex: 'none', margin: '6px auto 0' }
            : { display: 'none' } }),
          playerHere ? null : jsxs('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, padding: 12 },
            children: [
              jsx('div', { style: { color: 'var(--ui-text-secondary)' }, children: `${t('playingIn')} ${locationName}.` }),
              jsx(Button, { title: L('dockedHere'), onClick: () => setPlayerLocation('docked'), children: L('dockedHere') })
            ] })
        ] }),
      !playerHere || tipSeen ? null : jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', borderTop: '1px solid var(--ui-stroke-secondary)', flex: 'none', color: 'var(--ui-text-tertiary)', fontSize: 12 },
        children: [
          jsx('span', { style: { flex: 1 }, children: 'Tip: pop out or hide the player anytime with these buttons.' }),
          jsx(Button, { onClick: dismissTip, children: 'OK' })
        ]
      })
    ]
  })
}

// The full player route either holds the one live iframe (mini / explicit
// borrow), or hosts the audio-free control shell (Frame / Pop-out / desktop).
function Page() {
  const t = usePluginI18n(ID)
  const where = useValue($where)
  const desktop = useValue($desktop)
  const hostRef = useRef(null)
  const np = nowPlaying(useValue($live))

  // Layout effect: its cleanup runs before React removes the page from the
  // document, so the player moves out while it can still move without a reload.
  // (A passive effect's cleanup runs after removal: the iframe is already gone.)
  useLayoutEffect(() => {
    const el = hostRef.current
    if (!el || !engine) return undefined
    engine.showPage(el)
    return () => engine?.hidePage(el)
  }, [where === 'none' || where === 'elsewhere', desktop])

  if (where === 'none' || (where === 'elsewhere' && !np.online)) {
    return jsx('div', { style: { padding: 24 }, children: jsx(EmptyState, { title: t('offline'), description: t('offlineHint') }) })
  }
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, minWidth: 0, overflow: 'hidden' },
    children: [
      jsxs('div', {
        // Wraps instead of overflowing: in a narrow pane the transport drops to its own line.
        style: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 8px', padding: '6px 10px', minWidth: 0, borderBottom: '1px solid var(--ui-stroke-secondary)' },
        children: [
          jsx(StatusDot, { tone: np.connected ? 'good' : 'muted' }),
          jsx('span', {
            style: { flex: '1 1 100px', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--ui-text-secondary)' },
            children: label(np) || t('nothing')
          }),
          np.online ? jsx(LikeButton, { np }) : null,
          np.online ? jsx(SleepSelect, { np }) : null,
          jsx('div', { style: { flex: '0 1 260px', minWidth: 0 }, children: jsx(Transport, { np }) })
        ]
      }),
      jsx('div', { ref: hostRef, style: { flex: 1, minHeight: 0, position: 'relative' } })
    ]
  })
}

function NowPlayingCard() {
  const t = usePluginI18n(ID)
  const np = nowPlaying(useValue($live))
  return jsxs('div', {
    style: { display: 'inline-flex', alignItems: 'center', gap: 10, padding: '6px 10px', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 8 },
    children: [
      jsx('span', { style: { color: 'var(--ui-text-secondary)' }, children: `♪ ${label(np) || t('nothing')}` }),
      jsx('div', { style: { width: 120 }, children: jsx(Transport, { np, compact: true }) })
    ]
  })
}

// ---------------------------------------------------------------- settings
// Settings → REVamp: where the player lives. The pressed button follows
// $playerLocation (useValue), and picking one moves the player at once.
function RevampSettings() {
  const t = k => EN[k] || k
  const location = useValue($playerLocation)
  const dockedSide = useValue($dockedPlayerSide)
  useValue($desktop)
  const item = (loc, key, suffix = '') => jsx(Button, {
    variant: 'ghost', size: 'sm',
    'aria-pressed': loc === location && !$desktop.get(),
    onClick: () => setPlayerLocation(loc),
    children: t(key) + suffix
  }, loc)
  const extra = (key, label, on, onClick) => jsx(Button, { variant: 'ghost', size: 'sm', 'aria-pressed': on, onClick, children: label }, key)
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 8, padding: 12, alignItems: 'flex-start' }, children: [
    jsx('h3', { style: { margin: 0, fontSize: 13, color: 'var(--ui-text-primary)' }, children: t('location') }),
    jsx('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 }, role: 'group', 'aria-label': t('location'), children: placeItems(item, extra, t) }),
    jsx('h3', { style: { margin: '8px 0 0', fontSize: 13, color: 'var(--ui-text-primary)' }, children: t('dockedPlayerSide') }),
    jsxs('div', { style: { display: 'flex', gap: 6 }, role: 'group', 'aria-label': t('dockedPlayerSide'), children: [
      jsx(Button, { variant: 'ghost', size: 'sm', 'aria-pressed': dockedSide === 'right', onClick: () => applyDockedPlayerSide('right'), children: t('dockedPlayerRight') }),
      jsx(Button, { variant: 'ghost', size: 'sm', 'aria-pressed': dockedSide === 'left', onClick: () => applyDockedPlayerSide('left'), children: t('dockedPlayerLeft') })
    ] }),
    jsx(DuckSettings, {})
  ] })
}

// Volume dips for approvals: saved by the player, so changes go to it as a "settings" command.
function DuckSettings() {
  const t = k => EN[k] || k
  const live = useValue($live)
  const st = live?.settings || {}
  const [local, setLocal] = useState({})
  const value = k => local[k] ?? st[k]
  const set = patch => {
    setLocal(cur => ({ ...cur, ...patch }))
    void command('settings', patch)
  }
  const on = value('duck_on_approval') !== false
  const toastOn = value('duck_toast') !== false
  const level = Number(value('duck_level') ?? 0.25)
  const choice = (pressed, label, onClick, key) => jsx(Button, {
    variant: 'ghost', size: 'sm', 'aria-pressed': pressed, onClick, children: label
  }, key)
  const row = (label, children) => jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 }, children: [
    jsx('span', { style: { fontSize: 12, color: 'var(--ui-text-secondary)' }, children: label }),
    jsx('div', { style: { display: 'flex', gap: 6 }, role: 'group', 'aria-label': label, children })
  ] })
  return jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 8, marginTop: 12 }, children: [
    jsx('h3', { style: { margin: 0, fontSize: 13, color: 'var(--ui-text-primary)' }, children: t('duckTitle') }),
    row(t('duckOn'), [
      choice(on, t('on'), () => set({ duck_on_approval: true }), 'on'),
      choice(!on, t('off'), () => set({ duck_on_approval: false }), 'off')
    ]),
    row(t('duckLevel'), [0, 0.1, 0.25, 0.5].map(v =>
      choice(Math.abs(level - v) < 0.01, v === 0 ? 'Silent' : `${Math.round(v * 100)}%`, () => set({ duck_level: v }), String(v)))),
    row(t('duckToast'), [
      choice(toastOn, t('on'), () => set({ duck_toast: true }), 'on'),
      choice(!toastOn, t('off'), () => set({ duck_toast: false }), 'off')
    ])
  ] })
}

// ---------------------------------------------------------------- coexistence
// Revamp and the bundled Radio share the speakers politely: when Revamp starts
// playing here it announces on Radio's channel (Radio stops itself); when
// Radio starts, Revamp pauses. Only the window that runs the player speaks.
function watchRadio(ctx) {
  if (typeof BroadcastChannel !== 'function') return
  const bus = new BroadcastChannel(RADIO_BUS)
  const sender = `revamp:${crypto.randomUUID()}`
  let wasPlaying = false
  let announcedAt = 0
  bus.onmessage = e => {
    if (e.data?.type === 'play' && e.data.sender !== sender && nowPlaying($live.get()).playing && engine) command('pause')
  }
  const unsub = $live.listen(state => {
    const playing = nowPlaying(state).playing
    // A queue swap can flicker the status for one poll; one announcement is enough.
    if (playing && !wasPlaying && engine && (Date.now() - announcedAt > 5000 || announcedAt === 0)) {
      announcedAt = Date.now()
      bus.postMessage({ type: 'play', sender })
    }
    wasPlaying = playing
  })
  ctx.onDispose(() => { unsub(); bus.close() })
}

export default {
  id: ID,
  name: 'REVamp',
  description: 'Winamp-style player that plays inside Hermes: your MP3/FLAC library, playlists and internet radio from the status bar, the full player page, or popped out as a floating Winamp. The agent can DJ.',
  defaultEnabled: true, // installed on purpose = shown; Capabilities → Plugins can still turn it off
  register(ctx) {
    ctxRef = ctx
    engine = null
    $engineVersion.set($engineVersion.get() + 1)
    waveSamples = null
    heldStatus = null
    lastPetTitle = ''
    lastPetAt = 0
    disposeDockedPlayerPane = null
    $where.set('none')
    pageBorrowRequested = false
    // The saved home: docked by default; an old winampOpen=yes migrates to floating,
    // and the legacy flag is retired (derived from the location from here on).
    const savedLoc = ctx.storage.get('playerLocation', null)
    if (savedLoc === 'docked' || savedLoc === 'floating' || savedLoc === 'mini') {
      $playerLocation.set(savedLoc)
    } else {
      const migrated = ctx.storage.get('winampOpen', false) ? 'floating' : 'docked'
      ctx.storage.set('playerLocation', migrated)
      $playerLocation.set(migrated)
    }
    ctx.storage.set('winampOpen', false)
    // New installs and malformed/missing values start on the right. The old
    // classic-pane-spike id is deliberately never contributed again, so its
    // persisted (possibly left-drifted) layout cannot win over this fresh id.
    const savedSide = ctx.storage.get('dockedPlayerSide', 'right')
    const dockedSide = DOCKED_PLAYER_SIDES.includes(savedSide) ? savedSide : 'right'
    if (savedSide !== dockedSide) ctx.storage.set('dockedPlayerSide', dockedSide)
    $dockedPlayerSide.set(dockedSide)
    $winampOpen.set($playerLocation.get() === 'floating')
    $desktop.set(false)
    $live.set(null)
    $open.set(false)
    $libVersion.set(0)
    $scanning.set(false)
    $scanProgress.set(null)
    $queueOpen.set(false)
    $liked.set(null)
    likesVersion++
    refreshGeneration++
    heldNp = {}
    heldQueue = null
    queueEdits = Promise.resolve()
    sleepChoice = null
    scanWatch++ // a watcher from before a hot reload stops touching the new atoms
    ctx.onDispose(() => {
      scanWatch++
      ctxRef = null
      engine = null
      disposeDockedPlayerPane = null
      $where.set('none')
    })
    ctx.i18n.register({ en: EN })
    const t = key => ctx.i18n.t(key)

    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())

    claimEngine(ctx)
    // The desktop window took over (from any Hermes window) or closed: park or resume here.
    const offDesktop = $desktop.listen(open => (open ? engine?.park() : engine?.unpark()))
    ctx.onDispose(offDesktop)
    void refreshLive()
    ctx.setInterval(() => void refreshLive(), 1500)
    void loadLikes()
    ctx.setInterval(() => void loadLikes(), 15000) // likes made on the full page
    watchRadio(ctx)

    // Settings → REVamp (older hosts have no settings pages).
    try { ctx.registerSettingsPage?.({ id: 'revamp', title: 'REVamp', icon: 'play', render: () => jsx(RevampSettings, {}) }) } catch { /* ignore */ }

    ctx.registerMany([
      { id: 'player', area: STATUSBAR_AREAS.right, order: 10, render: () => jsx(RevampBar, {}) },
      { id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(Page, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: ROUTE, label: EN.navLabel, codicon: 'play-circle' } },
      {
        id: 'nowplaying',
        area: TRANSCRIPT_DIRECTIVE_AREA,
        data: { name: 'nowplaying', render: () => jsx(NowPlayingCard, {}) }
      },
      ...[
        ['toggle', 'cmdToggle', () => toggle(), ['mod+alt+p']],
        ['next', 'cmdNext', () => command('next'), ['mod+alt+right']],
        ['prev', 'cmdPrev', () => command('prev'), ['mod+alt+left']],
        ['stop', 'cmdStop', () => command('stop'), []],
        ['browse', 'cmdBrowse', () => $open.set(true), ['mod+alt+b']],
        ['open', 'cmdOpen', () => host.navigate(ROUTE), []],
        ['dock', 'cmdDock', () => setPlayerLocation('docked'), ['mod+alt+d']],
        ['winamp', 'cmdWinamp', () => setPlayerLocation($playerLocation.get() === 'floating' ? 'docked' : 'floating'), ['mod+alt+w']],
        ['mini', 'cmdMini', () => setPlayerLocation('mini'), ['mod+alt+m']],
        ['reload', 'cmdReload', () => { engine?.reload(); void refreshLive() }, ['mod+alt+r']],
        ['visualizer', 'cmdVisualizer', () => engine?.visualizer(), ['mod+alt+v']],
        ['desktop', 'cmdDesktop', () => void ($desktop.get() ? bringBackFromDesktop() : openOnDesktop()), ['mod+alt+shift+w']]
      ].flatMap(([id, key, run, defaults]) => [
        {
          id: `palette-${id}`,
          area: PALETTE_AREA,
          data: { id: `revamp.${id}`, label: t(key), keywords: ['revamp', 'music', 'winamp', 'radio', id], run }
        },
        {
          id: `keybind-${id}`,
          area: KEYBINDS_AREA,
          data: { id: `revamp.${id}`, label: t(key), category: t('category'), defaults, run: () => void run() }
        }
      ])
    ])
    if (PANE_SPIKE) disposeDockedPlayerPane = ctx.register(dockedPlayerPane(dockedSide))
  }
}
