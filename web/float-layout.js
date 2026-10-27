// Desktop pop-out geometry. Pure: the page and the node tests share it.
// The OS window's content box has to match the classic stack (plus MilkDrop
// beside it). A window that is merely "about the right size" leaves empty
// space the player can be dragged around in.

/** Far edge of viewport-relative rects, measured from the origin.
 *  An unpinned window that sits at x=40 still counts toward the width, so a
 *  size request cannot clip it. */
export function contentSize(rects) {
  const visible = (rects || []).filter((r) => r && r.width > 1 && r.height > 1 && Number.isFinite(r.right) && Number.isFinite(r.bottom));
  if (!visible.length) return null;
  const left = Math.min(...visible.map((r) => r.left));
  const top = Math.min(...visible.map((r) => r.top));
  const right = Math.max(...visible.map((r) => r.right));
  const bottom = Math.max(...visible.map((r) => r.bottom));
  return {
    x: Math.round(left),
    y: Math.round(top),
    w: Math.ceil(right - Math.min(0, left)),
    h: Math.ceil(bottom - Math.min(0, top)),
  };
}

/** Outer window size whose content area equals the player stack.
 *  Chrome's resizeTo and setWindowBounds both take the outer size, which
 *  includes the title bar and borders (outer − inner). */
export function outerWindowSize(contentW, contentH, innerW, innerH, outerW, outerH) {
  const n = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  const chromeW = Math.max(0, n(outerW) - n(innerW));
  const chromeH = Math.max(0, n(outerH) - n(innerH));
  return {
    width: Math.round(n(contentW) + chromeW),
    height: Math.round(n(contentH) + chromeH),
  };
}
