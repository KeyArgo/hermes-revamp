import { expect, mock, test } from 'claude-code/testing'
import { isPlayingStatus } from '../hooks/register'

// The band shows ⏸ while music plays and ▶ while it is paused (2026-10-25: it always showed ▶,
// because the player sends PLAYING in capitals).
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

test('/revamp reports an unreachable player instead of throwing', async $ => {
  const r = await $.command.run({ command: 'revamp', args: 'status' })
  expect(typeof r.text).toBe('string')
})

// ---- "waiting for approval" means a person is being asked (2026-10-24)
// In auto mode every risky call is an `ask` the classifier settles in a moment; reporting each
// one kept REVamp's approval banner (and the music ducking) on almost all the time.

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
  await $.tool.check({ tool: 'Bash', input: { command: 'rm -rf build' } })
  await clock.advance(7000)
  expect(events.filter(x => x === 'approval_request').length).toBe(1)
  await $.tool.call({ tool: 'Bash', input: { command: 'rm -rf build' } }).catch(() => {})
  expect(events.includes('approval_response')).toBe(true)
})
