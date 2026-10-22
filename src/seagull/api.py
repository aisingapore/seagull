"""HTTP API over a glossary: POST a text, get back the concepts it contains.

Run with ``GLOSSARY_JSONL=glossary.jsonl uvicorn seagull.api:app``.
The glossary is loaded and every language's matcher compiled at startup to
speed up responses.
"""

import os
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from seagull.lookup import Glossary
from seagull.schema import ConceptId, LangCode, LengthForm


class LookupRequest(BaseModel):
    """One text, its language, and the languages to give translations in."""

    text: str = Field(min_length=1)
    source_lang: LangCode = Field(min_length=1)
    target_langs: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _require_nfc(cls, text: str) -> str:
        # The Glossary indexes offsets using NFC-normalized text, so the text provided
        # here must also be NFC so that offsets point accurately to the span's position.
        if not unicodedata.is_normalized("NFC", text):
            raise ValueError("Text must be NFC-normalized.")
        return text

    @field_validator("target_langs")
    @classmethod
    def _drop_repeated_langs(cls, langs: list[str]) -> list[str]:
        return list(dict.fromkeys(langs))


class Mention(BaseModel):
    """One occurrence of a glossary form in the text.

    Attributes:
        start: Start of the occurrence in the request text.
        end: End of the occurrence, exclusive.
        text: The occurrence as written, equal to text[start:end].
        form: The glossary form it matched.
        length_form: The matched form's length_form.
        metadata: The matched form's metadata.
    """

    start: int
    end: int
    text: str
    form: str
    length_form: LengthForm
    metadata: dict[str, Any]


class Translation(BaseModel):
    """One form of a concept in a target language.

    Attributes:
        form: The target language form.
        length_form: The form's length_form.
        metadata: The form's metadata.
    """

    form: str
    length_form: LengthForm
    metadata: dict[str, Any]


class ConceptResult(BaseModel):
    """One concept found in the text.

    A span whose form belongs to several concepts will appear under each of them.

    Attributes:
        concept_id: The concept's id.
        mentions: Every occurrence of the concept in the text, in order of appearance.
        translations: One entry per requested language, in request order, each with the
            concept's forms in that language in glossary order; empty when the concept has no
            form in that language.
        metadata: The concept's metadata.
    """

    concept_id: ConceptId
    mentions: list[Mention]
    translations: dict[LangCode, list[Translation]]
    metadata: dict[str, Any]


class LookupResponse(BaseModel):
    """The concepts found in a text, with their translations in the requested languages.

    A concept is returned when at least one requested language has a form for it.

    Attributes:
        text: The NFC-normalized source text that was scanned for matches.
        source_lang: The language of the source text.
        concepts: The concepts found, in order of first appearance.
    """

    text: str
    source_lang: LangCode
    concepts: list[ConceptResult]


def create_app(path: str | Path) -> FastAPI:
    """Build the app over a glossary file.

    Args:
        path: The JSONL glossary, loaded at startup.

    Returns:
        The FastAPI app.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        glossary = Glossary.load(path)
        glossary.precompile_matchers()
        app.state.glossary = glossary
        yield

    app = FastAPI(title="SEA-GULL", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        glossary: Glossary = request.app.state.glossary
        return {
            "status": "ok",
            "concepts": len(glossary),
            "languages": glossary.languages(),
        }

    @app.get("/languages")
    def languages(request: Request) -> dict[str, list[LangCode]]:
        return {"languages": request.app.state.glossary.languages()}

    @app.post("/lookup")
    def lookup(payload: LookupRequest, request: Request) -> LookupResponse:
        glossary: Glossary = request.app.state.glossary

        # Glossary.lookup gives one target language at a time: run it per language and merge
        # the results by concept.
        concepts: dict[ConceptId, ConceptResult] = {}
        for target_lang in payload.target_langs:
            try:
                results = glossary.lookup(
                    payload.text, payload.source_lang, target_lang
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            for result in results:
                if result.concept_id not in concepts:
                    concepts[result.concept_id] = ConceptResult(
                        concept_id=result.concept_id,
                        mentions=[
                            Mention.model_validate(m, from_attributes=True)
                            for m in result.matched
                        ],
                        translations={
                            target_lang: [] for target_lang in payload.target_langs
                        },
                        metadata=result.metadata,
                    )
                concepts[result.concept_id].translations[target_lang] = [
                    Translation.model_validate(t, from_attributes=True)
                    for t in result.targets
                ]

        # A concept with forms only in a later language is first seen in a later run, so this
        # restores the order of first appearance.
        ordered = sorted(concepts.values(), key=lambda c: c.mentions[0].start)
        return LookupResponse(
            text=payload.text, source_lang=payload.source_lang, concepts=ordered
        )

    return app


app = create_app(os.environ.get("GLOSSARY_JSONL", "glossary.jsonl"))
