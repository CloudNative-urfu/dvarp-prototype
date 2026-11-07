"""Манифест прогона: seed, хеши корпуса/конфигураций/кода, закреплённые версии моделей (NFR-2.2).

Манифест фиксирует начальные условия прогона. Его отпечаток (`fingerprint`) считается по
всему, что определяет траекторию (seed, режим, корпус, конфигурации, код, модели), и не
зависит от времени создания и платформы — их различие выдаётся как предупреждение.
`verify_manifest` сравнивает записанный манифест с текущим состоянием рабочего каталога и
возвращает список расхождений; повтор прогона разрешён только при пустом списке ошибок.

Вспомогательные функции траекторий (`trajectory_fingerprint`, `compare_trajectories`)
используются оркестратором для фиксации дефектов воспроизводимости (NFR-2.3).
"""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .hashing import file_hashes, hash_obj, hash_tree
from .seeds import check_seed, derive_seed

SCHEMA = "darp.run_manifest/1"
MODEL_KINDS = ("stub", "openai_compatible", "local_weights")
# Плавающие ссылки на версию: с ними прогон не воспроизводим.
FLOATING_VERSIONS = frozenset({"", "latest", "main", "master", "head", "default", "*", "any"})
# Поля, исключаемые из отпечатка манифеста.
NON_FINGERPRINT_KEYS = ("created_at", "environment", "fingerprint", "notes")
# Изменчивые поля шага траектории: время и замеры latency не входят в сравнение (NFR-2.3).
VOLATILE_KEYS = frozenset(
    {"ts", "time", "created_at", "wall_ms", "wall_s", "latency_ms", "cpu_ms", "cpu_s", "duration_ms"}
)


class ReproError(ValueError):
    """Прогон нельзя считать детерминированным: не закреплён seed, версия модели или хеш."""


@dataclass(frozen=True)
class ModelSpec:
    """Закреплённая модель прогона.

    role: agent | attacker | detector:<plugin>; kind: stub | openai_compatible | local_weights.
    version: точная ревизия весов/коммит/хеш профиля заглушки; плавающие ссылки запрещены.
    pin: чем закреплена версия — weights_sha256 | revision | served_model_id | stub_profile_sha256.
    params: параметры генерации, влияющие на траекторию (temperature, seed, max_tokens).
    """

    role: str
    name: str
    version: str
    kind: str
    pin: str
    params: dict = field(default_factory=dict)
    params_b: float | None = None

    def validate(self) -> list[str]:
        errs: list[str] = []
        tag = f"model[{self.role}]"
        if not self.role:
            errs.append(f"{tag}: пустая роль")
        if not self.name:
            errs.append(f"{tag}: пустое имя модели")
        if self.kind not in MODEL_KINDS:
            errs.append(f"{tag}: неизвестный kind {self.kind!r}")
        if self.version.strip().lower() in FLOATING_VERSIONS:
            errs.append(f"{tag}: версия не закреплена ({self.version!r})")
        if not self.pin:
            errs.append(f"{tag}: не указан способ закрепления версии (pin)")
        if self.kind != "stub":
            # Живая генерация воспроизводима только при фиксированных temperature и seed.
            if "temperature" not in self.params:
                errs.append(f"{tag}: не зафиксирован temperature")
            if "seed" not in self.params:
                errs.append(f"{tag}: не зафиксирован seed запроса")
            else:
                try:
                    check_seed(self.params["seed"])
                except ValueError as e:
                    errs.append(f"{tag}: {e}")
        return errs

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ModelSpec":
        return cls(
            role=d["role"], name=d["name"], version=d["version"], kind=d["kind"],
            pin=d.get("pin", ""), params=dict(d.get("params", {})), params_b=d.get("params_b"),
        )


def stub_model_spec(role: str, name: str, profile: Mapping[str, Any]) -> ModelSpec:
    """Спецификация детерминированной заглушки: версия = хеш её профиля поведения."""
    return ModelSpec(role=role, name=name, version=hash_obj(dict(profile)), kind="stub",
                     pin="stub_profile_sha256", params={"temperature": 0})


def environment_info() -> dict:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sys.platform,
        "sqlite": sqlite3.sqlite_version,
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def build_manifest(
    *,
    run_id: str,
    master_seed: int,
    root: str | Path,
    corpus: str | Path,
    configs: Mapping[str, str | Path] | None = None,
    models: Iterable[ModelSpec] = (),
    code: str | Path | None = "darp_range",
    research_mode: bool = False,
    seed_labels: Iterable[Iterable[str]] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """Собирает манифест; пути корпуса/конфигураций/кода — относительно root.

    seed_labels: пути меток, для которых в манифест заранее выписываются выведенные seed'ы
    (например, [("episode", "atk-001")]) — удобно для ручной проверки и сверки с журналом.
    """
    root = Path(root)
    try:
        check_seed(master_seed)
    except ValueError as e:
        raise ReproError(str(e)) from None
    corpus_path = root / corpus
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "research_mode": bool(research_mode),
        "master_seed": master_seed,
        "derived_seeds": {
            "/".join(lbl): derive_seed(master_seed, *lbl) for lbl in (tuple(x) for x in seed_labels)
        },
        "corpus": {
            "path": _rel(corpus_path, root),
            "sha256": hash_tree(corpus_path),
            "files": file_hashes(corpus_path),
        },
        "configs": {
            name: {"path": _rel(root / p, root), "sha256": hash_tree(root / p)}
            for name, p in sorted((configs or {}).items())
        },
        "models": [m.to_dict() for m in sorted(models, key=lambda m: m.role)],
        "code": None if code is None else {"path": str(code), "sha256": hash_tree(root / code)},
        "extra": dict(extra or {}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": environment_info(),
    }
    ensure_reproducible(manifest)
    manifest["fingerprint"] = manifest_fingerprint(manifest)
    return manifest


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    return hash_obj({k: v for k, v in manifest.items() if k not in NON_FINGERPRINT_KEYS})


def reproducibility_errors(manifest: Mapping[str, Any]) -> list[str]:
    """Проверка NFR-2.2: seed, хеш корпуса и закреплённые версии всех моделей."""
    errs: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errs.append(f"неизвестная схема манифеста {manifest.get('schema')!r}")
    try:
        check_seed(manifest.get("master_seed"))
    except ValueError as e:
        errs.append(str(e))
    corpus = manifest.get("corpus") or {}
    if not corpus.get("sha256") or not corpus.get("files"):
        errs.append("не зафиксирован хеш корпуса (пустой или отсутствующий корпус)")
    models = manifest.get("models") or []
    if not models:
        errs.append("в манифесте нет ни одной модели")
    roles = [m.get("role") for m in models]
    if len(roles) != len(set(roles)):
        errs.append("роли моделей повторяются")
    for m in models:
        try:
            errs.extend(ModelSpec.from_dict(m).validate())
        except KeyError as e:
            errs.append(f"модель без поля {e}")
    if "fingerprint" in manifest and manifest["fingerprint"] != manifest_fingerprint(manifest):
        errs.append("отпечаток манифеста не совпадает с содержимым (манифест изменён вручную)")
    return errs


def ensure_reproducible(manifest: Mapping[str, Any]) -> None:
    errs = reproducibility_errors(manifest)
    if errs:
        raise ReproError("; ".join(errs))


@dataclass(frozen=True)
class Drift:
    field: str
    recorded: Any
    current: Any
    severity: str  # error | warning


def _diff_files(recorded: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    names = sorted(set(recorded) | set(current))
    return [n for n in names if recorded.get(n) != current.get(n)]


def verify_manifest(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> list[Drift]:
    """Расхождения между манифестом исходного прогона и манифестом повтора."""
    out: list[Drift] = []
    for key in ("schema", "master_seed", "research_mode"):
        if recorded.get(key) != current.get(key):
            out.append(Drift(key, recorded.get(key), current.get(key), "error"))
    rc, cc = recorded.get("corpus") or {}, current.get("corpus") or {}
    if rc.get("sha256") != cc.get("sha256"):
        out.append(Drift("corpus", rc.get("sha256"), cc.get("sha256"), "error"))
        for name in _diff_files(rc.get("files", {}), cc.get("files", {})):
            out.append(Drift(f"corpus.files.{name}", rc.get("files", {}).get(name),
                             cc.get("files", {}).get(name), "error"))
    rcf, ccf = recorded.get("configs") or {}, current.get("configs") or {}
    for name in sorted(set(rcf) | set(ccf)):
        a, b = (rcf.get(name) or {}).get("sha256"), (ccf.get(name) or {}).get("sha256")
        if a != b:
            out.append(Drift(f"configs.{name}", a, b, "error"))
    rm = {m["role"]: m for m in recorded.get("models") or []}
    cm = {m["role"]: m for m in current.get("models") or []}
    for role in sorted(set(rm) | set(cm)):
        if rm.get(role) != cm.get(role):
            out.append(Drift(f"models.{role}", rm.get(role), cm.get(role), "error"))
    a = (recorded.get("code") or {}).get("sha256")
    b = (current.get("code") or {}).get("sha256")
    if a != b:
        out.append(Drift("code", a, b, "error"))
    if recorded.get("extra") != current.get("extra"):
        out.append(Drift("extra", recorded.get("extra"), current.get("extra"), "error"))
    re_, ce = recorded.get("environment") or {}, current.get("environment") or {}
    for k in sorted(set(re_) | set(ce)):
        if re_.get(k) != ce.get(k):
            out.append(Drift(f"environment.{k}", re_.get(k), ce.get(k), "warning"))
    return out


def rebuild_like(recorded: Mapping[str, Any], root: str | Path) -> dict:
    """Пересобирает манифест по текущим файлам с теми же путями, seed'ом и моделями."""
    code = recorded.get("code")
    return build_manifest(
        run_id=recorded["run_id"],
        master_seed=recorded["master_seed"],
        root=root,
        corpus=recorded["corpus"]["path"],
        configs={k: v["path"] for k, v in (recorded.get("configs") or {}).items()},
        models=[ModelSpec.from_dict(m) for m in recorded.get("models") or []],
        code=None if code is None else code["path"],
        research_mode=recorded.get("research_mode", False),
        seed_labels=[tuple(k.split("/")) for k in recorded.get("derived_seeds", {})],
        extra=recorded.get("extra"),
    )


def write_manifest(manifest: Mapping[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def load_manifest(path: str | Path) -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    ensure_reproducible(manifest)
    return manifest


# --- Траектории (NFR-2.3) ---------------------------------------------------------------

def strip_volatile(obj: Any, volatile: frozenset[str] = VOLATILE_KEYS) -> Any:
    if isinstance(obj, dict):
        return {k: strip_volatile(v, volatile) for k, v in obj.items() if k not in volatile}
    if isinstance(obj, (list, tuple)):
        return [strip_volatile(v, volatile) for v in obj]
    return obj


def trajectory_fingerprint(steps: Iterable[Any]) -> str:
    """Хеш упорядоченной траектории без временных меток и замеров latency."""
    return hash_obj([strip_volatile(s) for s in steps])


def compare_trajectories(a: list[Any], b: list[Any], *, episode_id: str = "") -> dict | None:
    """None, если траектории совпали; иначе запись дефекта воспроизводимости."""
    ha, hb = trajectory_fingerprint(a), trajectory_fingerprint(b)
    if ha == hb:
        return None
    sa, sb = [strip_volatile(s) for s in a], [strip_volatile(s) for s in b]
    first = next((i for i, (x, y) in enumerate(zip(sa, sb)) if hash_obj(x) != hash_obj(y)),
                 min(len(sa), len(sb)))
    return {
        "kind": "reproducibility_defect",
        "episode_id": episode_id,
        "first_divergence": first,
        "len_a": len(sa),
        "len_b": len(sb),
        "step_a": sa[first] if first < len(sa) else None,
        "step_b": sb[first] if first < len(sb) else None,
        "sha256_a": ha,
        "sha256_b": hb,
    }
