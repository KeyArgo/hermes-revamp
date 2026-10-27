"""Regression reproduction: non-Latin searches must not turn into match-all."""

from revamp_core.library import _matches


def test_non_latin_query_does_not_match_an_unrelated_track():
    """A Chinese search term currently normalizes to an empty token (" ")."""
    assert not _matches({"title": "An unrelated English track"}, ["你好"])
