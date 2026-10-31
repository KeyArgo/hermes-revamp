"""The executable third-party bundle must remain traceable to its source."""

import hashlib
from pathlib import Path


def test_webamp_bundle_hash_and_provenance_notice_match():
    root = Path(__file__).resolve().parent.parent
    bundle = root / "web/vendor/webamp.butterchurn-bundle.min.mjs"
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert digest == "0c5f7fe0dc77d5c53fb56fdbf12282000632b8efc7564156d9bbfeaf4333be3e"
    assert "webamp@2.3.1" in notice
    assert digest in notice
    assert "679e18bd04b837e0fb420bf08a7d194d502b26c894a4e282d26a491bf7509105" in notice
