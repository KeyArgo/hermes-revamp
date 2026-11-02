// Hosted in a Hermes frame, the page waits for Hermes to say which view it wants (the
// classic windows only, or the full player) before showing anything: otherwise the full
// library and menus flash up while the player boots, then collapse to the classic windows.
// A plain script in <head>, so it runs before the first paint. app.js lifts the wait.
(function () {
  try {
    var q = new URLSearchParams(location.search);
    if (window.parent !== window && q.has("embed") && !q.has("control")) {
      var root = document.documentElement;
      root.classList.add("awaiting-mode");
      // Never stay hidden: show whatever is there if Hermes never answers.
      setTimeout(function () { root.classList.remove("awaiting-mode"); }, 8000);
    }
  } catch (e) { /* show the page as is */ }
})();
