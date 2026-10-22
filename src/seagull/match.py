"""Find glossary forms in running text via exact matching.

The Aho-Corasick algorithm is used to find every occurrence of every glossary form within the
normalized source text. Every hit is validated by rules governing word boundaries in the script
it is written in. A case-sensitive form only matches where the text has the same capitalization.
Overlapping matches are resolved by keeping the longest and leftmost forms (in a tie).

Spans refer to ``NFC(text)``, returned as ``MatchResult.text``.
"""

import bisect
import unicodedata
from collections.abc import Hashable, Iterable
from dataclasses import dataclass

import ahocorasick

from seagull.normalization import (
    normalize_and_map_char_indices,
    normalize_form,
)

# Scripts written without spaces between words: a hit ending in one of these gets syllable checks
# at that end instead of a word-boundary check.
_UNSPACED_SCRIPT_RANGES = (
    (0x0E00, 0x0E7F),  # Thai
    (0x0E80, 0x0EFF),  # Lao
    (0x1000, 0x109F),  # Burmese
    (0x1780, 0x17FF),  # Khmer
    (0x19E0, 0x19FF),  # Khmer Symbols (lunar date signs)
    (0x3005, 0x3007),  # Iteration mark 々, closing mark 〆 and zero 〇
    (0x3040, 0x30FF),  # Hiragana and Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2EE5F),  # CJK Extensions B, C, D, E, F and I (rare characters, names)
    (0x30000, 0x323AF),  # CJK Extensions G and H
)

# Any consonant written after a stacker becomes a subscript of the previous cluster.
_STACKERS = {"\u17d2", "\u1039"}  # Khmer coeng, Burmese virama

# Some Thai and Lao vowels are typed before the consonant, though spoken after it.
# A hit that does not include preposed vowels in the matching would be wrong.
_PREPOSED_VOWELS = {
    "\u0e40",
    "\u0e41",
    "\u0e42",
    "\u0e43",
    "\u0e44",  # Thai เ แ โ ใ ไ
    "\u0ec0",
    "\u0ec1",
    "\u0ec2",
    "\u0ec3",
    "\u0ec4",  # Lao ເ ແ ໂ ໃ ໄ
}

# Vowels that are keyed after the consonant and are not combining marks are also part
# of the syllable and need to be checked.
_POSTPOSED_VOWELS = {
    "\u0e30",
    "\u0e32",
    "\u0e33",
    "\u0e45",  # Thai ะ า ำ ๅ
    "\u0eb0",
    "\u0eb2",
    "\u0eb3",  # Lao ະ າ ຳ
}


def _in_unspaced_script(ch: str) -> bool:
    """Check whether a character belongs to a script written without spaces between words.

    Args:
        ch: A single character.

    Returns:
        True for scripts like Thai, Lao, Burmese, Khmer, Japanese kana and Chinese characters.
    """
    code_point = ord(ch)
    return any(first <= code_point <= last for first, last in _UNSPACED_SCRIPT_RANGES)


def _continues_spaced_script_word(ch: str) -> bool:
    """Check whether a character is alphanumeric and from a script written with spaces.

    Args:
        ch: A single character.

    Returns:
        True for letters and digits outside the unspaced scripts; False for spaces, punctuation and
        symbols, and for Chinese, Thai, Khmer and other unspaced-script characters.
    """
    return ch.isalnum() and not _in_unspaced_script(ch)


def _is_bounded(norm_text: str, start: int, end: int) -> bool:
    """Check that a hit neither continues a word nor splits a syllable, at each end separately.

    The first and last character of the hit are checked for whether they come from a script
    that separates words with spaces or not. This allows for mixed scripts to be handled correctly.

    At an end with a spaced script, the neighbouring character may not be a letter or digit of a
    spaced script. E.g. "PM" doesn't match inside "development", but "UN" matches in "UN会议",
    since Chinese characters don't continue a Latin word.

    At an end with a script without spaces, the hit must obey syllable boundaries. That is to say,
    the hit should not e.g. start after preposed vowels in Thai/Lao ("กา" inside "เกา") or stackers
    that subscript consonants (in Khmer/Burmese), or end before a combining mark
    ("ប្រាក" inside "ប្រាក់") or a postposed vowel ("เก" inside "เกา"). However, a syllable that
    ends in a final consonant can still be split ("กา" inside "การ") as this type of syllable
    requires a dictionary to determine whether the consonant belongs to the previous or next syllable.

    Args:
        norm_text: The normalized text.
        start: Hit start index.
        end: Hit end index, exclusive.

    Returns:
        True when neither end of the hit continues a word or splits a syllable.
    """
    # Check left end if hit is not at the beginning of the text.
    if start > 0:
        before = norm_text[start - 1]
        if _in_unspaced_script(norm_text[start]):
            if before in _STACKERS or before in _PREPOSED_VOWELS:
                return False
        elif _continues_spaced_script_word(before):
            return False

    # Check right end if hit is not at the end of the text.
    if end < len(norm_text):
        after = norm_text[end]
        if _in_unspaced_script(norm_text[end - 1]):
            if (
                unicodedata.category(after) in {"Mn", "Mc"}
                or after in _POSTPOSED_VOWELS
            ):
                return False
        elif _continues_spaced_script_word(after):
            return False

    return True


@dataclass(frozen=True)
class MatchTarget:
    """A form to look for in the source text, and the key reported when it is found.

    Several targets may share a normalized form (one per concept that uses it, or a
    case-sensitive and a case-insensitive spelling).

    Attributes:
        form: The form to search for as written in the glossary.
        key: Returned via MatchedSpan.keys when the form is found. Interpreted by the glossary
            lookup.
        case_sensitive: Whether exact case matching is needed.
    """

    form: str
    key: Hashable
    case_sensitive: bool = False


@dataclass(frozen=True)
class MatchedSpan:
    """A span in the NFC-normalized source text that matches a glossary form.

    Attributes:
        start: The start index of the span.
        end: The end index of the span.
        surface: The NFC-normalized surface form of the span.
        norm_form: The final normalized form of the span.
        keys: Keys for MatchTargets that pass the case-sensitivity check.
    """

    start: int
    end: int
    surface: str
    norm_form: str
    keys: tuple[Hashable, ...]


@dataclass(frozen=True)
class MatchResult:
    """The NFC-normalized text that was scanned and the spans matched in it.

    Attributes:
        text: The NFC-normalized source text.
        spans: MatchedSpans found, non-overlapping and in text order.
    """

    text: str
    spans: tuple[MatchedSpan, ...]


def _resolve_hits(hits: Iterable[MatchedSpan]) -> tuple[MatchedSpan, ...]:
    """Resolve overlapping hits, keeping the longest normalized form and then the leftmost.

    Args:
        hits: Candidate spans in NFC coordinates.

    Returns:
        The surviving spans sorted in order of appearance.
    """
    # 1. Sort in descending order of normalized form length; leftmost spans are first when tied.
    sorted_hits = sorted(hits, key=lambda h: (-len(h.norm_form), h.start))

    # 2. Drop overlapping shorter spans, keeping leftmost if overlapping spans
    # have the same length. Since kept spans don't overlap, a hit can only overlap
    # with kept spans just before and after it.
    kept_starts: list[int] = []
    kept_hits: list[MatchedSpan] = []
    for hit in sorted_hits:
        i = bisect.bisect_left(kept_starts, hit.start)
        if i > 0 and kept_hits[i - 1].end > hit.start:
            continue
        if i < len(kept_hits) and kept_hits[i].start < hit.end:
            continue
        kept_starts.insert(i, hit.start)
        kept_hits.insert(i, hit)

    return tuple(kept_hits)


class Matcher:
    """Compiled matcher over one language's forms."""

    def __init__(self, targets: Iterable[MatchTarget]):
        """Normalize every form and compile the automaton.

        Args:
            targets: The MatchTargets to look for. Forms that normalize to nothing are ignored.
        """
        self._targets: dict[str, list[MatchTarget]] = {}
        for target in targets:
            norm_form = normalize_form(target.form)
            if not norm_form:
                continue
            self._targets.setdefault(norm_form, []).append(target)

        self._automaton = ahocorasick.Automaton()
        for norm_form in self._targets.keys():
            self._automaton.add_word(norm_form, norm_form)
        self._automaton.make_automaton()

    def match(self, text: str) -> MatchResult:
        """Find every form in a given text.

        Matcher saves the keys of MatchTargets in a MatchedSpan when a match is obtained. This is
        contingent on the MatchTarget passing the case check. All case-insensitive targets will
        pass, but case-sensitive ones pass only if they have the same capitalization as the text. A
        match with no key that passes this check is dropped before overlaps are resolved, so that it
        cannot block a shorter or overlapping match. Matches that bleed into another word or that
        split a syllable are skipped as well.

        Args:
            text: Text to scan.

        Returns:
            The NFC-normalized text and its non-overlapping spans in text order.
        """
        nfc_text, norm_text, idx_map = normalize_and_map_char_indices(text)

        # Early empty return if no MatchTargets are available.
        # pyahocorasick raises an error on an automaton without words.
        if not self._targets:
            return MatchResult(text=nfc_text, spans=())

        hits: list[MatchedSpan] = []
        for end_idx, norm_form in self._automaton.iter(norm_text):
            start, end = end_idx - len(norm_form) + 1, end_idx + 1
            if not _is_bounded(norm_text, start, end):
                continue
            nfc_start, nfc_end = idx_map[start], idx_map[end - 1] + 1
            surface = nfc_text[nfc_start:nfc_end]
            cased_surface = normalize_form(surface, casefold=False)
            keys = tuple(
                target.key
                for target in self._targets[norm_form]
                if not target.case_sensitive
                or normalize_form(target.form, casefold=False) == cased_surface
            )
            if keys:
                hits.append(MatchedSpan(nfc_start, nfc_end, surface, norm_form, keys))
        return MatchResult(text=nfc_text, spans=_resolve_hits(hits))
