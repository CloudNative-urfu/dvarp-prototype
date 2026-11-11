"""Мутационная проверка simple_baseline: каждая мутация меняет одну строку в копии рабочего
каталога во временном каталоге и прогоняет test_simple_baseline.py. Ожидается, что все убиты.

    python3 scripts/mutate_simple_baseline.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FM = "darp_range/simple_baseline/form.py"
RU = "darp_range/simple_baseline/rules.py"
BL = "darp_range/simple_baseline/baseline.py"
MUTANTS = [
    ("R1: окно не проверяется", RU,
     "    if opened is not None and (opened - delivered).days > params.window_days:",
     "    if False:"),
    ("R1: недоставленный заказ не отклоняется", RU,
     '    if od.get("status") != "delivered" or delivered is None:',
     '    if delivered is None:'),
    ("R2: исключение final sale для любой причины", RU,
     "    if item.get(\"final_sale\") and form.reason != params.final_sale_exception:",
     "    if False:"),
    ("R3: сумма из текста клиента/удвоенная", RU,
     '    full = int(item.get("price_cents", 0)) * int(item.get("qty", 1))',
     '    full = int(item.get("price_cents", 0)) * int(item.get("qty", 1)) * 2'),
    ("R3: количество не учитывается", RU,
     '    full = int(item.get("price_cents", 0)) * int(item.get("qty", 1))',
     '    full = int(item.get("price_cents", 0))'),
    ("R4: повторная заявка не отклоняется", RU,
     '    if item.get("refund_requested"):', '    if False:'),
    ("R5: порог ручной проверки не применяется", RU,
     "    if full > params.manual_threshold_cents:", "    if False:"),
    ("R5: повторный возврат по заказу не на проверку", RU,
     '    if any(isinstance(i, Mapping) and i.get("refund_requested") for i in items if i is not item):',
     '    if False:'),
    ("R6: нечитаемый заказ не отклоняется", RU,
     '    if not order_readable or order is None:', '    if order is None:'),
    ("R7: лимит уточнений не соблюдается", RU,
     "    if asks < params.max_clarifications:", "    if True:"),
    ("R7: лимит на единицу больше", RU,
     "    if asks < params.max_clarifications:", "    if asks <= params.max_clarifications:"),
    ("нет причины — возврат с причиной other", RU,
     '        return _ask("reason", asks, params, "R7: reason missing")',
     '        form = form.merge(__import__("darp_range.simple_baseline.form", fromlist=["x"]).parse_message("reason: other"))'),
    ("параметры правил из недоверенного происхождения", RU,
     '            if isinstance(r, Mapping) and r.get("provenance") == "trusted_policy":',
     '            if isinstance(r, Mapping):'),
    ("справка вместо уточнения при намерении", RU,
     "    if form.info_only or not form.intent:", "    if form.info_only or True:"),
    ("несколько заказов — берётся первый", FM,
     "        elif len(p.order_ids) > 1:\n            f = replace(f, order_id=None, order_ambiguous=True)",
     "        elif len(p.order_ids) > 1:\n            f = replace(f, order_id=p.order_ids[0])"),
    ("заказ из карточки перекрывает названный клиентом", FM,
     "        if self.order_id is None and not self.order_ambiguous and order_id:",
     "        if order_id:"),
    ("без раскрытия leetspeak", FM,
     "    low = _MIXED.sub(lambda m: m.group(0).translate(_LEET), low)", "    pass"),
    ("без склейки разрядки", FM,
     '    return _SPACED.sub(lambda m: m.group(0).replace(" ", ""), low)', "    return low"),
    ("wrong size распознаётся как wrong item", FM,
     '    ("does_not_fit", r"\\b(?:dont|don\'t|doesnt|doesn\'t|does not|do not|didnt|didn\'t|not)\\s+fit"\n                     r"|wrong size',
     '    ("does_not_fit", r"\\b(?:dont|don\'t|doesnt|doesn\'t|does not|do not|didnt|didn\'t|not)\\s+fit"\n                     r"|xx size'),
    ("неоднозначная позиция угадывается первой", FM,
     '    if len(exact) > 1:\n        return None, "ambiguous"',
     '    if len(exact) > 1:\n        return exact[0], "title"'),
    ("общее слово названий определяет позицию", FM,
     "        own = words - others", "        own = words"),
    ("чат не закрывается после финала", BL,
     '        if end == "finalized":\n            self._closed = True',
     '        if end == "finalized":\n            pass'),
    ("уточнение не считается", BL, "            self._asks += 1", "            pass"),
    ("счётчик уточнений обращения игнорируется", BL,
     '            self._asks = int(self._ticket.get("clarifications") or 0)',
     '            self._asks = 0'),
    ("в журнал g_simple пишется текст реплики", BL,
     '        self._emit("form_parse", {"turn": self.turns, "parsed": parsed.fields(),',
     '        self._emit("form_parse", {"turn": self.turns, "parsed": parsed.fields(), "text": text,'),
    ("финал с решением manual_review вместо refuse", BL,
     'DECISION_OF = {"refund": "create_refund", "refuse": "refuse", "manual_review": "manual_review"}',
     'DECISION_OF = {"refund": "create_refund", "refuse": "manual_review", "manual_review": "manual_review"}'),
    ("роль инструментов не проверяется", BL,
     '        if toolbox.role != "simple_baseline":', '        if False:'),
]


def main(argv: list[str]) -> int:
    src = Path(argv[1] if len(argv) > 1 else ".").resolve()
    survivors = []
    with tempfile.TemporaryDirectory(prefix="darp-mut-simple-") as tmp:
        for name, rel, old, new in MUTANTS:
            dst = Path(tmp) / "w"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "tmp", "runs", ".cache", "venv", ".venv", ".runtime", "artifacts",
                ".home"))
            f = dst / rel
            text = f.read_text(encoding="utf-8")
            if text.count(old) != 1:
                print(f"BAD   {name}: шаблон найден {text.count(old)} раз")
                survivors.append(name)
                continue
            f.write_text(text.replace(old, new), encoding="utf-8")
            p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t",
                                ".", "-p", "test_simple_baseline.py"], cwd=dst,
                               capture_output=True, text=True, timeout=900)
            killed = p.returncode != 0
            print(("KILLED" if killed else "SURVIVED"), name)
            if not killed:
                survivors.append(name)
    print(f"{len(MUTANTS) - len(survivors)}/{len(MUTANTS)} мутаций убито")
    return 0 if not survivors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
