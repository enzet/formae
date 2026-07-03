"""Scale test: run `FormsExtractor` over sampled pages for whole languages.

Usage:
    python -m formae.benchmark --database data/wikt.db --sample 400 \
        --seed 42

Phase 1 scans all mainspace page bodies once for inflection-template usage
(per-language prefix sets from `formae.enrich`). Phase 2 samples N pages
per language and extracts every matching call, recording tier, errors, and
timing. Failures are written to `data/benchmark_failures.jsonl` for analysis.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sqlite3
import sys
import time

from .enrich import INFLECTION_PREFIXES
from .extractor import FormsExtractor


def scan_pages(database_path: str) -> dict[str, list[str]]:
    """Map each language to the mainspace pages using its templates."""
    patterns: dict[str, re.Pattern] = {
        language: re.compile(
            "|".join(r"\{\{\s*" + re.escape(prefix) for prefix in prefixes)
        )
        for language, prefixes in INFLECTION_PREFIXES.items()
    }
    pages: dict[str, list[str]] = {language: [] for language in patterns}
    database = sqlite3.connect(database_path)
    cursor = database.execute(
        "SELECT title, body FROM pages WHERE namespace_id=0"
        " AND body IS NOT NULL"
    )
    scanned = 0
    for title, body in cursor:
        scanned += 1
        if scanned % 2_000_000 == 0:
            print(f"  scanned {scanned / 1e6:.0f}M pages...", file=sys.stderr)
        if "{{" not in body:
            continue
        for language, pattern in patterns.items():
            if pattern.search(body):
                pages[language].append(title)
    database.close()
    return pages


def main() -> int:
    """Run the benchmark command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", "--db", required=True)
    parser.add_argument("--sample", type=int, default=400)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--failures", default="data/benchmark_failures.jsonl")
    parsed_arguments: argparse.Namespace = parser.parse_args()

    start_time: float = time.time()
    pages: dict[str, list[str]] = scan_pages(parsed_arguments.database)
    print(
        f"scan done in {time.time() - start_time:.0f}s: "
        + ", ".join(
            f"{language}={len(titles)}" for language, titles in pages.items()
        ),
        file=sys.stderr,
    )

    generator = random.Random(parsed_arguments.seed)
    extractor = FormsExtractor(parsed_arguments.database)
    failures = open(parsed_arguments.failures, "w", encoding="utf-8")
    statistics: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    durations: dict[str, float] = collections.defaultdict(float)

    for language, titles in pages.items():
        sample: list[str] = generator.sample(
            titles, min(parsed_arguments.sample, len(titles))
        )
        language_start: float = time.time()
        for title in sample:
            calls = extractor.find_inflection_calls(
                title, INFLECTION_PREFIXES[language]
            )
            if not calls:
                statistics[language]["no_calls_found"] += 1
                continue
            for template, arguments in calls:
                try:
                    result = extractor.extract_call(title, template, arguments)
                    statistics[language][result.tier] += 1
                    if result.tier == "none":
                        failures.write(
                            json.dumps(
                                {
                                    "language": language,
                                    "page": title,
                                    "template": template,
                                    "arguments": arguments,
                                    "error": result.error,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                except Exception as error:  # noqa: BLE001 — must not die.
                    statistics[language]["exception"] += 1
                    failures.write(
                        json.dumps(
                            {
                                "language": language,
                                "page": title,
                                "template": template,
                                "arguments": arguments,
                                "exception": repr(error)[:300],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        durations[language] = time.time() - language_start

    print("\n=== results ===")
    for language, counter in statistics.items():
        call_count: int = sum(
            value for key, value in counter.items() if key != "no_calls_found"
        )
        rate: float = (
            call_count / durations[language] if durations[language] else 0
        )
        print(
            f"{language}: {dict(counter)}  ({call_count} calls, "
            f"{durations[language]:.0f}s, {rate:.1f} calls/s)"
        )
    failures.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
