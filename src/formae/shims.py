"""Tier-3 shims: session-local patches that upgrade Lua modules to tier 1.

A shim rewrites a module's source so that, at the moment its internal forms
data exists (computed, not yet rendered to HTML), it returns that data as JSON
when the template call carries `json=1` — i.e. it retrofits the new-style
`json` parameter onto modules that never had it. After shimming, the normal
tier-1 probe in `formae.extractor` handles the family unchanged.

Shims are applied via `Wtp.add_page` into the working database. Contrary to
an earlier assumption, these writes DO persist to the database file (a
derived artifact, rebuildable from the dump), so application is guarded by
the `formae shim` marker: a module already carrying a marker is skipped,
keeping repeated sessions from stacking guards. The legacy `wiktparser shim`
marker (from before the project was renamed) is honored too, since it is
already persisted in existing database files.

Most modules follow one pattern — build a `data` table, then
`return make_table(data)` — so most shims are instances of
`_patch_before_render`. Modules that validate arguments through
`Module:parameters.process` additionally need `json` registered in their
parameter specification, or validation rejects the call before the guard runs.

Known families still lacking shims (each needs bespoke work): `grc-*`
(seven interlocking modules), `sa-*`, `he-verb`, `ar-nominals` (guard site is
far from the argument scope), `nl-adjectives` (per-inflection parameter
specifications).

Each shim is a `(module_name, patch_function)` pair where the patch function
maps original Lua source to patched source, raising `ValueError` if its anchor
is not found (e.g. after an upstream rewrite) so failures are loud.
"""

from __future__ import annotations

import re
from collections.abc import Callable

MODULE_NAMESPACE = 828

RETURN_ANCHOR = re.compile(
    r"^(\s*)return .*make_table\((\w+)[,)]", re.MULTILINE
)


def _patch_before_render(
    body: str,
    serialize_expression: str,
    parameters_anchor: str | None = None,
) -> str:
    """Insert a `json` guard immediately before the render call.

    Finds the unique `return ... make_table(...)` line and inserts, with the
    same indentation, a branch that returns `serialize_expression` as JSON
    when the template call carries `json`. If `parameters_anchor` is given,
    also registers `json` as a boolean in that parameter-specification table
    so `Module:parameters.process` accepts the extra argument.
    """
    matches = list(RETURN_ANCHOR.finditer(body))
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one render anchor, found {len(matches)}; "
            "module layout changed upstream"
        )
    match = matches[0]
    indentation = match.group(1)
    guard = (
        f"{indentation}-- formae shim: return forms as data.\n"
        f'{indentation}if args["json"] then\n'
        f'{indentation}\treturn require("Module:JSON").toJSON('
        f"{serialize_expression})\n"
        f"{indentation}end\n"
    )
    body = body[: match.start()] + guard + body[match.start() :]

    if parameters_anchor is not None:
        if body.count(parameters_anchor) != 1:
            raise ValueError(
                "parameter-specification anchor not found or ambiguous; "
                "module layout changed upstream"
            )
        body = body.replace(
            parameters_anchor,
            parameters_anchor + '\n\t\t["json"] = {type = "boolean"},',
        )
    return body


def _patch_fi_nominals(body: str) -> str:
    """Add a `json` branch to `Module:fi-nominals` `export.show`.

    This module renders several tables (possessive forms included), so the
    generic single-render-anchor patch does not apply; the guard goes right
    after `do_inflection_internal` fills `data.forms`.
    """
    anchor = "\n\tdo_inflection_internal(data, argobj)\n"
    if body.count(anchor) != 1:
        raise ValueError(
            "fi-nominals shim anchor not found or ambiguous; "
            "module layout changed upstream"
        )
    inject = (
        "\n\tdo_inflection_internal(data, argobj)\n"
        "\n"
        "\t-- formae shim: return forms as data instead of rendering.\n"
        '\tif args["json"] then\n'
        '\t\treturn require("Module:JSON").toJSON({\n'
        "\t\t\tforms = data.forms, title = data.title,\n"
        "\t\t\tcategories = data.categories, vh = data.vh,\n"
        "\t\t})\n"
        "\tend\n"
    )
    return body.replace(anchor, inject)


def _patch_fi_verbs(body: str) -> str:
    """Add a `json` branch to `Module:fi-verbs` `export.show`.

    Anchor: the `postprocess` call in `show`, after which `data.forms` is
    complete and rendering has not started.
    """
    anchor = "\n\tpostprocess(args, data)\n"
    if body.count(anchor) != 1:
        raise ValueError(
            "fi-verbs shim anchor not found or ambiguous; "
            "module layout changed upstream"
        )
    inject = (
        "\n\tpostprocess(args, data)\n"
        "\n"
        "\t-- formae shim: return forms as data instead of rendering.\n"
        '\tif args["json"] then\n'
        '\t\treturn require("Module:JSON").toJSON({\n'
        "\t\t\tforms = data.forms, title = data.title,\n"
        "\t\t\tcategories = data.categories,\n"
        "\t\t})\n"
        "\tend\n"
    )
    return body.replace(anchor, inject)


_DATA_EXPRESSION = (
    "{forms = data.forms, title = data.title, categories = data.categories}"
)

SHIMS: dict[str, Callable[[str], str]] = {
    "fi-nominals": _patch_fi_nominals,
    "fi-verbs": _patch_fi_verbs,
    "sv-adjectives": lambda body: _patch_before_render(body, _DATA_EXPRESSION),
    "sv-verbs": lambda body: _patch_before_render(body, _DATA_EXPRESSION),
    "sv-nouns": lambda body: _patch_before_render(
        body, _DATA_EXPRESSION, parameters_anchor="local params = {"
    ),
    "nl-verbs": lambda body: _patch_before_render(
        body, _DATA_EXPRESSION, parameters_anchor="local params = {"
    ),
    # In `Module:he-noun` the whole table passed to the renderer is the forms
    # structure itself.
    "he-noun": lambda body: _patch_before_render(body, "{forms = forms}"),
}


def apply_shims(processor) -> list[str]:
    """Apply all registered shims to a `Wtp` session; return applied names.

    Must run before any expansion so neither the Python LRU page cache nor
    the Lua sandbox's loaded-module cache holds the original.
    """
    applied: list[str] = []
    for module_name, patch in SHIMS.items():
        title = f"Module:{module_name}"
        page = processor.get_page(title, MODULE_NAMESPACE)
        if page is None or not page.body:
            continue
        if "formae shim" in page.body or "wiktparser shim" in page.body:
            # Already patched in a previous session (writes persist in the
            # database file); do not stack another guard. The second marker
            # is the project's pre-rename legacy.
            applied.append(module_name)
            continue
        processor.add_page(
            title, MODULE_NAMESPACE, body=patch(page.body), model="Scribunto"
        )
        applied.append(module_name)
    if applied:
        processor.get_page.cache_clear()
    return applied
