"""Компонент `repro`: воспроизводимость — seed, манифест прогона, хеши корпуса и
конфигураций, закреплённые версии моделей (NFR-2.2); помощники сравнения траекторий
для фиксации дефектов воспроизводимости (NFR-2.3, используются оркестратором).

Только стандартная библиотека. CLI: `python3 -m darp_range.repro --help`.
"""

from .hashing import HashingError, canonical_json, file_hashes, hash_obj, hash_tree, sha256_file
from .manifest import (
    Drift, ModelSpec, ReproError, build_manifest, compare_trajectories, ensure_reproducible,
    load_manifest, manifest_fingerprint, rebuild_like, reproducibility_errors, stub_model_spec,
    trajectory_fingerprint, verify_manifest, write_manifest,
)
from .seeds import SeedError, check_seed, derive_seed, make_rng

__all__ = [
    "HashingError", "canonical_json", "file_hashes", "hash_obj", "hash_tree", "sha256_file",
    "Drift", "ModelSpec", "ReproError", "build_manifest", "compare_trajectories",
    "ensure_reproducible", "load_manifest", "manifest_fingerprint", "rebuild_like",
    "reproducibility_errors", "stub_model_spec", "trajectory_fingerprint", "verify_manifest",
    "write_manifest", "SeedError", "check_seed", "derive_seed", "make_rng",
]
