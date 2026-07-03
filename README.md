# Formae

**Formae** (Latin for "forms") is a parser for the **English Wiktionary** that
aims to extract *more* than [Kaikki](https://kaikki.org)
([wiktextract](https://github.com/tatuylonen/wiktextract)) publishes — with a
primary focus on **declension/inflection tables**.

Wiktionary's inflection tables are not stored as data: a page contains a
template like `{{la-ndecl|aqua<1>}}` that invokes a Lua (Scribunto) module which
*computes* every form at render time. To get the forms you must actually run the
MediaWiki template with Lua engine. The distinctive goal of this project is to
capture the inflection **data structure** at its Lua source — the internal
`forms` table whose slot keys (`nom_sg`, `gen_pl`, …) are clean, unambiguous
grammatical tags — instead of reverse-engineering the rendered HTML grid (which
is what wiktextract does, heuristically).

## Built on

This project stands on **two upstream repositories** by Tatu Ylönen, cloned
into `vendor/` and installed as editable packages. They are pinned to exact
verified commits in [vendor.lock](vendor.lock) — not to release tags, because
upstream tags/PyPI releases lag far behind live Wiktionary, whose module
ecosystem the code must match (the dump, the upstream commits, and our shims
must stay contemporaneous):

- **[tatuylonen/wikitextprocessor](https://github.com/tatuylonen/wikitextprocessor)** —
  the engine: dump reader, sqlite page store, template expander, and a
  reimplementation of MediaWiki's Scribunto Lua sandbox.  `src/formae/` imports
  this **as a library** at runtime.  It vendors one further repo as a git
  submodule (`mediawiki-extensions-Scribunto`, the Lua standard-library parts) —
  so it must be cloned with its submodule initialized, or every `#invoke` fails.
- **[tatuylonen/wiktextract](https://github.com/tatuylonen/wiktextract)** —
  the extraction layer on top (the code behind [Kaikki](https://kaikki.org)).
  Formae uses it **as a pipeline tool**, not a library: its `wiktwords` CLI
  builds the page database from the dump and produces the per-entry JSONL that
  `formae.enrich` consumes.

Everything else is ordinary PyPI dependencies (`lupa` for in-process Lua,
etc. — see [requirements.txt](requirements.txt)).

## How it works

Each inflection template is classified by how its forms can be obtained as data,
and extracted through one of three tiers:

1. **`json`** — new-style modules accept a `json=1` parameter and return their
   complete internal data structure as JSON.
2. **`botapi`** — older modules export bot-oriented entry points (e.g.
   `generate_forms`) that return `slot=form1,form2|…`.
3. **shim** — modules with no data channel get a session-local Lua patch that
   retrofits the `json=1` interface; one shim covers a whole module family (and
   often a whole language).

All three tiers are implemented and verified end-to-end: Latin (tier 1),
Russian (tier 2), Finnish (tier 3 — one shim covers all 308 `fi-decl-*`
templates). About a dozen more languages (Ukrainian, German, Hindi, Spanish,
Italian, Icelandic, …) already expose a data channel and are expected to work
as-is; most of the rest need only a small shim per module family.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (any recent version).

```sh
uv venv   # Creates .venv/.

# Clone the upstreams at the verified commits pinned in `vendor.lock`:
WTP_SHA=$(awk '/^wikitextprocessor/{print $2}' vendor.lock)
WXT_SHA=$(awk '/^wiktextract/{print $2}' vendor.lock)
git clone https://github.com/tatuylonen/wikitextprocessor.git vendor/wikitextprocessor
git -C vendor/wikitextprocessor checkout "$WTP_SHA"
git -C vendor/wikitextprocessor submodule update --init   # Scribunto libs — required for #invoke.
git clone https://github.com/tatuylonen/wiktextract.git vendor/wiktextract
git -C vendor/wiktextract checkout "$WXT_SHA"

uv pip install -e vendor/wikitextprocessor
uv pip install levenshtein nltk pydantic
uv pip install --no-deps -e vendor/wiktextract

# Fetch the enwiktionary dump snapshot pinned in vendor.lock (a dated dump,
# never the "latest" symlink — that one silently moves under you) and verify:
DUMP=$(awk '/^enwiktionary-dump/{print $2}' vendor.lock)
DUMP_SHA1=$(awk '/^enwiktionary-dump/{sub("sha1:", "", $3); print $3}' vendor.lock)
curl -o "data/enwiktionary-$DUMP-pages-articles.xml.bz2" \
  "https://dumps.wikimedia.org/enwiktionary/$DUMP/enwiktionary-$DUMP-pages-articles.xml.bz2"
echo "$DUMP_SHA1  data/enwiktionary-$DUMP-pages-articles.xml.bz2" | shasum -a 1 -c -
```

Note: dumps.wikimedia.org retains only the last few dated runs. If the pinned
snapshot has aged out there, fetch it from a
[mirror](https://dumps.wikimedia.org/mirrors.html) or
[archive.org](https://archive.org/details/wikimediadownloads).

## License

The code is licensed under the [MIT License](LICENSE), matching both upstream
repositories. (The Scribunto Lua submodule vendored inside wikitextprocessor
is GPL; it is fetched at setup time, not distributed with this repository.)

**Extracted data is a separate matter:** anything formae extracts is derived
from Wiktionary content, which is dual-licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) / GFDL — so
any published dataset of extracted forms must carry CC BY-SA 4.0 with
attribution to [Wiktionary](https://en.wiktionary.org/wiki/Wiktionary:Copyrights).
