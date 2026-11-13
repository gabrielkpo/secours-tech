"""Text comparison shared by the bench (excerpts against pages) and the citation check (quotes
against passages): compare text, not typography."""

import re
import unicodedata

# no-break and thin no-break spaces, list bullet; curly and French quotes; dashes
_SAME = str.maketrans(
    {
        "\u00a0": " ",
        "\u202f": " ",
        "•": " ",
        "’": "'",
        "‘": "'",
        "«": '"',
        "»": '"',
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "ı": "i",
    }
)
# zero-width space, joiners, word joiner, byte order mark and soft hyphen: invisible, they would
# let two strings that read the same compare different
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
# French typography puts a space before ; : ! ? and inside quotes; a copy may keep or drop it
_TYPO_SPACE = re.compile(r'\s+(?=[;:!?])|(?<=")\s+|\s+(?=")')


def normalize(text: str) -> str:
    """Same quotes, dashes and spaces, invisible characters and list bullets dropped, a word cut
    at a line end joined back, lines joined, "œ" and "…" spelled out, an accent over a dotless i
    (as some PDFs store "î") composed on a plain i. Case and accents are kept."""
    text = text.translate(_INVISIBLE).translate(_SAME)
    text = unicodedata.normalize("NFC", text).replace("œ", "oe").replace("…", "...")
    text = re.sub(r"-\s*\n\s*", "-", text)
    text = re.sub(r"\s+", " ", text)
    return _TYPO_SPACE.sub("", text).strip()
