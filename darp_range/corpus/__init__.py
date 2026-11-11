"""Компонент `corpus`: размеченный корпус атак по трём осям FR-1.21 (механизм, цель,
доставка) и сопоставимых чистых контролей FR-1.22 с ground truth, происхождением и
хешем; тексты — «dirty English» (NFR-2.7), данные синтетические (NFR-2.13).

Только стандартная библиотека. CLI: `python3 -m darp_range.corpus --help`.
"""

from .bind import UNIVERSAL_FORBIDDEN, BoundEpisode, bind, pick_accounts
from .dirty import MARKER_NAMES, dirty_markers
from .entries import authored_entries
from .schema import (
    CONTROL_TYPES, DELIVERIES, GOALS, MECHANISMS, PREDICATES, CorpusError, coverage,
    dirty_rates, entry_texts, validate_corpus, validate_entry, volume,
)
from .store import CORPUS_FILE, DEFAULT_DIR, MANIFEST_FILE, freeze, is_fresh, load_corpus

__all__ = [
    "UNIVERSAL_FORBIDDEN", "BoundEpisode", "bind", "pick_accounts", "MARKER_NAMES",
    "dirty_markers", "authored_entries", "CONTROL_TYPES", "DELIVERIES", "GOALS", "MECHANISMS",
    "PREDICATES", "CorpusError", "coverage", "dirty_rates", "entry_texts", "validate_corpus",
    "validate_entry", "volume", "CORPUS_FILE", "DEFAULT_DIR", "MANIFEST_FILE", "freeze",
    "is_fresh", "load_corpus",
]
