"""Мутационная проверка guard_plugins: каждая мутация меняет одну строку в копии пакета во
временном каталоге и прогоняет test_guard_plugins.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_guard_plugins.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

B = "darp_range/guard_plugins/builtin.py"
T = "darp_range/guard_plugins/textnorm.py"
R = "darp_range/guard_plugins/rules.py"
A = "darp_range/guard_plugins/adapters.py"
MUTANTS = [
    ("нормализация leetspeak выключена", T, "    t = _LEET_TOKEN.sub(_deleet_token, t)\n", ""),
    ("склейка разрядки выключена", T,
     '    t = _SPACED.sub(lambda m: m.group(0).replace(" ", ""), t)\n', ""),
    ("цитаты не понижают вес", T, "w = r.weight * (quote_discount if quoted else 1.0)", "w = r.weight"),
    ("шумное ИЛИ заменено суммой без ограничения снизу", T,
     "        p *= 1.0 - min(1.0, max(0.0, w))", "        p *= 1.0 - min(1.0, max(0.0, w)) * 0.5"),
    ("порог block сравнивается строго", T, "    if score >= block_at:", "    if score > block_at:"),
    ("base64 не декодируется", B, "    for payload in decode_base64_payloads(text, min_len=min_b64):",
     "    for payload in []:"),
    ("контекст: проверяются и доверенные фрагменты", B,
     "        for i, frag in inp.untrusted_fragments():\n            fh = scan_text(",
     "        for i, frag in enumerate(inp.context):\n            fh = scan_text("),
    # «цели при allow» — эквивалентный мутант (noisy-or монотонен: фрагмент выше порога ⇒ весь
    # сигнал выше порога), заменён на проверку отбора целей по порогу фрагмента.
    ("цели: любой фрагмент со слабым совпадением", B,
     '            if fh and grade(noisy_or(h.weight for h in fh), p["flag_at"], p["block_at"]) != "allow":\n                targets.append(i)\n            hits.extend(fh)\n        return _signal(hits, p["flag_at"], p["block_at"], targets=tuple(targets))',
     '            if fh:\n                targets.append(i)\n            hits.extend(fh)\n        return _signal(hits, p["flag_at"], p["block_at"], targets=tuple(targets))'),
    ("правило смены роли удалено", R, '    rule("role.you_are_now"', '    rule("role.you_are_now_x", r"\\bzzzq\\b"), rule("role.unused"'),
    ("валидатор: область задачи не проверяется", B,
     "        if call.tool not in inp.task.allowed_tools:\n            f.block.append",
     "        if False:\n            f.block.append"),
    ("валидатор: схема не проверяется", B, "        errs = _schema_errors(call.tool, args)", "        errs = []"),
    ("валидатор: окно R1 не проверяется", B,
     "            elif window is not None and now - _ts(delivered_at) > timedelta(days=window):",
     "            elif False:"),
    ("валидатор: окно захардкожено вместо доверенной таблицы", B,
     '            window = policy.get("R1", {}).get("window_days")\n            if delivered_at is None:',
     '            window = 30\n            if delivered_at is None:'),
    ("валидатор: final sale без исключения defective", B,
     '            if final_sale and args.get("reason_code") != exc:', "            if final_sale:"),
    ("валидатор: порог R5 даёт block", B,
     '                f.flag.append((f"amount above {thr} cents needs manual review (R5)", "needs_review"))',
     '                f.block.append((f"amount above {thr} cents needs manual review (R5)", "needs_review"))'),
    ("валидатор: дубль не проверяется", B,
     '                f.block.append(("refund already requested for item (R4)", "duplicate_refund"))', "                pass"),
    ("валидатор: принадлежность позиции не проверяется", B,
     "            if row is None or row[1] != cust:", "            if row is None:"),
    ("валидатор: лимит уточнений >", B, "row[0] >= limit:", "row[0] > limit:"),
    ("валидатор: отказ не проверяется", B,
     "            if code == \"outside_window\" and delivered_at is not None and window is not None \\",
     "            if False and code == \"outside_window\" and delivered_at is not None and window is not None \\"),
    ("происхождение: несогласованность источника пропускается", B,
     "            if allowed is None or frag.provenance not in allowed:", "            if allowed is None:"),
    ("происхождение: текст правил не сверяется", B,
     "ref[0] != int(m.group(2)) or ref[1] != frag.text:", "ref[0] != int(m.group(2)):"),
    ("происхождение: ссылки на правила не сверяются с таблицей", B,
     '        unknown = sorted(r for r in refs if f"R{int(r)}" not in known_rules)', "        unknown = []"),
    ("происхождение: taint для refuse тоже", B,
     '                call.args.get("decision") in ("create_refund", "manual_review"):',
     '                call.args.get("decision") is not None:'),
    ("утечка: свой email считается утечкой", B, "            if e == own_email:\n                continue",
     "            if False:\n                continue"),
    ("утечка: Луна не проверяется", B, "            if 13 <= len(digits) <= 19 and luhn_ok(digits):",
     "            if 13 <= len(digits) <= 19:"),
    ("утечка: имена клиентов не проверяются", B, '        for (name,) in db.execute("SELECT name FROM customers WHERE id != ? ORDER BY id", (cust,)):',
     '        for (name,) in []:'),
    ("утечка: чужой заказ не помечается", B, "            if row is not None and row[0] != cust:",
     "            if False:"),
    ("адаптер включён по умолчанию", A,
     '    origin = "catalog #9: Qwen/Qwen3Guard-Gen-4B (Hugging Face), served locally"\n    enabled_by_default = False',
     '    origin = "catalog #9: Qwen/Qwen3Guard-Gen-4B (Hugging Face), served locally"\n    enabled_by_default = True'),
    ("Qwen3Guard: неразобранный ответ = Safe", A,
     '        raise DetectorOutputError("Qwen3Guard: no \'Safety:\' line")', '        return "Safe", []'),
    ("Qwen3Guard: Controversial как Unsafe", A,
     '        sig = self.params["unsafe" if safety == "Unsafe" else "controversial"]', '        sig = self.params["unsafe"]'),
    ("Llama Guard: категории не разбираются", A,
     '    cats = re.findall(r"S\\d{1,2}", lines[1]) if len(lines) > 1 else []', "    cats = []"),
    ("Granite: доверенный контекст тоже отправляется", A,
     '        parts = [f"[{c.provenance}] {c.text}" for _, c in inp.untrusted_fragments()]',
     '        parts = [f"[{c.provenance}] {c.text}" for c in inp.context]'),
    ("Presidio: отсутствие библиотеки = allow", A,
     '            raise AdapterUnavailable("presidio_analyzer is not installed") from exc',
     '            return GuardSignal("allow", score=0.0)'),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    survived = 0
    for name, rel, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            for sub in ("darp_range", "tests", "corpus"):
                shutil.copytree(root / sub, Path(td) / sub,
                                ignore=shutil.ignore_patterns("__pycache__"))
            p = Path(td) / rel
            src = p.read_text(encoding="utf-8")
            if old not in src:
                print(f"BROKEN MUTANT (нет строки): {name}")
                survived += 1
                continue
            p.write_text(src.replace(old, new, 1), encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".",
                                "-p", "test_guard_plugins.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
