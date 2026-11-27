import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'

const read = path => readFileSync(new URL('../' + path, import.meta.url), 'utf8')
const plugin = read('desktop/plugin.js')
function section(start, end, source = plugin) {
  const a = source.indexOf(start)
  const b = source.indexOf(end, a + start.length)
  assert.ok(a >= 0 && b > a, start)
  return source.slice(a, b)
}

test('N1 message listener requires a source before comparing frame windows', () => {
  const guard = section("ctx.addEventListener(window, 'message'", 'const fromPage')
  assert.match(guard, /if \([^)]*!e\.source[^)]*\) return/)
})

test('N2 report and diagnostic actions surface missing, refused and rejected host operations', async () => {
  const notices = []
  const ctxRef = {}
  const ctx = vm.createContext({ ctxRef, EN: {}, Button: {}, Pill: {}, ID: '', usePluginI18n: () => k => k,
    jsx: (_, props) => props, jsxs: (_, props) => props, formatDiagnostics: () => 'details',
    formatBugReportBody: () => '', buildBugReportUrl: () => '', collectPluginBugEnv: async () => ({}),
    notifyError: message => notices.push(message), host: { notify: () => {} } })
  vm.runInContext(section('async function openBugReport', 'function readPlaceMenuTipSeen') +
    section('function ReportBugRow', 'function BarToast'), ctx)
  ctx.ReportBugRow({}).children.at(-1).onClick()
  assert.equal(notices.length, 1)
  ctxRef.os = { openExternal: async () => false, writeClipboard: async () => { throw Error('refused') } }
  await ctx.openBugReport()
  await ctx.openBugReport(true)
  assert.equal(notices.length, 3)
})

test('N4 desktop settings write a state patch without a connected player', async () => {
  const requests = []
  const ctx = { rest: async (path, options) => requests.push([path, options]), refreshLive() {}, notifyError: assert.fail }
  vm.runInNewContext(section('function command(', '// Keep in sync'), ctx)
  await ctx.command('settings', { milkdropOn: false })
  const [[path, options]] = requests
  assert.equal(path, '/state')
  assert.equal(options.method, 'PUT')
  assert.equal(options.body.settings.milkdropOn, false)
  for (const [start, end] of [['MilkdropSettings', 'RevampSettings'], ['DuckSettings', '//']]) {
    assert.doesNotMatch(section('function ' + start, end), /local\[|local\.milkdropOn/)
  }
})

test('N5 diagnostics collect the build through the bridge and gateway', async () => {
  const bridge = { ready: true, request: async path => path === '/health' ? { version: '1', build: 'bridge' } : null }
  const ctx = { bridge, rest: async () => ({ version: '1', build: 'gateway' }), $live: { get() {} },
    ctxRef: null, host: {}, navigator: {}, pluginPlayerPlaceLabel() {}, formatDiagnostics() {} }
  vm.runInNewContext(section('async function collectPluginBugEnv', 'async function recoverPlayer'), ctx)
  assert.equal((await ctx.collectPluginBugEnv()).revampBuild, 'bridge')
  bridge.ready = false
  assert.equal((await ctx.collectPluginBugEnv()).revampBuild, 'gateway')
})

test('N6 failed loads notify once and never send play_index', async () => {
  for (const reply of [{ delivered: 0 }, { ok: false, error: 'unsupported' }, Error('offline')]) {
    const actions = [], errors = []
    const ctx = { rest: async (_, { body }) => {
      actions.push(body.action)
      if (reply instanceof Error) throw reply
      return reply
    }, notifyError: msg => errors.push(msg), setTimeout() {}, refreshLive() {} }
    vm.runInNewContext(section('function command(', '// Keep in sync') +
      section('async function playItems', '// Shuffle'), ctx)
    await ctx.playItems(['a', 'b'], 1)
    assert.deepEqual(actions, ['load'])
    assert.equal(errors.length, 1)
  }
})

test('N7 now file uses the Windows profile and expands backslash home paths', async () => {
  const [manifest] = JSON.parse(read('.release-check.json')).mirrors
  const hook = read(dirname(dirname(manifest)) + '/hooks/register.tsx')
  const fn = section('async function nowFilePath', 'export function fromNowFile', hook)
    .replace('$: EngineInterface', '$').replace(': Promise<string | null>', '').replace('p: string', 'p')
  const pathFor = vm.runInNewContext('(' + fn.trim() + ')')
  const env = { USERPROFILE: 'C:\\Users\\listener' }
  const host = { env: { get: key => env[key] } }
  assert.equal(await pathFor(host), env.USERPROFILE + '/.hermes/revamp/now.json')
  env.REVAMP_HOME = '~\\Music'
  assert.equal(await pathFor(host), env.USERPROFILE + '\\Music/now.json')
})

test('N8 artist subtitle counts album names', () => {
  const subtitle = section('sub: `${a.tracks}', ',\n', section('function ArtistList', 'function ', plugin)).slice(5)
  assert.equal(vm.runInNewContext(subtitle, { a: { tracks: 3, albums: ['One', 'Two'] }, t: k => k }), '3 tracks · 2 albums')
})

function pageLayout() {
  const ctx = {
    $where: { value: 'dock' }, $desktop: { value: true }, $engineVersion: { value: 0 },
    $live: {}, $playerLocation: {}, ID: '', usePluginI18n() {}, nowPlaying: () => ({}),
    useValue: atom => atom.value, useRef: () => ({ current: {} }),
    engine: null
  }
  let deps, cleanup
  ctx.useLayoutEffect = (effect, next) => {
    if (deps && deps.every((v, i) => v === next[i])) return
    deps = next
    cleanup?.()
    cleanup = effect()
  }
  vm.runInNewContext(section('function Page()', "  if (where === 'none'") + '}', ctx)
  return ctx
}

test('N9 closing the desktop window preserves the active page borrow', () => {
  const ctx = pageLayout()
  let borrowed = false
  ctx.engine = { showPage() { borrowed = false }, hidePage() {} }
  ctx.Page()
  borrowed = true
  ctx.$desktop.value = false
  ctx.Page()
  assert.equal(borrowed, true)
})

test('N10 page attaches when a remembered engine becomes ready', () => {
  const ctx = pageLayout()
  ctx.Page()
  let shown = 0
  ctx.engine = { showPage() { shown++ } }
  ctx.$engineVersion.value++
  ctx.Page()
  assert.equal(shown, 1)
})

test('N11 MilkDrop uses the renderer default until a saved choice exists', () => {
  const st = {}
  let software = false
  const ctx = { EN: {}, $live: {}, Button: {}, useValue: () => ({ settings: st }),
    jsx: (_, props) => props, jsxs: (_, props) => props, hostRendersInSoftware: () => software }
  vm.runInNewContext(section('function MilkdropSettings', 'function RevampSettings'), ctx)
  const on = () => ctx.MilkdropSettings().children[2].children[0]['aria-pressed']
  assert.equal(on(), true)
  software = true
  assert.equal(on(), false)
  st.milkdropOpen = true
  assert.equal(on(), true)
  st.milkdropOn = false
  assert.equal(on(), false)
})

test('N12 Windows paths have short row names and no POSIX mount jump', () => {
  const dir = 'C:\\Users\\listener\\Music'
  const ctx = { dirs: { data: { path: dir } }, t: k => k, navigator: { platform: 'Win32' } }
  vm.createContext(ctx)
  const base = vm.runInContext(section('const baseName =', '// Music folders') + 'baseName', ctx)
  assert.equal(base(dir + '\\'), 'Music')
  const name = section('name: tr.title ||', ',\n').slice(6)
  ctx.tr = { path: dir + '\\song.mp3' }
  assert.equal(vm.runInContext(name, ctx), 'song.mp3')
  const jumps = vm.runInContext(section('const here = dirs.data', 'return jsxs') + 'jumps', ctx)
  assert.ok(!jumps.some(j => j.path === '/mnt'))
})

test('N13 unpin deletes the stored favorite ID for a matching channel URL', async () => {
  const calls = []
  const ctx = { favorites: { data: [{ id: 'custom', url: 'stream' }], refetch() {} },
    rest: async (path, opts) => calls.push([path, opts.method]), URLSearchParams, notifyError: assert.fail }
  vm.runInNewContext(section('const pinned = Array.isArray(favorites.data)', '// Same fold') +
    section('async function pinToggle', 'const pending'), ctx)
  await ctx.pinToggle({ id: 'preset', url: 'stream' })
  assert.deepEqual(calls, [['/radio/favorites?id=custom', 'DELETE']])
})

test('N14 visualizer reaches the active player and recovery waits for engine creation', async () => {
  const calls = []
  const ctx = { engine: { visualizer: () => calls.push('local') }, $desktop: { get: () => true },
    command: action => calls.push(action) }
  const shortcut = section("['visualizer', 'cmdVisualizer', ", ", ['mod+alt+shift+v']")
  vm.runInNewContext('(' + shortcut.split("'cmdVisualizer', ")[1] + ')()', ctx)
  assert.deepEqual(calls, ['visualizer'])
  let installed
  Object.assign(ctx, { engine: null, $engineVersion: { listen(fn) { installed = fn; return () => {} } },
    wakeEngineRetry: () => setImmediate(() => { ctx.engine = { whenReady: async () => true }; installed?.() }),
    setTimeout, clearTimeout, EN: {}, refreshLive() {}, notifyError: assert.fail,
    host: { notify: notice => calls.push(notice.kind) } })
  vm.runInNewContext(section('async function recoverPlayer', 'async function openBugReport'), ctx)
  await ctx.recoverPlayer()
  assert.equal(calls.at(-1), 'success')
})

test('N15 control frame diagnostics reach the bounded diagnostic store', () => {
  const win = {}, frame = { contentWindow: {} }, record = { kind: 'error', ts: 10, message: 'failure', stack: '' }
  const rows = []
  const ctx = { window: {}, frame, controlFrame: { contentWindow: win },
    ctxRef: { storage: { get: () => [], set: (key, entries) => rows.push([key, entries]) } },
    ctx: { addEventListener: (_, name, handler) => handler({ source: win, data: { type: 'revamp:diagnostic', record } }) } }
  vm.runInNewContext(section('function savedDiagnostics', 'function buildBugReportUrl') +
    section("ctx.addEventListener(window, 'message'", 'function remove()'), ctx)
  assert.equal(rows.length, 1)
  assert.equal(rows[0][0], 'diagnostics')
  assert.equal(rows[0][1][0].message, 'failure')
})

test('N15 museum card link opens through the host and leaves the card inactive', () => {
  const events = [], handlers = {}
  const anchor = { setAttribute() {}, addEventListener: (name, fn) => { handlers[name] = fn } }
  const ctx = { link: 'https://example.test/skin', name: 'Skin', document: { createElement: () => anchor },
    row: { appendChild() {} }, openExternal: link => events.push(link) }
  vm.runInNewContext(section('if (link) {', 'if (onRemove)', read('web/views/skins.js')), ctx)
  handlers.click({ preventDefault: () => events.push('prevent'), stopPropagation: () => events.push('stop') })
  assert.deepEqual(events.sort(), [ctx.link, 'prevent', 'stop'].sort())
})

test('N4 pending player edits preserve independent desktop settings', () => {
  const player = read('web/player.js')
  const writes = []
  const ctx = { api: {}, saved: { settings: { milkdropOn: true } }, persistState: {},
    stagePersist: patch => writes.push(Object.assign(ctx.persistState, patch)) }
  vm.runInNewContext(section('api.setSavedSettings =', 'api.mainVisualizerPrefs', player), ctx)
  ctx.api.setSavedSettings({ duck_level: 0.5 })
  ctx.api.setSavedSettings({ duck_toast: false })
  assert.deepEqual({ ...writes.at(-1).settings }, { duck_level: 0.5, duck_toast: false })
})

test('N6 row and queue edits handle refusal without a success notice or a held queue', async () => {
  const successes = [], errors = []
  const ctx = {
    rest: async () => ({ delivered: 0 }), notifyError: msg => errors.push(msg),
    busy: false, setBusy() {}, batch: async () => ['track'], setMenu() {}, t: k => k,
    host: { notify: notice => successes.push(notice) }, queueEdits: Promise.resolve(), heldQueue: null,
    queue: ['track'], np: { index: 0 }, withHeld: x => x, $live: { get() {}, set() {} }, refreshLive() {}, setTimeout() {}
  }
  vm.runInNewContext(section('function command(', '// Keep in sync') +
    section('async function choose(action)', 'async function addToPlaylist') +
    section('function edit(change)', 'const move ='), ctx)
  await ctx.choose('append')
  ctx.edit(() => ({ action: 'queue_remove', extra: { index: 0 }, playlist: [], index: -1 }))
  await ctx.queueEdits
  assert.deepEqual(successes, [])
  assert.equal(errors.length, 2)
  assert.equal(ctx.heldQueue, null)
})
