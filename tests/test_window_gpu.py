"""The pop-out's native-Wayland GPU launch selection."""

from revamp_core import window


def test_float_gpu_args_only_enable_on_linux_wayland(monkeypatch):
    monkeypatch.setattr(window.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("REVAMP_FLOAT_GPU", raising=False)
    assert window.float_gpu_args() == ["--ozone-platform=wayland"]

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert window.float_gpu_args() == ["--ozone-platform=wayland"]

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert window.float_gpu_args() == []

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("REVAMP_FLOAT_GPU", "0")
    assert window.float_gpu_args() == []

    monkeypatch.setattr(window.sys, "platform", "darwin")
    monkeypatch.delenv("REVAMP_FLOAT_GPU", raising=False)
    assert window.float_gpu_args() == []
