"""Normalizing song titles and artist names for matching and deduplication."""

import re
import unicodedata

# " (feat. X)", " [ft. X]", " feat. X" at the end of a title.
FEATURING = re.compile(r"\s*[\(\[]?\s*\b(feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]?\s*$", re.IGNORECASE)
# " - Remastered 2011", " - 2011 Remaster", " (Remastered)", " - Single Version", " - Mono".
DECORATION = re.compile(
    r"\s*(?:-\s*|[\(\[])\s*(?:\d{4}\s+)?(?:digital(?:ly)?\s+)?(?:remaster(?:ed)?|re-?mastered|single version|album version|"
    r"radio edit|mono|stereo|explicit|clean)(?:\s+(?:version|\d{4}))?\s*[\)\]]?\s*$",
    re.IGNORECASE,
)
PARENTHETICAL = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
NON_WORD = re.compile(r"[^\w\s]")
SPACES = re.compile(r"\s+")
ARTIST_SEPARATORS = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+|,\s*", re.IGNORECASE)

#: Releases that are not real candidates (cover/karaoke filler on Last.fm).
FILLER = re.compile(
    r"karaoke|in the style of|made famous by|originally performed|tribute to|as made famous|\bbacking track\b",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    """Removes featuring credits and release decorations: "Song - 2011 Remaster" → "Song"."""
    previous = None
    cleaned = title.strip()
    while cleaned != previous:
        previous = cleaned
        cleaned = DECORATION.sub("", cleaned)
        cleaned = FEATURING.sub("", cleaned).strip()
    return cleaned or title.strip()


def primary_artist(artist: str) -> str:
    """The first credited artist: "Daft Punk, Pharrell Williams" → "Daft Punk"."""
    first = ARTIST_SEPARATORS.split(artist.strip(), maxsplit=1)[0].strip()
    return first or artist.strip()


def normalize(text: str) -> str:
    """Case-, accent- and punctuation-insensitive form for comparisons."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.casefold().replace("&", " and ")
    return SPACES.sub(" ", NON_WORD.sub(" ", lowered)).strip()


def base_title(title: str) -> str:
    """Normalized title without any parenthetical parts, for spotting versions of a song."""
    return normalize(PARENTHETICAL.sub("", clean_title(title)).split(" - ")[0])


def song_key(artist: str, title: str) -> tuple[str, str]:
    return normalize(primary_artist(artist)), base_title(title)


def is_filler(artist: str, title: str) -> bool:
    return bool(FILLER.search(artist) or FILLER.search(title))
