"""Retrieve curated glossary terms that are found in texts."""

from seagull.lookup import Glossary, MatchedForm, TargetForm, TermResult
from seagull.schema import (
    Concept,
    ConceptId,
    GlossaryError,
    Label,
    LangCode,
    LengthForm,
    build_concept,
)

__all__ = [
    "Concept",
    "ConceptId",
    "Glossary",
    "GlossaryError",
    "Label",
    "LangCode",
    "LengthForm",
    "MatchedForm",
    "TargetForm",
    "TermResult",
    "build_concept",
]
