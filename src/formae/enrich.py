"""Enrich wiktextract JSONL entries with slot-keyed inflection forms.

Usage:
    python -m formae.enrich --database data/wikt.db entries.jsonl

For each entry, inflection-template calls are taken from the entry's
`inflection_templates` when present; otherwise the page wikitext is scanned
for templates matching per-language prefixes (wiktextract frequently omits
`inflection_templates`, recording only `head_templates`).

Each entry gains an `inflection_data` list:
    {"template": name, "arguments": {...}, "tier": "json"|"botapi"|"none",
     "forms": {slot: [form, ...]}, ...}
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from .extractor import FormsExtractor

# Template-name prefixes that indicate an inflection table, per language
# code. Conservative seed set; extend as the family survey fills in (see
# `data/survey.json`).
INFLECTION_PREFIXES: dict[str, tuple[str, ...]] = {
    "la": ("la-ndecl", "la-adecl", "la-conj"),
    "ru": ("ru-noun-table", "ru-decl-adj", "ru-conj"),
    "fi": ("fi-decl", "fi-conj"),
    "sv": ("sv-infl", "sv-conj"),
    "nl": ("nl-conj",),
    "he": ("he-decl",),
}


def enrich_entry(extractor: FormsExtractor, entry: dict) -> dict:
    """Attach `inflection_data` to one wiktextract entry."""
    language_code: str = entry.get("lang_code", "")
    word: str = entry.get("word", "")
    calls: list[tuple[str, dict[str, str]]] = [
        (template["name"], template.get("args", {}))
        for template in entry.get("inflection_templates") or []
    ]
    if not calls and language_code in INFLECTION_PREFIXES:
        calls = extractor.find_inflection_calls(
            word, INFLECTION_PREFIXES[language_code]
        )

    results: list[dict] = []
    for name, arguments in calls:
        result = extractor.extract_call(word, name, arguments)
        item: dict = {
            "template": name,
            "arguments": arguments,
            "tier": result.tier,
        }
        if result.forms:
            item["forms"] = result.forms
        if result.error:
            item["error"] = result.error
        results.append(item)
    if results:
        entry["inflection_data"] = results
    return entry


def main() -> int:
    """Run the enrichment command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="wiktextract JSONL file")
    parser.add_argument(
        "--database", "--db", required=True, help="path to wikt.db"
    )
    parser.add_argument(
        "--out", default="-", help="output JSONL (default stdout)"
    )
    parsed_arguments: argparse.Namespace = parser.parse_args()

    extractor = FormsExtractor(parsed_arguments.database)
    output: TextIO = (
        sys.stdout
        if parsed_arguments.out == "-"
        else open(parsed_arguments.out, "w", encoding="utf-8")
    )
    statistics: dict[str, int] = {}
    with open(parsed_arguments.input, encoding="utf-8") as input_file:
        for line in input_file:
            entry = enrich_entry(extractor, json.loads(line))
            for item in entry.get("inflection_data", []):
                statistics[item["tier"]] = statistics.get(item["tier"], 0) + 1
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"tier statistics: {statistics}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
