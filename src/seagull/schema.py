"""Defines glossary schema.

`Concept` refers to an entity or a terminology.
`Label` refers to a written form that represents a concept.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias, get_args

from seagull.normalization import normalize_form

CONCEPT_KEYS = frozenset({"concept_id", "labels", "metadata"})
LABEL_KEYS = frozenset(
    {"form", "length_form", "case_sensitive", "match_only", "metadata"}
)

ConceptId: TypeAlias = str
LangCode: TypeAlias = str  # ISO 639-1 language code, e.g. "en"

LengthForm = Literal["full", "general", "short", "acronym", "abbreviation"]
LENGTH_FORMS = frozenset(get_args(LengthForm))


class GlossaryError(ValueError):
    """An error concerning a glossary entry or file."""


@dataclass(frozen=True)
class Label:
    """One form representing a concept in one language.

    Attributes:
        form: A particular written form that refers to the concept it represents.
        length_form: Whether the form is the fully expanded form or an acronym/abbreviation etc.
        case_sensitive: True = only match if case of string found in source text is exactly the same.
        match_only: True = only used for matching, not to be returned as valid translation.
        metadata: Freely customizable metadata field e.g. to hold information for disambiguating between forms.
    """

    form: str
    length_form: LengthForm = "general"
    case_sensitive: bool = False
    match_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Concept:
    """One glossary entry: its forms per language, in order of preference, and freely customizable metadata.

    Attributes:
        concept_id: A unique identifier for the concept.
        labels: Written forms that represent the concept, grouped by language.
        metadata: Freely customizable metadata field e.g. to hold information for disambiguating between homonymic concepts.
    """

    concept_id: ConceptId
    labels: dict[LangCode, tuple[Label, ...]]
    metadata: dict[str, Any] = field(default_factory=dict)


def get_concept_id(lang: LangCode, form: str) -> ConceptId:
    """Builds concept_id for a concept that does not have one.

    Derived by applying SHA-1 hashing on UTF-8 encoded concatenation of the language code and the surface form.
    Only the initial 12 hex characters are kept to keep the length manageable. Note that the hash will change if
    the form is edited or shifted to a different location in the list.

    Args:
        lang: The first language in the entry.
        form: That language's first form.

    Returns:
        ``c-`` followed by 12 hex characters.
    """
    # \x1f used as a separator as it will never occur in the normalized form following it.
    # This prevents accidental conflation of two different lang-form combinations.
    key = f"{lang}\x1f{normalize_form(form)}"
    hashed_key = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return "c-" + hashed_key[:12]


def _validate_keys(obj: dict, allowed_keys: frozenset[str], location: str) -> None:
    """Reject keys in user's glossary that are unsupported by SEA-GULL's schemas.

    Args:
        obj: Parsed JSON object being validated.
        allowed_keys: The keys supported by SEA-GULL's schemas.
        location: Location of JSON object.

    Raises:
        GlossaryError: If obj has a key not in allowed_keys.
    """
    unsupported_keys = sorted(set(obj) - allowed_keys)
    if unsupported_keys:
        raise GlossaryError(
            f"{location}: unsupported key(s) {', '.join(map(repr, unsupported_keys))}; "
            f"allowed: {', '.join(sorted(allowed_keys))}. "
            "If necessary, please put any additional info in 'metadata'."
        )


def _get_metadata(obj: dict, location: str) -> dict[str, Any]:
    """Return the metadata of a Concept or Label.

    Args:
        obj: Parsed JSON object (Concept/Label).
        location: Location of JSON object.

    Returns:
        The metadata, or an empty dict if obj has none.

    Raises:
        GlossaryError: If the metadata is not a JSON object.
    """
    metadata = obj.get("metadata", {})
    if not isinstance(metadata, dict):
        raise GlossaryError(f"{location}: 'metadata' must be a dictionary")
    return metadata


def _build_label(obj: Any, location: str) -> Label:
    """Validate one label and build it.

    Args:
        obj: Parsed JSON object for one label.
        location: Location of JSON object.

    Returns:
        The Label.

    Raises:
        GlossaryError: If obj is not an object, has an unsupported key, has an empty form, a
            length_form outside LengthForm, a flag that is not boolean, or invalid metadata.
    """
    if not isinstance(obj, dict):
        raise GlossaryError(f"{location}: a label must be an object")

    _validate_keys(obj, LABEL_KEYS, location)

    form = obj.get("form")
    if not isinstance(form, str) or not normalize_form(form):
        raise GlossaryError(f"{location}: 'form' must be a non-empty string")

    length_form = obj.get("length_form", "general")
    if not isinstance(length_form, str) or length_form not in LENGTH_FORMS:
        raise GlossaryError(
            f"{location}: 'length_form' must be one of {', '.join(sorted(LENGTH_FORMS))}; "
            f"got {length_form!r}"
        )

    flags = {}
    for flag in ("case_sensitive", "match_only"):
        value = obj.get(flag, False)
        if not isinstance(value, bool):
            raise GlossaryError(f"{location}: '{flag}' must be true or false")
        flags[flag] = value

    metadata = _get_metadata(obj, location)

    return Label(form=form, length_form=length_form, metadata=metadata, **flags)


def build_concept(obj: Any, location: str = "entry") -> Concept:
    """Validate one parsed glossary entry and convert it into a Concept.

    Args:
        obj: Parsed JSON object for one entry.
        location: Location of JSON object, e.g. "glossary.jsonl:12".

    Returns:
        The Concept, with an id from get_concept_id if it has none.

    Raises:
        GlossaryError: If the entry or one of its labels breaks the format, or two forms in one
            language differ only in case or spacing.
    """
    if not isinstance(obj, dict):
        raise GlossaryError(f"{location}: an entry must be a JSON object")

    _validate_keys(obj, CONCEPT_KEYS, location)

    obj_labels = obj.get("labels")
    if not isinstance(obj_labels, dict) or not obj_labels:
        raise GlossaryError(
            f"{location}: 'labels' must be a non-empty object of language → list"
        )

    labels: dict[LangCode, tuple[Label, ...]] = {}
    for lang, items in obj_labels.items():
        if not lang.strip():
            raise GlossaryError(f"{location}: empty language code")

        if not isinstance(items, list) or not items:
            raise GlossaryError(
                f"{location}: labels[{lang!r}] must be a non-empty list"
            )

        labels[lang] = tuple(
            _build_label(item, f"{location}: labels[{lang!r}][{i}]")
            for i, item in enumerate(items)
        )

    concept_id = obj.get("concept_id")
    if concept_id is None:
        first_lang = next(iter(labels))
        concept_id = get_concept_id(first_lang, labels[first_lang][0].form)
    elif not isinstance(concept_id, str) or not concept_id.strip():
        raise GlossaryError(f"{location}: 'concept_id' must be a non-empty string")

    metadata = _get_metadata(obj, location)

    concept = Concept(concept_id=concept_id, labels=labels, metadata=metadata)
    check_unique_labels(concept, location)
    return concept


def check_unique_labels(concept: Concept, location: str) -> None:
    """Reject a Concept with two forms in one language that differ only in case or spacing.

    Lookup relies on this: each piece of matched text then belongs to at most one label of a
    concept.

    Args:
        concept: The Concept to check.
        location: Location of the Concept, used in the error message.

    Raises:
        GlossaryError: If two forms in one language normalize to the same string.
    """
    for lang, label_list in concept.labels.items():
        seen: dict[str, str] = {}
        for label in label_list:
            norm = normalize_form(label.form)
            if norm in seen:
                raise GlossaryError(
                    f"{location}: labels[{lang!r}] lists {seen[norm]!r} and {label.form!r}, which "
                    "differ only in case or spacing; keep one (use case_sensitive to tell them apart "
                    "in another concept)"
                )
            seen[norm] = label.form


def check_unique_ids(concepts: Iterable[tuple[Concept, str]]) -> None:
    """Reject Concepts that share an id, whether given or derived.

    Args:
        concepts: Each Concept with the location of its JSON object.

    Raises:
        GlossaryError: If two Concepts share the same concept_id
    """
    first_seen: dict[ConceptId, str] = {}
    for concept, location in concepts:
        if concept.concept_id in first_seen:
            raise GlossaryError(
                f"{location}: concept_id {concept.concept_id!r} is already used at "
                f"{first_seen[concept.concept_id]}. Give one of them an explicit, different concept_id"
            )
        first_seen[concept.concept_id] = location


def load_jsonl(path: str | Path) -> list[Concept]:
    """Read and validate a glossary file, one entry per line; blank lines are skipped.

    Args:
        path: The JSONL file.

    Returns:
        The concepts in file order.

    Raises:
        GlossaryError: If a line is not valid JSON, breaks the format, or reuses an id.
    """
    path = Path(path)
    concepts: list[tuple[Concept, str]] = []

    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            location = f"{path.name}:{line_no}"
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise GlossaryError(f"{location}: invalid JSON: {e}") from e
            concept = build_concept(obj, location)
            concepts.append((concept, location))

    check_unique_ids(concepts)
    return [concept for concept, _ in concepts]
