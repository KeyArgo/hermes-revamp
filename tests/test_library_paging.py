"""The library keeps loading as the user reaches its scroll marker."""

from pathlib import Path


def test_track_paging_autoloads_at_the_scroll_marker_with_button_fallback():
    text = (Path(__file__).resolve().parent.parent / "web/views/library.js").read_text(encoding="utf-8")
    start = text.index("function renderTracks()")
    end = text.index("function renderArtists()", start)
    tracks = text[start:end]
    assert 'typeof IntersectionObserver === "function"' in tracks
    assert 'root: listEl.closest(".panel-body")' in tracks  # the panel scrolls, not .stage
    assert 'loadMoreObserver.observe(more)' in tracks
    assert 'b.addEventListener("click", loadNext)' in tracks


def test_library_search_toolbar_is_sticky_while_the_panel_scrolls():
    root = Path(__file__).resolve().parent.parent
    view = (root / "web/views/library.js").read_text(encoding="utf-8")
    css = (root / "web/app.css").read_text(encoding="utf-8")
    assert 'bar.className = "btn-row lib-search-toolbar"' in view
    assert ".panel-body .lib-search-toolbar" in css
    assert "position: sticky" in css
