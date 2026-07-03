"""Extract inflection forms from English Wiktionary.

Built on the `wiktextract/wikitextprocessor` stack. The distinctive feature is
capturing inflection data at the Lua source (slot-keyed forms like `nom_sg`,
`gen_pl`) instead of parsing rendered HTML tables.
"""

from .extractor import ExtractResult, FormsExtractor

__all__ = ["ExtractResult", "FormsExtractor"]
