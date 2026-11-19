import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { NowPlaying } from '../types'

// Revamp for Claude Code. Every call to the player goes through `revampctl`
// (bin/revampctl in the revamp repo, linked onto PATH), which talks to the
// local Revamp server on 127.0.0.1:8114 with its token.

const now = atom({ plugin: 'revamp', key: 'now' } as const, null)
const isHidden = atom({ plugin: 'revamp', key: 'isHidden' } as const, false)

const TOOL = 'mcp__revamp__player'
const ACTIONS = ['status', 'play', 'pause', 'toggle', 'stop', 'next', 'prev', 'volume', 'search', 'queue', 'radio', 'url'] as const

async function ctl($: EngineInterface, args: readonly string[], timeoutMs = 8000) {
  try {
    const r = await $.process.run(['revampctl', ...args], { timeoutMs })
    const out = (r.stdout || r.stderr).trim()
    return { ok: r.exitCode === 0, out }
  } catch (err) {
    return { ok: false, out: `revampctl failed to run: ${String(err)}` }
  }
}

// Fire and forget: agent activity must never slow a tool call down. Each event is a process,
// so none go out while no player is open to show them (the ticker has no one to tell).
function emit($: EngineInterface, args: readonly string[]) {
  if (!connected) return
  $.process.run(['revampctl', 'event', ...args], { timeoutMs: 2000 }).catch(() => {})
}

// Approval tracking (see the tool.check hook): an ask counts only once it outlasts ASK_GRACE_MS.
const ASK_GRACE_MS = 6000
let pendingAsk: { cancel(): void } | null = null
let awaitingApproval = false

function settleApproval($: EngineInterface, tool: string) {
  pendingAsk?.cancel()
  pendingAsk = null
  if (awaitingApproval) {
    awaitingApproval = false
    emit($, ['approval_response', tool])
  }
}

function preview(e: Record<string, unknown>): string {
  // tool events nest the arguments under `input`; top-level keys are the fallback
  const input = (e.input && typeof e.input === 'object' ? e.input : {}) as Record<string, unknown>
  for (const src of [input, e]) {
    for (const k of ['command', 'file_path', 'pattern', 'url', 'query', 'description', 'prompt']) {
      const v = src[k]
      if (typeof v === 'string' && v) return v.replace(/\s+/g, ' ').slice(0, 80)
    }
  }
  return ''
}

// The player reports PLAYING / PAUSED / STOPPED (Webamp's names); accept any case.
// Plain words, not ⏮ ⏸ ⏭: those glyphs are blank in many terminal fonts.
export function transportLabels(isPlaying: boolean) {
  return { prev: 'Prev', toggle: isPlaying ? 'Pause' : 'Play', next: 'Next' }
}

export function isPlayingStatus(status: unknown): boolean {
  return String(status ?? '').toLowerCase() === 'playing'
}

// Now playing, without a process per glance. The server writes <data dir>/now.json while a
// player reports (revamp_core/store.py: on a change, else every 5 s), so a file older than
// NOW_STALE_MS means no player. Each session only stats it every CHECK_MS and reads it when it
// changed: no process, no request, nothing at all while REVamp sits unused. A server from
// before now.json is asked with `revampctl state`, at most once per FALLBACK_MS.
const CHECK_MS = 5000
const NOW_STALE_MS = 15000 // store.NOW_FILE_STALE
const FALLBACK_MS = 60000

let nowPath: string | null = null
let seenFile = false // this server writes now.json: never fall back again
let lastMtime = -1
let lastAsk = -Infinity
let connected = false // last known; agent events go out only while a player can show them

export async function nowFilePath($: EngineInterface): Promise<string | null> {
  const home = await $.env.get('HOME')
  const tilde = (p: string) => (home && (p === '~' || p.startsWith('~/')) ? home + p.slice(1) : p)
  const revamp = await $.env.get('REVAMP_HOME')
  if (revamp) return `${tilde(revamp)}/now.json`
  const hermes = await $.env.get('HERMES_HOME')
  if (hermes) return `${tilde(hermes)}/revamp/now.json`
  return home ? `${home}/.hermes/revamp/now.json` : null
}

export function fromNowFile(text: string): NowPlaying | null {
  const s = JSON.parse(text)
  if (!s || typeof s !== 'object' || !s.connected) return null
  const title = s.title ? `${s.title}${s.artist ? ` — ${s.artist}` : ''}` : 'nothing queued'
  return { line: title, isPlaying: isPlayingStatus(s.status), isConnected: true }
}

async function set($: EngineInterface, next: NowPlaying | null) {
  connected = Boolean(next?.isConnected)
  const prev = await read($, now)
  if (JSON.stringify(prev) !== JSON.stringify(next)) await update($, now, () => next)
}

// `force`: the person just did something; look now, and ask an old server even inside FALLBACK_MS.
async function refresh($: EngineInterface, force = false) {
  if (nowPath === null) nowPath = await nowFilePath($).catch(() => null)
  const stat = nowPath ? await $.fs.stat(nowPath).catch(() => undefined) : undefined
  if (stat) {
    seenFile = true
    if ((await $.clock.now()) - stat.mtimeMs > NOW_STALE_MS) {
      lastMtime = -1
      return set($, null)
    }
    if (stat.mtimeMs === lastMtime && !force) return
    lastMtime = stat.mtimeMs
    let next: NowPlaying | null = null
    try {
      next = fromNowFile(await $.fs.read(nowPath!))
    } catch {
      lastMtime = -1 // caught mid-replace: read it again next time
      return
    }
    return set($, next)
  }
  if (seenFile) return set($, null) // the file went away with its server
  const t = await $.clock.now()
  if (!force && t - lastAsk < FALLBACK_MS) return
  lastAsk = t
  const r = await ctl($, ['state'], 4000)
  let next: NowPlaying | null = null
  if (r.ok) {
    try {
      const s = JSON.parse(r.out)
      const live = s.live ?? {}
      const np = live.now_playing ?? {}
      const title = np.title ? `${np.title}${np.artist ? ` — ${np.artist}` : ''}` : 'nothing queued'
      next = { line: title, isPlaying: isPlayingStatus(np.status), isConnected: Boolean(live.player_connected) }
    } catch {
      next = null
    }
  }
  return set($, next)
}

// After a button or command the player reports within a heartbeat or two.
function refreshSoon($: EngineInterface) {
  refresh($, true).catch(() => {})
  $.clock.after(1500, () => refresh($, true).catch(() => {}))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'revamp',
      description: 'Revamp player: play | pause | toggle | stop | next | prev | volume N | queue <words> | radio <words> | status | url | show | hide',
    })
    await $.tool.register({
      name: 'player',
      description:
        "Control the user's Revamp music player (a Winamp clone). Actions: status, play, pause, toggle, stop, next, prev, " +
        'volume (percent), search (library, query), queue (play library matches for query), radio (tune a station matching query), url. ' +
        "Only use when the user asks for music or player control.",
      inputSchema: {
        type: 'object',
        properties: {
          action: { type: 'string', enum: ACTIONS },
          query: { type: 'string', description: 'Words for search / queue / radio' },
          percent: { type: 'integer', minimum: 0, maximum: 100, description: 'For volume' },
        },
        required: ['action'],
      },
    })
    $.clock.every(CHECK_MS, () => refresh($))
    await refresh($, true)
    return next(e)
  })

  // The model's player tool.
  on('tool.call', { tool: TOOL }, async ($, e) => {
    const input = e as unknown as { action: string; query?: string; percent?: number }
    const args = [input.action]
    if (input.action === 'volume') args.push(String(input.percent ?? 50))
    if (input.query) args.push(input.query)
    const r = await ctl($, args, 15000)
    refreshSoon($)
    return r.ok ? { result: r.out, text: r.out } : { isError: true as const, result: r.out, text: r.out }
  }).catch(() => ({ isError: true as const, result: 'Revamp tool failed', text: 'Revamp tool failed' }))

  // Every other tool call feeds Revamp's agent ticker.
  //
  // "Waiting for approval" means a PERSON is being asked. A tool.check verdict of `ask` doesn't
  // say that: in auto mode the classifier settles every ask within a second or two, and reporting
  // each one kept REVamp's banner (and the music ducking) on almost permanently. So an ask counts
  // only if the call still hasn't started ASK_GRACE_MS later.
  on('tool.check', async ($, e, next) => {
    // A new check means the previous ask was answered (a denied call never reaches tool.call).
    settleApproval($, e.tool)
    const verdict = await next(e)
    if (verdict.decision === 'ask' && e.tool !== TOOL) {
      const tool = e.tool
      const what = preview(e as unknown as Record<string, unknown>)
      pendingAsk = $.clock.after(ASK_GRACE_MS, () => {
        pendingAsk = null
        awaitingApproval = true
        emit($, ['approval_request', tool, what])
      })
    }
    return verdict
  }).catch(($, e, next) => next(e)) // cosmetic hook: fail open

  // The person answered (or typed a new prompt instead): nobody is waiting any more.
  on('prompt.submit', async ($, e, next) => {
    settleApproval($, 'prompt')
    return next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', async ($, e, next) => {
    if (e.tool === TOOL) return next(e)
    settleApproval($, e.tool) // the call started: whoever decided, decided
    emit($, ['tool_start', e.tool, preview(e as unknown as Record<string, unknown>)])
    const ran = await next(e)
    const ok = ran.deny === undefined && ran.isError !== true
    emit($, ['tool_end', e.tool, '', ok ? '1' : '0'])
    return ran
  }).catch(($, e, next) => next(e)) // cosmetic hook: fail open

  on('command.run', { command: 'revamp' }, async ($, e) => {
    const words = e.args.trim().split(/\s+/).filter(Boolean)
    const sub = words[0] ?? 'status'
    if (sub === 'hide' || sub === 'show') {
      await update($, isHidden, () => sub === 'hide')
      return { text: sub === 'hide' ? 'Revamp band hidden.' : 'Revamp band shown.' }
    }
    const r = await ctl($, sub === 'radio' || sub === 'queue' ? [sub, words.slice(1).join(' ')] : words.length ? words : ['status'], 15000)
    await refresh($, true)
    return { text: r.out || (r.ok ? 'ok' : 'Revamp did not answer.') }
  })

  // Now-playing band above the prompt, with transport buttons.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const np = await read($, now)
    if (e.props.hasSurvey || np === null || !np.isConnected || (await read($, isHidden))) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    const press = (action: string) => () => {
      ctl($, [action]).then(() => refreshSoon($))
    }
    return (
      <Box flexDirection="row" gap={1}>
        <Text color={np.isPlaying ? 'success' : undefined} dimColor={!np.isPlaying}>
          {np.isPlaying ? '♪' : '❚❚'} {np.line.slice(0, Math.max(10, (e.props.bodyColumns ?? 80) - 32))}
        </Text>
        <Button key="prev" plain label={transportLabels(np.isPlaying).prev} onPress={press('prev')} />
        <Button key="toggle" plain label={transportLabels(np.isPlaying).toggle} onPress={press('toggle')} />
        <Button key="next" plain label={transportLabels(np.isPlaying).next} onPress={press('next')} />
      </Box>
    )
  })
}
