"""Regression reproduction: backup imports must verify their manifest hashes."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from revamp_core import backup


def test_import_refuses_zip_entry_that_does_not_match_its_manifest(tmp_path, monkeypatch):
    """A corrupted or tampered exported backup must not silently overwrite state."""
    backup_root = tmp_path / "backups"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    original = b'{"likes": ["keep-this"]}'
    (data_dir / "state.json").write_bytes(original)
    monkeypatch.setenv("REVAMP_BACKUP_DIR", str(backup_root))

    payload = b'{"likes": ["silently-corrupted"]}'
    manifest = {
        "created": 0,
        "files": {"state.json": hashlib.sha256(b"the-original-export").hexdigest()},
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("state.json", payload)
        zf.writestr("manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="manifest|hash|damaged"):
        backup.import_zip(archive.getvalue(), data_dir, root=backup_root)

    assert (data_dir / "state.json").read_bytes() == original
