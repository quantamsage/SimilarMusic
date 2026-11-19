import pytest

from app.services.text import base_title, clean_title, is_filler, normalize, primary_artist, song_key


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Midnight City", "Midnight City"),
        ("Come Together - Remastered 2009", "Come Together"),
        ("Come Together - 2019 Remaster", "Come Together"),
        ("Bohemian Rhapsody (Remastered 2011)", "Bohemian Rhapsody"),
        ("Get Lucky (feat. Pharrell Williams)", "Get Lucky"),
        ("Get Lucky [ft. Pharrell]", "Get Lucky"),
        ("One More Time - Radio Edit", "One More Time"),
        ("Song (feat. A) - Remastered", "Song"),
        ("Yesterday - Mono", "Yesterday"),
        ("Hey Jude - Live", "Hey Jude - Live"),
        ("(Remastered)", "(Remastered)"),
    ],
)
def test_clean_title(title: str, expected: str) -> None:
    assert clean_title(title) == expected


@pytest.mark.parametrize(
    ("artist", "expected"),
    [
        ("Daft Punk, Pharrell Williams", "Daft Punk"),
        ("Calvin Harris feat. Rihanna", "Calvin Harris"),
        ("Simon & Garfunkel", "Simon & Garfunkel"),
        ("Earth, Wind & Fire", "Earth"),
        ("M83", "M83"),
    ],
)
def test_primary_artist(artist: str, expected: str) -> None:
    assert primary_artist(artist) == expected


def test_normalize_ignores_case_accents_and_punctuation() -> None:
    assert normalize("Beyoncé") == normalize("BEYONCE")
    assert normalize("Simon & Garfunkel") == "simon and garfunkel"
    assert normalize("  Don't  Stop! ") == "don t stop"


def test_base_title_spots_versions() -> None:
    assert base_title("Midnight City") == base_title("Midnight City (Eric Prydz Remix)")
    assert base_title("Midnight City") == base_title("MIDNIGHT CITY - Live at Coachella")
    assert base_title("Wait") != base_title("Midnight City")
    assert song_key("M83, Someone", "Wait (Remastered)") == ("m83", "wait")


def test_filler_detection() -> None:
    assert is_filler("Karaoke Hits Band", "Midnight City")
    assert is_filler("Studio Group", "Midnight City (In the Style of M83)")
    assert is_filler("The Tribute Co", "Made Famous by M83")
    assert not is_filler("MGMT", "Kids")
