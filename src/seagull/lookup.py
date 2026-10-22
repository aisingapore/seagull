"""Look up the glossary concepts a text contains, with their forms in a target language.

Glossary turns concepts into matcher input and maps matcher output back into concepts. For each
source language it builds one Matcher (match.py) using every label in that language, keyed by
(concept_id, label_index), and caches it. Lookup then runs that matcher over the text and
follows each span's keys back to the concept and the label that matched.

Each result (TermResult) holds:
    matched: every occurrence of a source form found, as written in the text, with its label's
        length_form and metadata;
    targets: the concept's target-language forms with their length_form and metadata, except
        match_only ones, in glossary order;
    metadata: the concept's metadata.

Results are ordered by each concept's first appearance in the text. A concept with no target
form is dropped, and a form shared by several concepts will return all of them in glossary
order.
"""

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from seagull.match import MatchTarget, Matcher
from seagull.schema import (
    Concept,
    ConceptId,
    LangCode,
    LengthForm,
    check_unique_ids,
    check_unique_labels,
    load_jsonl,
)


@dataclass(frozen=True)
class MatchedForm:
    """An occurrence of a glossary form in the text.

    Attributes:
        start: The start index of the hit in the NFC text.
        end: The end index of the hit in the NFC text, exclusive.
        text: The form as written in the text.
        form: The glossary form.
        length_form: The label's length_form.
        metadata: A copy of the label's metadata.
    """

    start: int
    end: int
    text: str
    form: str
    length_form: LengthForm
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TargetForm:
    """The form for a concept in the target language with its metadata.

    Attributes:
        form: The target-language form.
        length_form: The label's length_form.
        metadata: A copy of the label's metadata.
    """

    form: str
    length_form: LengthForm
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TermResult:
    """One concept found in the text.

    Multiple mentions of the same concept will only return a single TermResult.
    Each mention is stored in the 'matched' attribute.

    Attributes:
        concept_id: Unique identifier for the Concept.
        matched: Every occurrence of a source form in order of appearance in the text.
        targets: Corresponding forms in the target language, without match_only ones.
        metadata: A copy of the concept's metadata.
    """

    concept_id: ConceptId
    matched: tuple[MatchedForm, ...]
    targets: tuple[TargetForm, ...]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """The result as JSON-ready data, with lists instead of tuples."""
        return {
            "concept_id": self.concept_id,
            "matched": [asdict(m) for m in self.matched],
            "targets": [asdict(t) for t in self.targets],
            "metadata": deepcopy(self.metadata),
        }


class Glossary:
    """Loads the glossary and manages matchers and the lookup.

    Matchers are compiled per source language on first use and kept.
    """

    def __init__(self, concepts: Iterable[Concept]):
        """Validate and hold the concepts by id.

        Args:
            concepts: Concepts from build_concept.

        Raises:
            GlossaryError: Two concepts share an id, or a concept has two forms in one language
                that differ only in case or spacing.
        """
        concepts = list(concepts)
        for i, concept in enumerate(concepts):
            check_unique_labels(concept, f"concept #{i + 1}")
        check_unique_ids((c, f"concept #{i + 1}") for i, c in enumerate(concepts))
        self._concepts: dict[ConceptId, Concept] = {c.concept_id: c for c in concepts}
        self._matchers: dict[LangCode, Matcher] = {}
        self._languages: frozenset[LangCode] = frozenset(
            lang for c in self._concepts.values() for lang in c.labels
        )

    @classmethod
    def load(cls, path: str | Path) -> "Glossary":
        """Load and validate a JSONL glossary file.

        Args:
            path: Path to the JSONL file.

        Returns:
            The glossary.
        """
        return cls(load_jsonl(path))

    def __len__(self) -> int:
        return len(self._concepts)

    def get(self, concept_id: ConceptId) -> Concept | None:
        """The concept with the stipulated id, or None.

        This is the glossary's own Concept, not a copy: modifying its metadata changes the glossary.
        """
        return self._concepts.get(concept_id)

    def languages(self) -> list[LangCode]:
        """Return the set of languages supported by the glossary.

        The set of supported languages is derived by going through all Concepts
        and counting every language that has at least one Label.
        """
        return sorted(self._languages)

    def precompile_matchers(self, langs: Iterable[LangCode] | None = None) -> None:
        """Compile matchers ahead of the first lookup.

        Optional: ``lookup()`` compiles a missing matcher on first use.
        Calling this at startup eliminates the cost of compiling the matcher
        during lookup.

        Args:
            langs: Source languages to compile; ``None`` means compile every glossary language.

        Raises:
            ValueError: A language in langs is not supported by the glossary.
        """
        if langs is None:
            langs = self.languages()
        else:
            langs = list(langs)
            unknown = [lang for lang in langs if lang not in self._languages]
            if unknown:
                raise ValueError(
                    f"{', '.join(map(repr, unknown))} not supported by the glossary; "
                    f"available: {', '.join(self.languages())}"
                )
        for lang in langs:
            self._get_matcher(lang)

    def lookup(
        self, text: str, source_lang: LangCode, target_lang: LangCode
    ) -> list[TermResult]:
        """Find every concept the text contains and give its forms in the target language.

        Args:
            text: Source text written in source_lang.
            source_lang: Language of the source text.
            target_lang: Language of the returned forms.

        Returns:
            One result per concept found, ordered by its first appearance in the text. Each lists
            every occurrence of a source form (as written, with its position in the NFC text),
            every target form that isn't match_only, in glossary order, and the concept's metadata.
            A concept with no target form is left out. When one form belongs to several concepts,
            all of them are returned in glossary order. Metadata is copied, so changing a result
            never changes the glossary.

        Raises:
            ValueError: source_lang or target_lang is not in the glossary.
        """
        for name, lang in (("source_lang", source_lang), ("target_lang", target_lang)):
            if lang not in self._languages:
                raise ValueError(
                    f"{name} {lang!r} is not supported by the glossary; "
                    f"available: {', '.join(self.languages())}"
                )

        # Spans come in text order, so concepts enter this dict in order of first appearance.
        matched: dict[ConceptId, list[MatchedForm]] = {}

        spans = self._get_matcher(source_lang).match(text).spans
        for span in spans:
            for concept_id, label_index in span.keys:
                label = self._concepts[concept_id].labels[source_lang][label_index]

                mentions = matched.setdefault(concept_id, [])
                mentions.append(
                    MatchedForm(
                        start=span.start,
                        end=span.end,
                        text=span.surface,
                        form=label.form,
                        length_form=label.length_form,
                        metadata=deepcopy(label.metadata),
                    )
                )

        results: list[TermResult] = []
        for concept_id, mentions in matched.items():
            concept = self._concepts[concept_id]
            targets = tuple(
                TargetForm(label.form, label.length_form, deepcopy(label.metadata))
                for label in concept.labels.get(target_lang, ())
                if not label.match_only
            )
            if targets:
                results.append(
                    TermResult(
                        concept_id, tuple(mentions), targets, deepcopy(concept.metadata)
                    )
                )
        return results

    def _get_matcher(self, lang: LangCode) -> Matcher:
        """Get a matcher for a specific language. If non-existent, compile it first."""
        if lang not in self._matchers:
            self._matchers[lang] = Matcher(
                MatchTarget(
                    form=label.form,
                    key=(concept.concept_id, label_index),
                    case_sensitive=label.case_sensitive,
                )
                for concept in self._concepts.values()
                for label_index, label in enumerate(concept.labels.get(lang, ()))
            )
        return self._matchers[lang]
