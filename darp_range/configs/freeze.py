"""Заморозка конфигураций по хешу и контроль «защита не меняется на лету» (NFR-2.10, §5).

Протокол (как в Detection→Decision / RAGWarrant: всё, что влияет на решение, фиксируется до
итогового прогона, решение воспроизводимо из снимка данных, снимка профиля и версии кода):

1. `freeze(set, dir)` пишет каждую конфигурацию в `<dir>/<name>.json` (канонический JSON с
   вычисленными полями: разрешённые параметры плагинов, отпечатки, операционное определение)
   и `<dir>/configs.lock.json`: sha256 каждого файла, отпечаток набора, хеш кода защиты
   (пакеты гвардрейлов, политики, правил процесса, исполнителя, инструментов). Повторная заморозка
   в каталог с lock запрещена — lock только дополняется записями `post_hoc`.
2. `verify(dir)` сверяет файлы с lock и с текущим кодом: изменённый/пропавший файл, лишняя
   конфигурация без записи в lock, конфигурация, чьи вычисленные поля разошлись с кодом
   (например, сменились пороги плагина по умолчанию), и дрейф кода защиты.
3. `add_post_hoc(dir, cfg)` — конфигурация, построенная после блокировки: только роль
   `post_hoc`, отдельный раздел lock, в основной вывод не входит (§5).
4. `FreezeGuard` — проверка в прогоне: перед каждым эпизодом (обязательно в режиме `A`)
   конфигурация и реально собранные части защиты сверяются с lock; расхождение — исключение
   `FrozenConfigError`, прогон недействителен (адаптация защиты на лету запрещена).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from darp_range.repro import canonical_json, hash_obj, hash_tree, sha256_file

from .registry import ConfigSet
from .spec import ConfigError, ConfigSpec

LOCK_NAME = "configs.lock.json"
LOCK_SCHEMA = "darp-configs-lock-1"
# Код, от которого зависит поведение защиты при той же конфигурации (пути от корня пакета).
DEFENSE_CODE = ("configs", "guard_contract", "guard_plugins", "guard_policy", "executor", "tools")
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class FrozenConfigError(RuntimeError):
    """Конфигурация или собранная защита расходится с замороженной (NFR-2.10)."""


def defense_code_hashes(root: Path = PACKAGE_ROOT) -> dict[str, str]:
    return {d: hash_tree(root / d) for d in DEFENSE_CODE}


def _dump(obj: Any) -> str:
    # канонический порядок ключей + отступы: файл читаем и его sha256 стабилен
    return json.dumps(json.loads(canonical_json(obj)), ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n"


def _entry(cfg: ConfigSpec, path: Path) -> dict[str, Any]:
    return {"file": path.name, "sha256": sha256_file(path), "fingerprint": cfg.fingerprint(),
            "family": cfg.family, "role": cfg.role, "control_of": cfg.control_of}


def freeze(configs: ConfigSet, out_dir: str | Path, *, note: str = "",
           locked_at: str | None = None, replace: bool = False) -> dict[str, Any]:
    """Заморозить набор. `replace=True` — перезаморозка ДО итогового теста (например, после
    исправления кода защиты на стадии разработки): прежний lock не удаляется, а переносится в
    `history/` (история блокировок только дополняется), прежние файлы конфигураций удаляются."""
    if not isinstance(configs, ConfigSet):
        raise ConfigError("freeze: нужен ConfigSet")
    out = Path(out_dir)
    lock_path = out / LOCK_NAME
    if lock_path.exists():
        if not replace:
            raise FrozenConfigError(f"{lock_path} уже существует: набор заморожен; новые "
                                    f"конфигурации — только add_post_hoc, изменение существующих "
                                    f"запрещено (перезаморозка до итогового теста — replace=True)")
        old = load_lock(out)
        hist = out / "history"
        hist.mkdir(exist_ok=True)
        stamp = str(old.get("locked_at", "unknown")).replace(":", "")
        n = len(list(hist.glob("configs.lock.*.json")))
        lock_path.rename(hist / f"configs.lock.{n:03d}.{stamp}.json")
        for ent in {**old.get("configs", {}), **old.get("post_hoc", {})}.values():
            (out / ent["file"]).unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    entries = {}
    for cfg in configs:
        if cfg.role == "post_hoc":
            raise ConfigError(f"{cfg.name}: post_hoc не входит в предзарегистрированный набор")
        p = out / f"{cfg.name}.json"
        p.write_text(_dump(cfg.to_json()), encoding="utf-8")
        entries[cfg.name] = _entry(cfg, p)
    lock = {"schema": LOCK_SCHEMA, "preregistered": True,
            "locked_at": locked_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": note, "set_fingerprint": configs.fingerprint(),
            "configs": entries, "post_hoc": {}, "defense_code": defense_code_hashes()}
    lock["lock_fingerprint"] = hash_obj({k: v for k, v in lock.items() if k != "locked_at"})
    lock_path.write_text(_dump(lock), encoding="utf-8")
    return lock


def load_lock(dir_: str | Path) -> dict[str, Any]:
    p = Path(dir_) / LOCK_NAME
    if not p.is_file():
        raise FrozenConfigError(f"нет {p}: набор не заморожен")
    lock = json.loads(p.read_text(encoding="utf-8"))
    if lock.get("schema") != LOCK_SCHEMA:
        raise FrozenConfigError(f"{p}: schema {lock.get('schema')!r} ≠ {LOCK_SCHEMA}")
    return lock


@dataclass(frozen=True)
class Drift:
    kind: str        # file_changed | file_missing | unlisted | code_mismatch | defense_code | lock
    name: str
    detail: str


def verify(dir_: str | Path, *, check_code: bool = True) -> list[Drift]:
    d = Path(dir_)
    lock = load_lock(d)
    out: list[Drift] = []
    listed = {**lock.get("configs", {}), **lock.get("post_hoc", {})}
    for name, ent in listed.items():
        p = d / ent["file"]
        if not p.is_file():
            out.append(Drift("file_missing", name, str(p)))
            continue
        if sha256_file(p) != ent["sha256"]:
            out.append(Drift("file_changed", name, "sha256 differs from lock"))
            continue
        try:
            cfg = ConfigSpec.from_spec(json.loads(p.read_text(encoding="utf-8")))
        except ConfigError as e:
            out.append(Drift("code_mismatch", name, str(e)))
            continue
        if cfg.fingerprint() != ent["fingerprint"] or cfg.name != name:
            out.append(Drift("code_mismatch", name, "fingerprint differs from lock"))
        if name in lock.get("post_hoc", {}) and cfg.role != "post_hoc":
            out.append(Drift("lock", name, "post-hoc entry must have role post_hoc"))
        if name in lock.get("configs", {}) and cfg.role == "post_hoc":
            out.append(Drift("lock", name, "post_hoc role inside preregistered set"))
    for p in sorted(d.glob("*.json")):
        if p.name == LOCK_NAME:
            continue
        if p.stem not in listed:
            out.append(Drift("unlisted", p.stem, "configuration file not in lock (post-hoc "
                                                 "configurations must be added with add_post_hoc)"))
    if check_code:
        cur = defense_code_hashes()
        for k, v in lock.get("defense_code", {}).items():
            if cur.get(k) != v:
                out.append(Drift("defense_code", k, "defense code changed since lock"))
    return out


def load_frozen(dir_: str | Path, *, include_post_hoc: bool = False,
                allow_code_drift: bool = False) -> ConfigSet:
    """Набор из замороженного каталога; любой дрейф конфигураций — ошибка. Дрейф кода защиты
    по умолчанию тоже ошибка: решение должно воспроизводиться из (данные, профиль, код);
    `allow_code_drift=True` — только для разработки, вызывающий обязан пометить прогон."""
    bad = [x for x in verify(dir_) if not (allow_code_drift and x.kind == "defense_code")]
    if bad:
        raise FrozenConfigError("замороженные конфигурации расходятся: " +
                                "; ".join(f"{x.kind}:{x.name}" for x in bad))
    lock = load_lock(dir_)
    names = list(lock["configs"]) + (list(lock.get("post_hoc", {})) if include_post_hoc else [])
    d = Path(dir_)
    cfgs = [ConfigSpec.from_spec(json.loads((d / f"{n}.json").read_text(encoding="utf-8")))
            for n in names]
    if include_post_hoc:
        # post_hoc не участвует в проверке полноты отдельно: набор без них уже полон
        return ConfigSet(cfgs)
    cs = ConfigSet(cfgs)
    if cs.fingerprint() != lock["set_fingerprint"]:
        raise FrozenConfigError("отпечаток набора расходится с lock")
    return cs


def add_post_hoc(dir_: str | Path, cfg: ConfigSpec) -> dict[str, Any]:
    """Дописать конфигурацию, построенную после блокировки набора (§5): помечается post_hoc,
    существующие записи lock не меняются."""
    d = Path(dir_)
    lock = load_lock(d)
    if cfg.role != "post_hoc":
        raise ConfigError("после блокировки набора добавляются только конфигурации роли post_hoc")
    if cfg.name in lock["configs"] or cfg.name in lock.get("post_hoc", {}):
        raise FrozenConfigError(f"{cfg.name} уже в lock: изменение запрещено")
    p = d / f"{cfg.name}.json"
    if p.exists():
        raise FrozenConfigError(f"{p} уже существует")
    p.write_text(_dump(cfg.to_json()), encoding="utf-8")
    ent = _entry(cfg, p)
    ent["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lock.setdefault("post_hoc", {})[cfg.name] = ent
    (d / LOCK_NAME).write_text(_dump(lock), encoding="utf-8")
    return ent


@dataclass
class FreezeGuard:
    """Контроль заморозки в прогоне (NFR-2.10). Создаётся из lock до первого эпизода."""
    expected: Mapping[str, str]                    # имя → отпечаток конфигурации
    runtime: dict[str, dict[str, Any]] = field(default_factory=dict)
    checks: int = 0

    @classmethod
    def from_lock(cls, dir_: str | Path, *, include_post_hoc: bool = True) -> "FreezeGuard":
        lock = load_lock(dir_)
        exp = {n: e["fingerprint"] for n, e in lock["configs"].items()}
        if include_post_hoc:
            exp.update({n: e["fingerprint"] for n, e in lock.get("post_hoc", {}).items()})
        return cls(exp)

    @classmethod
    def from_set(cls, configs: ConfigSet | Iterable[ConfigSpec]) -> "FreezeGuard":
        return cls({c.name: c.fingerprint() for c in configs})

    def check(self, cfg: ConfigSpec, bundle: Any = None) -> None:
        """Сверить конфигурацию эпизода и (если дана) собранную защиту с замороженными."""
        exp = self.expected.get(cfg.name)
        if exp is None:
            raise FrozenConfigError(f"{cfg.name}: конфигурация не заморожена")
        if cfg.fingerprint() != exp:
            raise FrozenConfigError(f"{cfg.name}: конфигурация изменена после заморозки")
        if bundle is not None:
            rt = bundle.runtime_fingerprints()
            want = {"guard_set": cfg.guards.fingerprint() if cfg.guards else None,
                    "policy": cfg.policy.fingerprint() if cfg.guards else None,
                    "process": None if cfg.process is None else cfg.process.fingerprint()}
            for k, v in want.items():
                if rt.get(k) != v:
                    raise FrozenConfigError(f"{cfg.name}: собранная защита ({k}) не совпадает "
                                            f"с замороженной конфигурацией")
            first = self.runtime.setdefault(cfg.name, rt)
            if first != rt:
                raise FrozenConfigError(f"{cfg.name}: защита меняется между эпизодами")
        self.checks += 1
