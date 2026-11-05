"""Filenames: a leading chapter number is a track number, not an artist (grok a10r19)."""
from revamp_core import tags


def test_a_leading_number_and_dash_is_a_track_not_an_artist():
    # "01 - An Unexpected Party" used to become artist "01", track None, so the book never
    # continued (the scanner splits "Artist - Title" first).
    assert tags._humanise("01 - An Unexpected Party") == ("An Unexpected Party", "", 1)
    assert tags._humanise("1 - Intro") == ("Intro", "", 1)


def test_the_artist_form_is_untouched():
    assert tags._humanise("Nina Simone - 07 - Sinnerman") == ("Sinnerman", "Nina Simone", 7)
    assert tags._humanise("Dune") == ("Dune", "", None)


def test_a_number_in_front_of_the_title_still_reads_as_a_track():
    assert tags._humanise("07. Title") == ("Title", "", 7)


def test_a_band_name_that_is_a_number_is_not_a_track_number():
    # Claude a10r20: "311 - Amber" lost its artist to the chapter rule.
    assert tags._humanise("311 - Amber") == ("Amber", "311", None)
    assert tags._humanise("702 - Steelo") == ("Steelo", "702", None)


def test_a_track_prefix_leaves_a_title_that_starts_with_a_number_alone():
    # Claude a10r21: the second number overwrote the first and was cut from the title.
    assert tags._humanise("01 - 2 Fast 2 Furious") == ("2 Fast 2 Furious", "", 1)
