export type NowPlaying = { line: string; isPlaying: boolean; isConnected: boolean }

declare module 'claude-code' {
  interface PluginState {
    revamp: { now: NowPlaying | null; isHidden: boolean }
  }
}
