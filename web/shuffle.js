// shuffle.js — shuffle as a dealt deck, the way music players do it.
//
// Webamp 2.3.1's own shuffle picks a uniformly random track on every Next (it only avoids the
// current one), so a 12-song queue heard 6 distinct songs in its first 12 Nexts, songs came back
// before others had played once, and Previous jumped somewhere random. A deck is the standard
// fix: the queue is shuffled once (Fisher–Yates), Next deals the next card, every song plays
// once per pass, Previous walks back through what you heard, and with Repeat on a fresh pass
// is dealt (never starting with the song that just ended).
//
// Track ids are whatever the caller uses (Webamp's playlist ids). The deck follows the queue as
// it changes: removed songs drop out, added songs are slipped into the part not yet played.

const defaultRandom = () => {
  try {
    const buf = new Uint32Array(1);
    globalThis.crypto.getRandomValues(buf);
    return buf[0] / 4294967296;
  } catch {
    return Math.random();
  }
};

/** Fisher–Yates: every order equally likely. */
export function shuffled(ids, random = defaultRandom) {
  const out = [...ids];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

const HISTORY = 500; // songs kept behind the current one, for Previous

export function createDeck(random = defaultRandom) {
  // What was heard, then the rest of this pass. A new pass is appended, so Previous keeps
  // walking back across passes; the same song may appear once per pass.
  let order = [];
  let pos = -1;   // index in `order` of the song playing now (-1: none yet)

  // Follow the queue: keep what is still there, slip new songs into the unplayed part.
  function sync(ids) {
    const live = new Set(ids);
    const played = order.slice(0, pos + 1).filter((id) => live.has(id));
    const ahead = order.slice(pos + 1).filter((id) => live.has(id));
    const known = new Set(order);
    for (const id of ids) {
      if (!known.has(id)) ahead.splice(Math.floor(random() * (ahead.length + 1)), 0, id);
    }
    order = [...played, ...ahead];
    pos = played.length - 1;
    if (pos > HISTORY) { order = order.slice(pos - HISTORY); pos = HISTORY; }
  }

  return {
    /** A new pass that starts with `current` (when it is in the queue). */
    start(ids, current = null) {
      const has = current != null && ids.includes(current);
      order = [...(has ? [current] : []), ...shuffled(ids.filter((id) => id !== current), random)];
      pos = has ? 0 : -1;
    },
    /** The next song, or null at the end of the pass with Repeat off. */
    next(ids, { repeat = false } = {}) {
      sync(ids);
      if (pos < order.length - 1) return order[++pos];
      if (!repeat || !ids.length) return null;
      const last = order[pos];
      const fresh = shuffled(ids, random);
      if (fresh.length > 1 && fresh[0] === last) {
        const j = 1 + Math.floor(random() * (fresh.length - 1));
        [fresh[0], fresh[j]] = [fresh[j], fresh[0]];
      }
      order = [...order.slice(0, pos + 1), ...fresh]; // history stays behind the new pass
      pos += 1;
      return order[pos];
    },
    /** The song before this one in what you heard (the first one stays put). */
    prev(ids) {
      sync(ids);
      if (pos > 0) return order[--pos];
      return pos === 0 ? order[0] : null;
    },
    /** The person picked a song: it plays now, and the deck carries on from it. */
    jump(ids, id) {
      sync(ids);
      if (!ids.includes(id) || order[pos] === id) return;
      // Still to come in this pass: it plays now instead of later. Already heard: it plays
      // again now, and the rest of the pass is unchanged. Either way history is kept.
      const ahead = order.indexOf(id, pos + 1);
      if (ahead !== -1) order.splice(ahead, 1);
      order.splice(pos + 1, 0, id);
      pos += 1;
    },
    get order() { return [...order]; },
    get pos() { return pos; },
  };
}
