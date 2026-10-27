"""Text normalization helper functions.

Both the glossary and text being scanned must be normalized by the same code to ensure accurate
matching.
"""

import unicodedata

# Invisible characters that NFKC leaves in place and that break exact matching.
# Visible spaces such as \u00a0 (NO-BREAK SPACE) are not included as NFKC converts them into a
# normal space.
ZERO_WIDTH_CHARS = frozenset(
    {
        "\u00ad",  # SOFT HYPHEN: optional line-break point inside a word
        "\u200b",  # ZERO WIDTH SPACE: invisible word break
        "\u200c",  # ZERO WIDTH NON-JOINER: blocks ligatures and conjuncts
        "\u200d",  # ZERO WIDTH JOINER: forces joining
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u2060",  # WORD JOINER: prevents a line break; the successor to \ufeff
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE, also the byte order mark at the start of a file
    }
)


def normalize_and_map_char_indices(
    string: str, *, casefold: bool = True
) -> tuple[str, str, list[int]]:
    """Normalize a string and record where each output character came from.

    The string is first NFC-normalized as a whole, which joins letters with the combining accents
    that follow them ("a" + U+0300 → "à"). Each character is then NFKC-normalized and casefolded by
    default. Zero-width characters are dropped, runs of whitespace become one space, and leading
    and trailing whitespaces are removed.

    Args:
        string: Any text.
        casefold: False keeps the case, for comparing case-sensitive forms.

    Returns:
        The NFC text that the indices refer to, the normalized string, and a list mapping each
        index of the normalized string to the index in the NFC text it was derived from.
    """
    nfc_text = unicodedata.normalize("NFC", string)
    normalized_chars: list[str] = []
    idx_map: list[int] = []
    # Start of the current whitespace run, or None.
    space_run_start: int | None = None

    for i, ch in enumerate(nfc_text):
        # 1. Drop all zero-width characters
        if ch in ZERO_WIDTH_CHARS:
            continue

        # 2. Apply NFKC normalization
        # NFKC and casefolding may turn one character into several ("ﬁ" → "fi", "ß" → "ss").
        norm_ch = unicodedata.normalize("NFKC", ch)

        # 3. Apply casefold as defined
        if casefold:
            norm_ch = norm_ch.casefold()

        # 4. Handle stretches of whitespace
        if norm_ch.isspace():
            if normalized_chars and space_run_start is None:
                space_run_start = i
            continue
        if space_run_start is not None:
            normalized_chars.append(" ")
            idx_map.append(space_run_start)
            space_run_start = None

        # 5. Output the character(s)
        normalized_chars.extend(norm_ch)
        idx_map.extend([i] * len(norm_ch))

    normalized_text = "".join(normalized_chars)

    return nfc_text, normalized_text, idx_map


def normalize_form(form: str, *, casefold: bool = True) -> str:
    """Normalizes the form of a glossary term.

    The same normalization procedure as the text being matched is applied
    (normalize_and_map_char_indices).

    Args:
        form: A surface form.
        casefold: False keeps the case, for comparing case-sensitive forms.

    Returns:
        The normalized string.
    """
    return normalize_and_map_char_indices(form, casefold=casefold)[1]
