"""Матрица доступа и модель угроз (ревизия 9, задача access-matrix).

`configs/isolation/access_matrix.json`: субъекты attacker/defender/evaluator/broker ×
ресурсы → allow/deny, механизм обеспечения, ID теста. `validate(matrix)` возвращает список
ошибок (пусто — матрица корректна):
- у каждой строки есть субъект из списка, ресурс, access, механизм и тест;
- каждая deny-строка attacker ссылается на существующую отрицательную OS-пробу
  (`probe:<id>` из `probes.NEGATIVE`);
- каждая allow-строка (любого субъекта) — на позитивную пробу (`probes.POSITIVE`) или
  существующий unittest (`unittest:<модуль.Класс.метод>`);
- deny-строки остальных субъектов ссылаются на существующий тест (они не OS-изолированы в
  этой ревизии — механизм и примечание это говорят);
- ключ `llm_api_key` разрешён только broker (и broker-строка обязательна);
- каждая отрицательная проба покрыта хотя бы одной deny-строкой attacker;
- текущая основа — boundary-2r, boundary-1 — legacy; пары (субъект, ресурс) уникальны.

Ревизия 10 (claim-level-in-manifest):
- у каждой строки обязательное `enforcement`: `os` (обеспечено ОС: namespaces/монтирования/
  окружение процесса) или `convention` (соглашение кода в одном процессе);
- каждая deny-строка с `enforcement=os` ссылается на СОБСТВЕННУЮ пробу (`probe:<id>` из
  `probes.NEGATIVE` или наблюдений брокера `broker_probes.HOST_PROBES`), не делит её с другой
  os-deny-строкой; файловые пробы читают свой ресурс со своей синтетической канарейкой;
- deny-строки атакующего — только `os`;
- строка defender→attacker_memory — `convention` с записанной известной утечкой
  (`known_leak.probe = defender_gc_attacker_canary`).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .probes import NEGATIVE, POSITIVE

ENFORCEMENT = ("os", "convention")
DEFENDER_GC_PROBE = "defender_gc_attacker_canary"


def _host_probes() -> set[str]:
    from .broker_probes import HOST_PROBES
    return set(HOST_PROBES)

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "configs" / "isolation" / "access_matrix.json"
SUBJECTS = ("attacker", "defender", "evaluator", "broker")
KEY_RESOURCE = "llm_api_key"


def load_matrix(path: str | Path = MATRIX_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unittest_exists(ref: str) -> bool:
    """`tests.mod.Class.method` → существует ли такой тест (импорт модуля и атрибутов)."""
    parts = ref.split(".")
    for i in range(len(parts) - 1, 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        try:
            for a in parts[i:]:
                obj = getattr(obj, a)
        except AttributeError:
            return False
        return callable(obj) and parts[-1].startswith("test")
    return False


def _row_name(i: int, row: Mapping[str, Any]) -> str:
    return f"row {i} ({row.get('subject')}/{row.get('resource')}/{row.get('access')})"


def validate(matrix: Mapping[str, Any], *,
             test_exists: Callable[[str], bool] = unittest_exists) -> list[str]:
    errs: list[str] = []
    b = matrix.get("boundary") or {}
    if b.get("current") != "boundary-2r":
        errs.append("boundary.current должен быть boundary-2r")
    if "boundary-1" not in (b.get("legacy") or []):
        errs.append("boundary-1 должен быть помечен legacy")
    rows = matrix.get("rows")
    if not isinstance(rows, list) or not rows:
        return errs + ["rows: пустая матрица"]
    seen: set[tuple[str, str]] = set()
    covered: set[str] = set()
    os_probe_owner: dict[str, str] = {}
    known = set(NEGATIVE) | _host_probes()
    key_allowed: list[str] = []
    for i, row in enumerate(rows):
        n = _row_name(i, row)
        missing = [k for k in ("subject", "resource", "access", "mechanism", "test")
                   if not isinstance(row.get(k), str) or not row.get(k).strip()]
        if missing:
            errs.append(f"{n}: нет полей {missing}")
            continue
        subj, res, acc, test = row["subject"], row["resource"], row["access"], row["test"]
        if subj not in SUBJECTS:
            errs.append(f"{n}: субъект вне {SUBJECTS}")
        if acc not in ("allow", "deny"):
            errs.append(f"{n}: access должен быть allow|deny")
        if (subj, res) in seen:
            errs.append(f"{n}: повтор пары субъект/ресурс")
        seen.add((subj, res))
        enf = row.get("enforcement")
        if enf not in ENFORCEMENT:
            errs.append(f"{n}: enforcement должен быть os|convention, получено {enf!r}")
        kind, _, ref = test.partition(":")
        if acc == "deny" and enf == "os":
            if kind != "probe" or ref not in known:
                errs.append(f"{n}: deny+os без собственной OS-пробы ({test})")
            elif ref in os_probe_owner:
                errs.append(f"{n}: проба {ref} уже принадлежит строке {os_probe_owner[ref]}")
            else:
                os_probe_owner[ref] = n
        if subj == "attacker" and acc == "deny" and enf != "os":
            errs.append(f"{n}: deny-строка атакующего должна быть enforcement=os")
        if subj == "defender" and res == "attacker_memory":
            kl = row.get("known_leak") or {}
            if enf != "convention" or kl.get("probe") != DEFENDER_GC_PROBE:
                errs.append(f"{n}: defender→attacker_memory — только convention с known_leak "
                            f"{DEFENDER_GC_PROBE}")
        if kind not in ("probe", "unittest") or not ref:
            errs.append(f"{n}: тест должен быть probe:<id> или unittest:<id>, получено {test!r}")
            continue
        if subj == "attacker" and acc == "deny":
            if kind != "probe" or ref not in known:
                errs.append(f"{n}: deny-строка атакующего без существующей отрицательной "
                            f"OS-пробы ({test})")
            else:
                covered.add(ref)
        elif acc == "allow":
            ok = (kind == "probe" and ref in POSITIVE) or (kind == "unittest" and test_exists(ref))
            if not ok:
                errs.append(f"{n}: allow-строка без позитивного теста ({test})")
        else:
            ok = (kind == "probe" and ref in NEGATIVE) or (kind == "unittest" and test_exists(ref))
            if not ok:
                errs.append(f"{n}: deny-строка без существующего теста ({test})")
        if res == KEY_RESOURCE and acc == "allow":
            key_allowed.append(subj)
    if [s for s in key_allowed if s != "broker"]:
        errs.append(f"{KEY_RESOURCE}: разрешён не только broker: {sorted(set(key_allowed))}")
    if "broker" not in key_allowed:
        errs.append(f"{KEY_RESOURCE}: нет allow-строки broker")
    for s in SUBJECTS:
        if s != "broker" and (s, KEY_RESOURCE) not in seen:
            errs.append(f"{KEY_RESOURCE}: нет deny-строки для {s}")
    if not any(r.get("subject") == "defender" and r.get("resource") == "attacker_memory"
               for r in rows):
        errs.append("нет строки defender→attacker_memory (известная утечка не записана)")
    uncovered = sorted(known - covered)
    if uncovered:
        errs.append(f"отрицательные пробы без deny-строки атакующего: {uncovered}")
    return errs


def attacker_rows(matrix: Mapping[str, Any]) -> list[dict]:
    return [r for r in matrix["rows"] if r.get("subject") == "attacker"]
