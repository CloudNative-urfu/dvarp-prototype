"""Пробный процесс брокера (ревизия 10, broker-side-isolation-evidence).

НЕ входит в staged-пакет атакующего: брокер копирует этот файл и `isolation/probes.py` в
отдельный каталог и монтирует его ro в `/probe` той же командой bwrap, что у процесса
атакующего (меняется только точка входа). Результат читает брокер из stdout.

    probe_main.py SPEC_JSON      — выполнить пробы, напечатать JSON-строку, ждать строки stdin
                                   (брокер в это время смотрит процесс со стороны хоста)
    probe_main.py --sleep S      — напечатать "ready" и спать S секунд (проба die-with-parent)
"""
import importlib.util
import json
import os
import sys
import time

# копия darp_range/isolation/probes.py брокера, лежит рядом в /probe (не пакет darp_range и не
# сторонняя зависимость): загружается по явному пути, без изменения sys.path
_spec = importlib.util.spec_from_file_location(
    "probes", os.path.join(os.path.dirname(os.path.abspath(__file__)), "probes.py"))
probes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probes)


def main(argv):
    if argv[1] == "--sleep":
        sys.stdout.write(json.dumps({"ready": True, "pid": os.getpid()}) + "\n")
        sys.stdout.flush()
        time.sleep(float(argv[2]))
        return 0
    with open(argv[1], encoding="utf-8") as f:
        spec = json.load(f)
    res = probes.run_all(spec, where=spec.get("where", "probe_process"))
    sys.stdout.write(json.dumps({"results": res, "pid": os.getpid()}) + "\n")
    sys.stdout.flush()
    sys.stdin.readline()        # держим процесс, пока брокер смотрит /proc со стороны хоста
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
