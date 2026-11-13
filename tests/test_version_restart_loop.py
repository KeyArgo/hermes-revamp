"""A checkout reads 1.0.0-dev.N on its chip; the update watcher must still see 1.0.0.

"1.0.0-dev" sorts below "1.0.0", so comparing the display version with plugin.yaml on disk made a
git checkout believe a newer REVamp was installed under it and restart itself on every check."""
from revamp_core import server


def test_the_running_version_is_the_release_the_manifest_declares():
    assert server.VERSION == server._installed_version()
    assert "-dev" not in server.VERSION


def test_the_update_watcher_sees_no_newer_install_under_a_checkout():
    disk = server._installed_version()
    assert not server._version_key(disk) > server._version_key(server.VERSION)


def test_the_chip_still_says_which_build_is_running():
    info = server._stamp_dict()
    assert info["version"].startswith(server.VERSION)
    assert info["release"] == server.VERSION
