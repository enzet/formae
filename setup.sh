#!/bin/sh
# Set up the development environment from the pins in vendor.lock:
#   1. create .venv (via uv),
#   2. clone/sync the vendored upstreams at their pinned commits,
#   3. install everything into .venv,
#   4. fetch and verify the pinned enwiktionary dump snapshot.
#
# Idempotent: safe to re-run, and re-running after a pin bump in vendor.lock
# syncs vendor/ and data/ to the new pins.
set -eu

cd "$(dirname "$0")"

command -v uv >/dev/null || {
    echo "Error: uv is required (https://docs.astral.sh/uv/)." >&2
    exit 1
}

WTP_SHA=$(awk '/^wikitextprocessor/{print $2}' vendor.lock)
WXT_SHA=$(awk '/^wiktextract/{print $2}' vendor.lock)
DUMP=$(awk '/^enwiktionary-dump/{print $2}' vendor.lock)
DUMP_SHA1=$(awk '/^enwiktionary-dump/{sub("sha1:", "", $3); print $3}' vendor.lock)

echo "Git hooks (.githooks)."
git config core.hooksPath .githooks

echo "Virtual environment (.venv)."
[ -d .venv ] || uv venv

echo "Upstream clones at pinned commits."
[ -d vendor/wikitextprocessor ] || git clone \
    https://github.com/tatuylonen/wikitextprocessor.git vendor/wikitextprocessor
git -C vendor/wikitextprocessor checkout --quiet "$WTP_SHA"
# Scribunto Lua libs — without the submodule every #invoke fails.
git -C vendor/wikitextprocessor submodule update --init --quiet

[ -d vendor/wiktextract ] || git clone \
    https://github.com/tatuylonen/wiktextract.git vendor/wiktextract
git -C vendor/wiktextract checkout --quiet "$WXT_SHA"

echo "Python packages."
uv pip install --quiet -e vendor/wikitextprocessor
uv pip install --quiet levenshtein nltk pydantic
# --no-deps keeps the resolver from re-fetching wikitextprocessor from Git
# over our editable install (its deps are installed just above).
uv pip install --quiet --no-deps -e vendor/wiktextract

echo "Dump snapshot $DUMP."
DUMP_FILE="data/enwiktionary-$DUMP-pages-articles.xml.bz2"
mkdir -p data
if [ ! -f "$DUMP_FILE" ]; then
    # dumps.wikimedia.org retains only the last few dated runs; if the pinned
    # snapshot has aged out, fetch it manually from a mirror
    # (https://dumps.wikimedia.org/mirrors.html) or
    # https://archive.org/details/wikimediadownloads and re-run.
    curl -o "$DUMP_FILE" \
        "https://dumps.wikimedia.org/enwiktionary/$DUMP/enwiktionary-$DUMP-pages-articles.xml.bz2"
fi
echo "$DUMP_SHA1  $DUMP_FILE" | shasum -a 1 -c -

echo "Done."
