"""Document ingestion: source files -> Claude content blocks + verification text."""

from asm_review.ingest.loader import LoadedSources, load_sources

__all__ = ["LoadedSources", "load_sources"]
