"""Проверка artifacts/decomposition.json на соответствие workflow-contract.json.

Проверяет: число компонентов 1–20, формат id, известные ID требований,
покрытие всех handoff.requirements, отсутствие key_requirements в out_of_scope,
существование зависимостей и отсутствие циклов, наличие check_command и
непустых assumptions. Работает офлайн, только стандартная библиотека.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def validate(decomposition: dict, handoff: dict) -> list[str]:
    errors: list[str] = []
    required = set(handoff["requirements"])
    key = set(handoff["key_requirements"])
    comps = decomposition.get("components", [])
    if not 1 <= len(comps) <= 20:
        errors.append(f"components: ожидается 1–20, получено {len(comps)}")
    ids = [c.get("id", "") for c in comps]
    if len(ids) != len(set(ids)):
        errors.append("components: повторяющиеся id")
    covered: set[str] = set()
    for c in comps:
        cid = c.get("id", "")
        if not re.fullmatch(r"[a-z0-9_-]+", cid):
            errors.append(f"{cid!r}: id не соответствует [a-z0-9_-]+")
        for field in ("title", "requirements", "depends_on", "check_command"):
            if field not in c:
                errors.append(f"{cid}: нет поля {field}")
        reqs = c.get("requirements", [])
        if not reqs:
            errors.append(f"{cid}: пустой список requirements")
        for r in reqs:
            if r not in required:
                errors.append(f"{cid}: неизвестное требование {r}")
        covered |= set(reqs)
        for dep in c.get("depends_on", []):
            if dep not in ids:
                errors.append(f"{cid}: неизвестная зависимость {dep}")
        cmd = c.get("check_command")
        if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) for a in cmd)):
            errors.append(f"{cid}: check_command должен быть непустым argv")
    out = decomposition.get("out_of_scope", [])
    out_ids = set()
    for o in out:
        if not o.get("reason"):
            errors.append(f"out_of_scope {o.get('id')}: нет причины")
        out_ids.add(o.get("id"))
    for r in out_ids & key:
        errors.append(f"key_requirement {r} в out_of_scope")
    for r in out_ids & covered:
        errors.append(f"{r}: одновременно в компоненте и out_of_scope")
    missing = required - covered - out_ids
    if missing:
        errors.append(f"не распределены требования: {sorted(missing)}")
    if key - covered:
        errors.append(f"key_requirements не в компонентах: {sorted(key - covered)}")
    # Поиск циклов обходом в глубину.
    graph = {c.get("id"): c.get("depends_on", []) for c in comps}
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return True
        if state.get(n) == 2:
            return False
        state[n] = 1
        if any(visit(m) for m in graph.get(n, []) if m in graph):
            return True
        state[n] = 2
        return False

    if any(visit(n) for n in graph):
        errors.append("depends_on содержит цикл")
    assumptions = decomposition.get("assumptions", [])
    if not assumptions:
        errors.append("assumptions пуст")
    for a in assumptions:
        if not all(a.get(k) for k in ("topic", "decision", "rationale")):
            errors.append(f"assumption неполон: {a.get('topic')}")
    return errors


def main() -> int:
    decomposition = json.loads((ROOT / "artifacts" / "decomposition.json").read_text())
    handoff = json.loads((ROOT / "handoff.json").read_text())
    errors = validate(decomposition, handoff)
    for e in errors:
        print("ERROR:", e)
    if not errors:
        n = len(decomposition["components"])
        print(f"OK: {n} компонентов, все {len(handoff['requirements'])} требований распределены")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
