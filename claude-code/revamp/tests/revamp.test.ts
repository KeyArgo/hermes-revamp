import { expect, mock, test } from 'claude-code/testing'
import { fromNowFile, isPlayingStatus, transportLabels } from '../hooks/register'

test('transport buttons use plain-text labels (glyphs are blank in many terminal fonts)', () => {
  expect(transportLabels(true)).toEqual({ prev: 'Prev', toggle: 'Pause', next: 'Next' })
  expect(transportLabels(false).toggle).toBe('Play')
})

test('play state is read in any case', async () => {
  expect(isPlayingStatus('PLAYING')).toBe(true)
  expect(isPlayingStatus('playing')).toBe(true)
  expect(isPlayingStatus('PAUSED')).toBe(false)
  expect(isPlayingStatus('STOPPED')).toBe(false)
  expect(isPlayingStatus(undefined)).toBe(false)
})

test('/revamp hide and show toggle the band', async $ => {
  const hidden = await $.command.run({ command: 'revamp', args: 'hide' })
  expect(hidden.text).toBe('Revamp band hidden.')
  const shown = await $.command.run({ command: 'revamp', args: 'show' })
  expect(shown.text).toBe('Revamp band shown.')
})

test('/revamp reports an unreachable player instead of throwing', async ($, on) => {
  mock.clock(on)
  mock.env(on, { HOME: '/home/t' })
  const r = await $.command.run({ command: 'revamp', args: 'status' })
  expect(typeof r.text).toBe('string')
})

// ---- "waiting for approval" means a person is being asked (2026-10-24)
// In auto mode every risky call is an `ask` the classifier settles in a moment; reporting each
// one kept REVamp's approval banner (and the music ducking) on almost all the time.

// A player is open: now.json is fresh. `/revamp status` makes the plugin look at once.
async function openPlayer($: any, on: any, clock: { now(): number }, status = 'PLAYING') {
  mock.env(on, { HOME: '/home/t' })
  on('fs.stat', async ($: any, e: any) => {
    if (e.path !== NOW) throw new Error('ENOENT')
    return { value: { kind: 'file', size: 80, mtimeMs: clock.now(), isLink: false } }
  })
  on('fs.read', async () => ({ value: JSON.stringify({ connected: true, title: 'Song', artist: 'Band', status }) }))
  await $.command.run({ command: 'revamp', args: 'status' })
}
const NOW = '/home/t/.hermes/revamp/now.json'

function recordEvents(on: Parameters<Parameters<typeof test>[1]>[1]) {
  const events: string[] = []
  on('process.run', async ($, e) => {
    if (e.argv[0] === 'revampctl' && e.argv[1] === 'event') events.push(String(e.argv[2]))
    return { value: { exitCode: 0, stdout: '', stderr: '' } }
  })
  return events
}

test('an ask settled at once (auto mode) never shows as waiting', async ($, on) => {
  const clock = mock.clock(on)
  const events = recordEvents(on)
  on('tool.check', async () => ({ decision: 'ask' }))
  await $.tool.check({ tool: 'Bash', input: { command: 'ls' } })
  await clock.advance(1000)
  await $.tool.call({ tool: 'Bash', input: { command: 'ls' } }).catch(() => {})
  await clock.advance(10000)
  expect(events.includes('approval_request')).toBe(false)
})

test('an ask nobody has answered after a few seconds shows, and clears on the answer', async ($, on) => {
  const clock = mock.clock(on)
  const events = recordEvents(on)
  on('tool.check', async () => ({ decision: 'ask' }))
  await openPlayer($, on, clock)
  await $.tool.check({ tool: 'Bash', input: { command: 'rm -rf build' } })
  await clock.advance(7000)
  expect(events.filter(x => x === 'approval_request').length).toBe(1)
  await $.tool.call({ tool: 'Bash', input: { command: 'rm -rf build' } }).catch(() => {})
  expect(events.includes('approval_response')).toBe(true)
})

// ---- idle REVamp costs nothing (2026-11-18)
// Every session ran `revampctl state` (a Python process) every 4 s: with 11 sessions open that was
// half a core while no music played. Now sessions stat now.json and start no process for it.

function countRuns(on: any) {
  const runs: string[] = []
  on('process.run', async ($: any, e: any) => {
    if (e.argv[0] === 'revampctl') runs.push(String(e.argv[1]))
    return { value: { exitCode: 3, stdout: '', stderr: 'Revamp server not reachable' } }
  })
  return runs
}
const START = { cwd: '/w', surface: 'terminal', isInteractive: true } as const
function engineStart(on: any) {
  on('session.start', async () => ({ cwd: '/w' }))
  on('command.register', async () => ({ value: undefined }))
  on('tool.register', async () => ({ value: undefined }))
}

test('no player and an old server: asked at most once a minute, no agent events', async ($, on) => {
  const clock = mock.clock(on, { now: 1_000_000 })
  mock.env(on, { HOME: '/home/t' })
  on('fs.stat', async () => { throw new Error('ENOENT') })
  const runs = countRuns(on)
  engineStart(on)
  await $.session.start(START)
  await clock.advance(5 * 60_000)
  for (let i = 0; i < 20; i++) await $.tool.call({ tool: 'Bash', input: { command: 'ls' } }).catch(() => {})
  expect(runs.filter(r => r === 'state').length).toBeLessThanOrEqual(6)
  expect(runs.filter(r => r === 'event').length).toBe(0)
})

test('now.json present: no process at all, however long the session runs', async ($, on) => {
  const clock = mock.clock(on, { now: 1_000_000 })
  mock.env(on, { HOME: '/home/t' })
  let written = clock.now()
  on('fs.stat', async ($: any, e: any) => {
    if (e.path !== NOW) throw new Error('ENOENT')
    return { value: { kind: 'file', size: 80, mtimeMs: written, isLink: false } }
  })
  on('tool.call', async () => ({ result: 'ok', text: 'ok' }))
  let reads = 0
  on('fs.read', async () => { reads++; return { value: JSON.stringify({ connected: true, title: 'Song', status: 'paused' }) } })
  const runs = countRuns(on)
  engineStart(on)
  await $.session.start(START)
  await clock.advance(10 * 60_000)  // the player went away 10 min ago: file stale
  expect(runs.filter(r => r === 'state').length).toBe(0)
  expect(reads).toBe(1)              // read once; later checks are a stat
  for (let i = 0; i < 5; i++) await $.tool.call({ tool: 'Bash', input: { command: 'ls' } }).catch(() => {})
  expect(runs.filter(r => r === 'event').length).toBe(0)  // stale = no player to tell
  written = clock.now()               // a player opens again
  await clock.advance(5000)
  await $.tool.call({ tool: 'Bash', input: { command: 'ls' } }).catch(() => {})
  expect(runs.filter(r => r === 'event').length).toBe(2)  // tool_start + tool_end
})

test('now.json lines up the band', () => {
  expect(fromNowFile('{"connected":true,"title":"A","artist":"B","status":"PLAYING"}'))
    .toEqual({ line: 'A — B', isPlaying: true, isConnected: true })
  expect(fromNowFile('{"connected":false,"title":"A"}')).toBe(null)
  expect(fromNowFile('{"connected":true,"status":"stopped"}')?.line).toBe('nothing queued')
})
