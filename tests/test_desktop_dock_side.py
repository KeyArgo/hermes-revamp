"""Static contract tests for the desktop plugin's user-selectable dock edge."""

from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"


def _source():
    return PLUGIN.read_text(encoding="utf-8")


def test_docked_player_defaults_to_a_fresh_right_pane():
    source = _source()
    assert "const PANE_IDS = { right: 'classic-pane-right', left: 'classic-pane-left' }" in source
    assert "const scopedPaneId = side => `${ID}:${PANE_IDS[side]}`" in source
    assert "const $dockedPlayerSide = atom('right')" in source
    assert "ctx.storage.get('dockedPlayerSide', 'right')" in source
    assert "dock: { pane: 'workspace', pos: side }" in source


def test_left_choice_registers_the_left_pane_and_persists():
    source = _source()
    assert "ctxRef.storage.set('dockedPlayerSide', side)" in source
    assert "applyDockedPlayerSide('left')" in source
    assert "disposeDockedPlayerPane = ctxRef.register(dockedPlayerPane(side))" in source
    assert "host.revealPane?.(scopedPaneId(side))" in source
    assert "lifecycleKeepAlive: true" in source


def test_retired_pane_id_is_never_registered():
    source = _source()
    # Mentioning it in the migration comment is intentional; it must not be a
    # pane contribution or a reveal target after this release.
    assert "id: 'classic-pane-spike'" not in source
    assert "revealPane?.('classic-pane-spike')" not in source
