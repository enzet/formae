"""Survey inflection-template families and classify each by extraction tier.

Usage:
    python -m formae.survey --database data/wikt.db --out data/survey.json

Reads the wikt.db sqlite directly (bulk scan; no Lua needed). For every
Template whose name looks inflection-related, finds the module it `#invoke`s,
then classifies the module:

- tier "json": module registers a `json` parameter (new-style, full data
  structure returned natively);
- tier "botapi": module exports a `generate_*forms*` / `generate_args` bot
  entry point;
- tier "shim": neither channel; needs a session-local patch (tier 3).

Output: JSON mapping from module to `{tier, functions, templates}` plus a
per-language summary printed to standard error.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys

# Template names that indicate an inflection/conjugation/declension table.
INFLECTION_NAME_PATTERN = re.compile(
    r"""^[a-z]{2,3}(-[a-z]{2,4})?-   # Language-code prefix: la-, ru-, grc-.
        .*
        (decl|conj|infl|noun-table|verb-table|adecl|ndecl|mut(ation)?$)
    """,
    re.VERBOSE,
)
INVOKE_PATTERN = re.compile(r"#invoke:\s*([^|{}\n]+?)\s*\|")
# Utility modules templates commonly invoke before the real inflection
# module.
UTILITY_MODULE_PATTERN = re.compile(
    r"^(checkparams|string|ustring|parameters|template parser|documentation|"
    r"TemplateStyles|math|yesno|IPA|audio)",
    re.IGNORECASE,
)
EXPORT_PATTERN = re.compile(r"function\s+export\.([A-Za-z_0-9]+)")
# New-style modules register a boolean `json` template parameter.
JSON_PARAMETER_PATTERN = re.compile(
    r"""\bjson\b\s*=\s*\{[^}]*boolean|\["json"\]|\bargs\.json\b"""
)
BOT_API_PATTERN = re.compile(r"^generate_.*(forms?|args)", re.ASCII)


def main() -> int:
    """Run the survey command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", "--db", required=True)
    parser.add_argument("--out", default="data/survey.json")
    parsed_arguments: argparse.Namespace = parser.parse_args()

    database = sqlite3.connect(parsed_arguments.database)

    # Step 1: map inflection-looking templates to the module each invokes.
    template_module: dict[str, str] = {}
    for title, body, redirect in database.execute(
        "SELECT title, body, redirect_to FROM pages WHERE namespace_id=10"
    ):
        name: str = title.removeprefix("Template:")
        if not INFLECTION_NAME_PATTERN.match(name) or redirect:
            continue
        invoked: list[str] = [
            module.strip() for module in INVOKE_PATTERN.findall(body or "")
        ]
        invoked = [
            module
            for module in invoked
            if not UTILITY_MODULE_PATTERN.match(module)
        ]
        if not invoked:
            continue
        # Prefer a module sharing the template's language prefix, else the
        # first invoked module.
        language_prefix: str = name.split("-", 1)[0] + "-"
        preferred: list[str] = [
            module for module in invoked if module.startswith(language_prefix)
        ]
        template_module[name] = (preferred or invoked)[0]

    # Step 2: classify each referenced module.
    modules: dict[str, dict] = {}
    for module in sorted(set(template_module.values())):
        row = database.execute(
            "SELECT body FROM pages WHERE namespace_id=828 AND title=?",
            (f"Module:{module}",),
        ).fetchone()
        body: str = row[0] if row and row[0] else ""
        exports: list[str] = EXPORT_PATTERN.findall(body)
        bot_functions: list[str] = [
            function for function in exports if BOT_API_PATTERN.match(function)
        ]
        if JSON_PARAMETER_PATTERN.search(body):
            tier = "json"
        elif bot_functions:
            tier = "botapi"
        else:
            tier = "shim"
        modules[module] = {
            "tier": tier,
            "functions": bot_functions,
            "templates": sorted(
                template
                for template, invoked_module in template_module.items()
                if invoked_module == module
            ),
        }

    # Step 3: summarize per language code (template-name prefix).
    language_tiers: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for template, module in template_module.items():
        language: str = template.split("-", 1)[0]
        language_tiers[language][modules[module]["tier"]] += 1

    with open(parsed_arguments.out, "w", encoding="utf-8") as output_file:
        json.dump(
            modules, output_file, ensure_ascii=False, indent=1, sort_keys=True
        )

    total: collections.Counter = collections.Counter()
    for counts in language_tiers.values():
        total.update(counts)
    print(
        f"templates matched: {len(template_module)}  "
        f"modules: {len(modules)}  tiers: {dict(total)}",
        file=sys.stderr,
    )
    print(
        "\nworst-covered languages (most shim-only templates):",
        file=sys.stderr,
    )
    by_shim_count = sorted(
        language_tiers.items(), key=lambda item: -item[1].get("shim", 0)
    )
    for language, counts in by_shim_count[:15]:
        print(f"  {language:6} {dict(counts)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
