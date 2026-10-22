# Southeast Asian Glossary Utility for Lexical Lookup (SEA-GULL)

`SEA-GULL` is a lightweight retrieval mechanism for glossary terms (e.g. named entities
or specific terminology) that mainly supports machine translation use cases, but can also benefit
any use case where terminologies are important. Its focus is on supporting glossary retrieval
in Southeast Asian languages, which use a variety of scripts and encodings that can make
retrieval more challenging.

It specifies a glossary schema containing useful fields that can provide important information to
better use the retrieved terms downstream. Users supply their own curated glossary in the
required format and the retriever will retrieve the relevant entries from the glossary (with their
translations and auxiliary information) whenever a match is observed in the source text to be
translated.

Retrieval is fast enough to run on every translation request. With a glossary of ~17k concepts, each
lookup takes approximately (on an Apple M4 Max):

| Source text                         | Glossary matches | Lookup time |
| ----------------------------------- | ---------------- | ----------- |
| A paragraph (500 characters)        | 5–20             | 0.2–0.4 ms  |
| A short document (5,000 characters) | 70–170           | 2–3.5 ms    |
| A long document (50,000 characters) | 750–1,700        | 20–40 ms    |
| A book (500,000 characters)         | 7,500–17,000     | 0.2–0.5 s   |

Lookup time grows with the length of the text and the number of matches in it. The figures were
measured on generated English text in which glossary names make up about 15% (lower figures) to 75%
(higher figures) of the words.


## Contents

- [1. Demo](#1-demo)
- [2. Installation](#2-installation)
- [3. How to use SEA-GULL](#3-how-to-use-sea-gull)
  - [3.1. Data Types/Schemas in SEA-GULL](#31-data-typesschemas-in-sea-gull)
  - [3.2. How to build a glossary to be used with SEA-GULL](#32-how-to-build-a-glossary-to-be-used-with-sea-gull)
  - [3.3. Using the library](#33-using-the-library)
  - [3.4. Lookup results](#34-lookup-results)
  - [3.5. HTTP API](#35-http-api)
- [4. How does matching work in SEA-GULL?](#4-how-does-matching-work-in-sea-gull)
- [5. Limitations](#5-limitations)

## 1. Demo

Visit our [demo](https://sea-gull.sea-lion.ai) page to see SEA-GULL in action!

## 2. Installation

SEA-GULL requires Python 3.12 or later, and uses [uv](https://docs.astral.sh/uv/) to manage its
dependencies.

```bash
git clone https://github.com/aisingapore/seagull.git
cd seagull

uv sync             # Install the SEA-GULL library
uv sync --extra api # Run this to be able to serve the glossary as a FastAPI HTTP API endpoint
```

## 3. How to use SEA-GULL

### 3.1. Data Types/Schemas in SEA-GULL

In SEA-GULL, entities or terminologies are referred to as `Concepts`. Each `Concept` has various
`Labels` which represent a particular representation of the `Concept` in writing.

### 3.2. How to build a glossary to be used with SEA-GULL

SEA-GULL only implements the retrieval layer and does not come with preset glossaries. It assumes
that the user's glossary has been curated already, and that all translations are correct. There
can be multiple alternatives available for translating a single term, but they should differ in
some ways (context-dependent usage).

SEA-GULL's glossary schema has been designed to take this into consideration, allowing for
additional information that can be used downstream to disambiguate between candidates.
Crucially, SEA-GULL retrieves ALL relevant `Labels` and does NOT do this disambiguation.
The implementation of the disambiguation layer is left to the user since this is often
use case-specific, dependent on available information and requires domain knowledge.

The glossary requires a JSONL format. Each JSON object in the file represents a
`Concept` and has the following schema:

#### `Concept`

| Field        | Type                                    | Required | Description                                                                                                     |
| ------------ | --------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------- |
| `concept_id` | string                                  | No       | If not provided, it is derived by hashing the first `Label`'s form (`c-` followed by 12 hexadecimal characters) |
| `labels`     | object: language code → list of `Label` | Yes      | The `Concept`'s forms in each language, e.g. `{"en": [...], "vi": [...]}`.                                      |
| `metadata`   | object                                  | No       | Any information that helps disambiguate concepts downstream.                                                    |

#### `Label`

| Field            | Type    | Required                 | Description                                                                                                                                            |
| ---------------- | ------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `form`           | string  | Yes                      | One written representation for the `Concept`, e.g. `"Association of Southeast Asian Nations"`                                                          |
| `length_form`    | string  | No (default `"general"`) | What kind of form it is: `"full"`, `"general"`, `"short"`, `"acronym"` or `"abbreviation"`                                                             |
| `case_sensitive` | boolean | No (default `false`)     | Match only where the text has exactly this capitalisation, e.g. for acronyms that are also ordinary words when lowercased (like "SEAGULL")             |
| `match_only`     | boolean | No (default `false`)     | Use this for matching against the source text, but never return it as a translation, e.g. for old names or common misspellings / alternative spellings |
| `metadata`       | object  | No                       | Any information that helps disambiguate this `Concept`'s forms downstream.                                                                             |

For example, one line of the glossary file would look like this (spread over several lines here
for readability). It is included as [`examples/glossary.jsonl`](examples/glossary.jsonl), so you
can try the examples below straight away:

```json
{"concept_id": "1001",
 "labels": {
   "en": [{"form": "Association of Southeast Asian Nations", "length_form": "full"},
          {"form": "ASEAN", "length_form": "acronym", "case_sensitive": true}],
   "vi": [{"form": "Hiệp hội các quốc gia Đông Nam Á", "length_form": "full"},
          {"form": "ASEAN", "length_form": "acronym", "case_sensitive": true}],
   "th": [{"form": "สมาคมประชาชาติแห่งเอเชียตะวันออกเฉียงใต้", "length_form": "full"},
          {"form": "อาเซียน", "length_form": "general"}]},
 "metadata": {"entity_type": "ORG", "domain": ["international organisations"]}}
```

##### Length Form

`length_form` can be useful downstream when determining which form to use. For example,
one might establish a translation guideline that asserts that the first occurrence of
any entity should be written out in full, with the acronym in brackets, e.g. Association
of Southeast Asian Nations (ASEAN), while subsequent mentions will only use the acronym.

We provide some sensible choices for `length_form` values:

| Length Form    | What it is                                 | Example                            |
| -------------- | ------------------------------------------ | ---------------------------------- |
| `full`         | Fully expanded form                        | Singapore Department of Statistics |
| `general`      | Form that is generally used                | Department of Statistics           |
| `short`        | Short version but not abbreviation/acronym | SingStat                           |
| `acronym`      | First letters of multi-word phrase         | DOS                                |
| `abbreviation` | Contraction/Truncation                     | Dept of Statistics                 |

If you find a need to add other `length_form` types, you may add them to the `LengthForm` Literal in `schema.py`

##### Order of Labels

You may want to place the most preferred `Label` as the first entry to allow for easier selection
downstream (just pick the first entry without further disambiguation). However, this is completely
dependent on your use case and how you design the downstream disambiguation layer.

##### Metadata

`metadata` is freely customizable and optional: the retriever never reads it. Feel free to add any
information that is necessary for downstream use here. Here are some suggestions:

| Metadata of | Key           | Meaning                                                               |
| ----------- | ------------- | --------------------------------------------------------------------- |
| `Concept`   | `domain`      | Topic tags                                                            |
| `Concept`   | `country`     | Provide country context (e.g. Ministry of Education in which country) |
| `Concept`   | `description` | Description/definition of the concept                                 |
| `Label`     | `derivation`  | e.g. `translation`, `transliteration`, `exonym`, `endonym`            |
| `Label`     | `note`        | When to use this form (e.g. legal context uses A, engineering uses B) |

### 3.3. Using the library

```python
import unicodedata

from seagull import Glossary

GLOSSARY_PATH = "examples/glossary.jsonl"  # The path to your JSONL glossary file

glossary = Glossary.load(GLOSSARY_PATH)

# Perform NFC normalization first to ensure returned match indices are accurate.
text = unicodedata.normalize(
    "NFC", "The Association of Southeast Asian Nations comprises 11 member states."
)
results = glossary.lookup(text, source_lang="en", target_lang="vi")
# lookup() returns a list of glossary entries
print(results[0].to_dict())
```

```json
{"concept_id": "1001",
 "matched": [{"start": 4, "end": 42, "text": "Association of Southeast Asian Nations",
              "form": "Association of Southeast Asian Nations", "length_form": "full",
              "metadata": {}}],
 "targets": [{"form": "Hiệp hội các quốc gia Đông Nam Á", "length_form": "full", "metadata": {}},
             {"form": "ASEAN", "length_form": "acronym", "metadata": {}}],
 "metadata": {"entity_type": "ORG", "domain": ["international organisations"]}}
```

### 3.4. Lookup results

`Glossary.lookup(text, source_lang, target_lang)` returns one result per concept found, in order of
first appearance:

| Field        | Description                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------- |
| `concept_id` | ID for the concept                                                                                            |
| `matched`    | Every occurrence of a source form, in order of appearance in the source text                                  |
| `targets`    | Every target-language form except `match_only` ones, in glossary order, with its `length_form` and `metadata` |
| `metadata`   | The concept's metadata                                                                                        |

Each object in `matched` is one occurrence in the source text:

| Field         | Type    | Description                                                                                   |
| ------------- | ------- | --------------------------------------------------------------------------------------------- |
| `start`       | integer | Where the occurrence starts in the source text                                                |
| `end`         | integer | Where the occurrence ends, exclusive, so `text[start:end]` is the occurrence                  |
| `text`        | string  | The occurrence as written in the source text, which may differ from `form` in case or spacing |
| `form`        | string  | The glossary form it matched                                                                  |
| `length_form` | string  | The matched form's `length_form`, e.g. `"acronym"` when the source text used the acronym      |
| `metadata`    | object  | The matched form's `metadata`                                                                 |

A form mentioned three times will result in three entries under `matched`.

`start` and `end` are code-point offsets into the NFC-normalized text, end exclusive. Normalize your
text with `unicodedata.normalize("NFC", text)` before calling `lookup()` (as in 3.3), so that the
indices match correctly. Most text is NFC already, but sometimes characters (especially the modified
Latin characters in the Vietnamese alphabet) may be decomposed. NFC ensures consistency in dealing
with such cases and that indices will match up. Glossary forms don't need to be normalized as
SEA-GULL performs its own normalization when it loads the glossary (including NFC).

### 3.5. HTTP API

Instead of using SEA-GULL as a module, you may also run it as a HTTP API endpoint (with FastAPI).
This requires running `uv sync --extra api` first to install the relevant dependencies.

```bash
GLOSSARY_JSONL=examples/glossary.jsonl uv run --extra api uvicorn seagull.api:app --port 8000

curl -s localhost:8000/lookup -H 'Content-Type: application/json' \
  -d '{"text": "The Association of Southeast Asian Nations comprises 11 member states.",
       "source_lang": "en", "target_langs": ["vi"]}'
```

`POST /lookup` takes `text`, `source_lang` and `target_langs` (a list of one or more languages)
and returns:

```json
{"text": "The Association of Southeast Asian Nations comprises 11 member states.",
 "source_lang": "en",
 "concepts": [
   {"concept_id": "1001",
    "mentions": [{"start": 4, "end": 42, "text": "Association of Southeast Asian Nations",
                  "form": "Association of Southeast Asian Nations", "length_form": "full",
                  "metadata": {}}],
    "translations": {"vi": [{"form": "Hiệp hội các quốc gia Đông Nam Á", "length_form": "full",
                             "metadata": {}},
                            {"form": "ASEAN", "length_form": "acronym", "metadata": {}}]},
    "metadata": {"entity_type": "ORG", "domain": ["international organisations"]}}]}
```

The response carries the same information as `Glossary.lookup()`, but with every requested language
in a single call.

`text` must be NFC-normalized, otherwise the request is rejected with a 422 error. Normalize it
before sending, e.g. `unicodedata.normalize("NFC", text)` in Python or `text.normalize("NFC")` in
JavaScript.

`GET /health` reports the number of concepts and the languages in the glossary. `GET /languages`
gets the list of supported languages.

## 4. How does matching work in SEA-GULL?

At the moment, SEA-GULL only supports exact matching. Fuzzy matching is much more fragile and
difficult to tune especially with the diversity of writing systems in Southeast Asian
languages.

- Matching ignores case (unless `case_sensitive`), Unicode presentation differences
  (e.g. full-width Latin characters, ligatures etc.), zero-width characters, soft hyphens and extra whitespace.
- Forms in spaced scripts (e.g. Latin-based, Tamil) must be whole words: "PM" doesn't match inside
  "development". A Chinese, Thai or Khmer character next to a Latin form doesn't count as part of
  its word, so "UN" matches in "他在UN会议上". A form mixing scripts ("MOE教育部") is checked at each
  end by that end's script.
- Forms in Chinese, Thai, Lao, Khmer and Burmese (scripts that do not use spaces between words) match
  inside running text but will not match if it splits a syllable. Vowels, tone marks, Khmer/Burmese stackers
  (for typing subscript consonants) are taken into account for this. However, in languages like Thai/Lao, it can
  be difficult to determine if a syllable's final consonant belongs to that syllable or is actually the first
  consonant for the next syllable, so some inaccuracies may occur in detecting such syllable boundaries.
  (e.g. "กา" inside "การ").
- When matches overlap, the longer one wins, then the leftmost.


## 5. Limitations

While developing SEA-GULL, certain limitations were discovered, but we intend to tackle them in future works.

| Limitation | Current solution | Future work |
|---|---|---|
| **Misspellings are not found** unless listed in the glossary | List common misspellings and spelling variants as `match_only` forms. | Revisit fuzzy matching and its interaction with various SEA writing scripts. |
| **Inflected or affixed words are not found.** (Important for languages like Tamil and Malay/Indonesian) | List common inflected forms as `match_only` forms. | Look into stemming and lemmatization. |
| **Tamil (and other Indic) vowel signs don't count as part of a word**, so a short Tamil form can match inside a longer word: "கல" is found inside "கலை". | Mostly affects very short forms. | Implement custom rule to handle Indic vowel signs. |
| **Thai/Lao/Khmer syllable boundary detection** | Usually only a concern with shorter forms. | (Dictionary-based) word segmentation (e.g. PyThaiNLP for Thai, khmer-nltk for Khmer) |
| **Thai/Lao tone/vowel marks typed in a different order** from the glossary don't match. | Usually the standard order (vowel, then tone mark) is followed, so impact should not be too great. | Implement custom normalization. |
| **A short form inside a longer name the glossary doesn't know is matched on its own.** If "Korea" is a form of South Korea and North Korea isn't in the glossary, the "Korea" in "North Korea" is reported as South Korea. | Add the longer names as concepts (they need no translation to block the shorter match), or leave out ambiguous short forms. | A glossary review tool that lists short and single-word forms for curators to check. |
| **Forms that ignore case and are also everyday words or phrases match ordinary text**: a building called "THE LEGACY" matches "the legacy". | Mark such forms `case_sensitive`. | Warn at load time about case-insensitive forms that are ordinary words or phrases, checked against a word list. |

## License

The code for SEA-GULL is licensed under the MIT license.

## Contact

If you have any questions or would like to contribute, please reach out to sealion@aisingapore.org