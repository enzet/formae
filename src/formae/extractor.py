"""Tiered extraction of slot-keyed inflection forms.

Given an inflection-template call (name + arguments), obtain the forms as
data, never by parsing rendered HTML:

- Tier 1 (`json`): new-style modules (e.g. `Module:la-nominal`) accept a
  `json=1` template parameter and return their complete internal data
  structure as JSON. Shimmed modules (see `formae.shims`) join this tier.
- Tier 2 (`botapi`): older modules (e.g. `Module:ru-noun`) export
  bot-oriented entry points such as `generate_forms` that return
  `slot=form1,form2|slot=...`. These read `frame:getParent().args`, so the
  template's arguments must be supplied as the *parent frame* of the
  `#invoke`.
- Tier 3 (`none`): neither channel exists; the call is recorded so a shim can
  be written for that module family later.

The tier decision is probed once per template name and cached.
"""

from __future__ import annotations

import dataclasses
import json
import re

from wikitextprocessor import Wtp

from .shims import apply_shims

TEMPLATE_NAMESPACE = 10
MODULE_NAMESPACE = 828

# Bot-API entry points, in order of preference.
BOT_API_FUNCTIONS = [
    "generate_forms",
    "generate_noun_forms",
    "generate_verb_forms",
]

INVOKE_PATTERN = re.compile(r"#invoke:\s*([^|{}\n]+?)\s*\|\s*([A-Za-z_0-9]+)")
EXPORT_PATTERN = re.compile(r"function\s+export\.([A-Za-z_0-9]+)")
SLOT_LINE_PATTERN = re.compile(r"^[a-z0-9_]+=", re.ASCII)
TEMPLATE_CALL_PATTERN = re.compile(r"\{\{([^{}|]+)((?:\|[^{}]*)?)\}\}")
ARGUMENT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_ -]+")

# Cross-reference templates ("see the conjugation of X") that render a link,
# not an inflection table; extraction is skipped for them.
SKIP_TEMPLATE_PATTERN = re.compile(r"-see$|-verb-see$")


@dataclasses.dataclass
class ExtractResult:
    """Outcome of one template-call extraction."""

    # One of "json", "botapi", "skip" (cross-reference template, no table),
    # or "none".
    tier: str
    # Mapping from slot (e.g. `nom_sg`) to forms; empty when tier is "none".
    # Each form is a plain string, or a `{"form": ..., "tags": [...]}` mapping
    # for qualified variants.
    forms: dict[str, list]
    # Tier-1 only: the non-forms keys of the module's data structure
    # (accel, gender, title, and so on).
    extra: dict
    error: str | None = None


class FormsExtractor:
    """Extract slot-keyed inflection forms from template calls."""

    def __init__(self, database_path: str, language_code: str = "en") -> None:
        self.processor: Wtp = Wtp(
            db_path=database_path, lang_code=language_code
        )
        # Module patches (tier 3 to tier 1); idempotent, persisted into the
        # working database (a rebuildable derived artifact). Must precede
        # any expansion.
        self.shimmed: list[str] = apply_shims(self.processor)
        # Mapping from template name to the cached tier decision:
        # ("json",) | ("botapi", module, function) | ("none",).
        self._tier_cache: dict[str, tuple] = {}

    def extract_call(
        self,
        page_title: str,
        template: str,
        arguments: dict[str, str],
        unwrap_depth: int = 2,
    ) -> ExtractResult:
        """Extract forms for one template call made on `page_title`."""
        if SKIP_TEMPLATE_PATTERN.search(template):
            return ExtractResult(tier="skip", forms={}, extra={})

        self.processor.start_page(page_title)
        cached: tuple | None = self._tier_cache.get(template)

        if cached is None or cached[0] == "json":
            result = self._try_json(template, arguments)
            if result is not None:
                self._tier_cache[template] = ("json",)
                return result

        if cached is None or cached[0] == "botapi":
            plan = cached[1:] if cached else self._find_bot_api(template)
            if plan:
                module, function = plan
                result = self._try_bot_api(
                    template, module, function, arguments
                )
                if result is not None:
                    self._tier_cache[template] = ("botapi", module, function)
                    return result

        # Wrapper templates ({{sv-infl-noun-c-ar}} just calls
        # {{sv-decl-noun|...}}) never see our extra `json` argument. Expand
        # one level to substitute the wrapper's parameters, then recurse on
        # the inner call.
        if unwrap_depth > 0:
            result = self._try_unwrap(
                page_title, template, arguments, unwrap_depth
            )
            if result is not None:
                return result

        self._tier_cache[template] = ("none",)
        return ExtractResult(
            tier="none",
            forms={},
            extra={},
            error=f"no data channel found for {{{{{template}}}}}",
        )

    def _try_unwrap(
        self,
        page_title: str,
        template: str,
        arguments: dict[str, str],
        unwrap_depth: int,
    ) -> ExtractResult | None:
        """Capture the wrapper's inner template call and recurse on it.

        Uses `expand`'s `template_fn` hook, which receives each inner call
        with its arguments already substituted — so `{{sv-infl-noun-c-ar}}`
        yields the underlying `{{sv-decl-noun|...}}` call directly.
        """
        captured: list[tuple[str, dict[str, str]]] = []

        def capture(name: str, call_arguments: dict) -> str | None:
            # Only the first language-prefixed template is a candidate;
            # helpers like `{{pagename}}` must expand normally, since they
            # run during substitution of the candidate's own arguments.
            if captured or name == template or "-" not in name:
                return None
            captured.append(
                (
                    name,
                    {
                        str(key): str(value)
                        for key, value in call_arguments.items()
                    },
                )
            )
            # Short-circuit: the call is captured, no need to render it.
            return ""

        self.processor.expand(
            build_call(template, arguments), template_fn=capture
        )
        for inner_name, inner_arguments in captured:
            result = self.extract_call(
                page_title, inner_name, inner_arguments, unwrap_depth - 1
            )
            if result.tier not in ("none", "skip"):
                return result
        return None

    def _try_json(
        self, template: str, arguments: dict[str, str]
    ) -> ExtractResult | None:
        """Probe tier 1: call the template with an extra `json=1`."""
        call = build_call(template, {**arguments, "json": "1"})
        expanded: str = self.processor.expand(call)
        data = find_json_payload(expanded)
        if not isinstance(data, dict):
            return None
        raw_forms = data.get("forms", {})
        if isinstance(raw_forms, list):
            # Some modules (e.g. `Module:he-noun`) return a list of paradigm
            # groups, each a mapping with a discriminator key like
            # `number`; flatten to slots such as `s_3ms`, `p_c`.
            flattened: dict = {}
            for group_index, group in enumerate(raw_forms):
                if not isinstance(group, dict):
                    continue
                prefix = str(group.get("number", group_index))
                for key, value in group.items():
                    if key != "number":
                        flattened[f"{prefix}_{key}"] = value
            raw_forms = flattened
        forms = {
            slot: normalize_slot_values(values)
            for slot, values in raw_forms.items()
        }
        extra = {key: value for key, value in data.items() if key != "forms"}
        return ExtractResult(tier="json", forms=forms, extra=extra)

    def _find_bot_api(self, template: str) -> tuple[str, str] | None:
        """Locate the module a template invokes and its bot-API export.

        Templates often invoke utility modules (`checkparams`, ...) before
        the real inflection module, so every invoked module is tried in
        order.
        """
        template_page = self.processor.get_page(
            f"Template:{template}", TEMPLATE_NAMESPACE
        )
        if template_page is None or not template_page.body:
            return None
        for module, _function in INVOKE_PATTERN.findall(template_page.body):
            module = module.strip()
            module_page = self.processor.get_page(
                f"Module:{module}", MODULE_NAMESPACE
            )
            if module_page is None or not module_page.body:
                continue
            exports = set(EXPORT_PATTERN.findall(module_page.body))
            for function in BOT_API_FUNCTIONS:
                if function in exports:
                    return module, function
        return None

    def _try_bot_api(
        self,
        template: str,
        module: str,
        function: str,
        arguments: dict[str, str],
    ) -> ExtractResult | None:
        """Probe tier 2: `#invoke` the bot entry point with a parent frame."""
        # Positional arguments must use integer keys so Lua sees
        # `frame.args[1]`, `frame.args[2]`, and so on; with string keys the
        # module falls back to the (unaccented) page name and rejects it.
        frame_arguments = {
            int(key) if key.isdigit() else key: value
            for key, value in arguments.items()
        }
        expanded: str = self.processor.expand(
            f"{{{{#invoke:{module}|{function}}}}}",
            parent=(f"Template:{template}", frame_arguments),
        )
        if not SLOT_LINE_PATTERN.match(expanded):
            return None
        forms: dict[str, list] = {}
        for part in expanded.strip().split("|"):
            slot, _, value = part.partition("=")
            if slot and value:
                forms[slot] = value.split(",")
        return ExtractResult(tier="botapi", forms=forms, extra={})

    def find_inflection_calls(
        self, page_title: str, template_prefixes: tuple[str, ...]
    ) -> list[tuple[str, dict[str, str]]]:
        """Scan a page's wikitext for inflection-template calls.

        Fallback for entries where wiktextract did not record
        `inflection_templates` (it often only fills `head_templates`).
        Matching is by template-name prefix (e.g. `("ru-noun-table",)`).
        """
        page = self.processor.get_page(page_title, 0)
        if page is None or not page.body:
            return []
        calls: list[tuple[str, dict[str, str]]] = []
        for match in TEMPLATE_CALL_PATTERN.finditer(page.body):
            name = match.group(1).strip()
            if not name.startswith(template_prefixes):
                continue
            calls.append((name, parse_call_arguments(match.group(2))))
        return calls


def find_json_payload(text: str) -> dict | None:
    """Find and parse a JSON object embedded in an expansion result.

    Modules usually return bare JSON for `json=1`, but wrappers may prepend
    markup (e.g. a `checkparams` warning about the extra parameter), so the
    payload is searched for rather than assumed to start at offset zero.
    """
    decoder = json.JSONDecoder()
    start = 0
    while (start := text.find("{", start)) != -1:
        # Template braces (`{{`) are markup, not JSON.
        if text[start : start + 2] == "{{":
            start += 2
            continue
        try:
            data, _end = decoder.raw_decode(text, start)
        except ValueError:
            start += 1
            continue
        if isinstance(data, dict):
            return data
        start += 1
    return None


def parse_call_arguments(argument_text: str) -> dict[str, str]:
    """Parse the `|...` tail of a template call into an arguments mapping.

    Positional arguments get string keys "1", "2", and so on; named
    arguments keep their names.
    """
    arguments: dict[str, str] = {}
    position = 0
    for raw in split_template_arguments(argument_text)[1:]:
        key, separator, value = raw.partition("=")
        if separator and ARGUMENT_NAME_PATTERN.fullmatch(key.strip()):
            arguments[key.strip()] = value.strip()
        else:
            position += 1
            arguments[str(position)] = raw.strip()
    return arguments


def split_template_arguments(text: str) -> list[str]:
    """Split template-argument text on `|`, but not inside `[[...]]`.

    A naive split shreds piped wikilinks (`[[прошедший|проше́дшего]]`), which
    are routine in multi-word inflection calls.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        pair = text[index : index + 2]
        if pair == "[[":
            depth += 1
            current.append(pair)
            index += 2
        elif pair == "]]" and depth:
            depth -= 1
            current.append(pair)
            index += 2
        elif text[index] == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
        else:
            current.append(text[index])
            index += 1
    parts.append("".join(current))
    return parts


def normalize_slot_values(values: str | list | dict) -> list:
    """Normalize one slot's JSON value into a list of forms.

    Lua modules store slots as mixed tables: the array part holds plain
    forms; named keys hold qualified variants (e.g. Finnish `gen_pl`
    `{"1": "vesien", "rare": ["vetten"]}`). Plain forms stay strings;
    qualified ones become `{"form": ..., "tags": [qualifier]}`.
    """
    if isinstance(values, str):
        return [values]
    if isinstance(values, (int, float)):
        return [str(values)]
    if isinstance(values, list):
        return [
            value if isinstance(value, str) else value.get("form", "")
            for value in values
            if isinstance(value, (str, dict))
        ]
    if isinstance(values, dict):
        normalized: list = []
        for key in sorted((k for k in values if k.isdigit()), key=int):
            normalized.extend(normalize_slot_values(values[key]))
        for key in sorted(k for k in values if not k.isdigit()):
            normalized.extend(
                {"form": form, "tags": [key]}
                if isinstance(form, str)
                else form
                for form in normalize_slot_values(values[key])
            )
        return normalized
    return []


def build_call(template: str, arguments: dict[str, str]) -> str:
    """Reconstruct `{{template|1=...|key=value}}` wikitext from arguments.

    Positional arguments are emitted in numeric order with explicit `N=` so
    that values containing `=` cannot be misparsed.
    """

    def sort_key(item: tuple[str, str]) -> tuple[int, int | str]:
        key = item[0]
        return (0, int(key)) if key.isdigit() else (1, key)

    parts = [template]
    parts += [
        f"{key}={value}"
        for key, value in sorted(arguments.items(), key=sort_key)
    ]
    return "{{" + "|".join(parts) + "}}"
