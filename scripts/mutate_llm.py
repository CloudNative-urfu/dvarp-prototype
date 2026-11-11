"""Мутационная проверка llm: каждая мутация меняет одну строку в копии пакета во временном
каталоге и прогоняет test_llm.py. Ожидается, что все мутации убиты.

    python3 scripts/mutate_llm.py [рабочий_каталог]
Код 0 — все убиты, 1 — есть выжившие.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ST = "darp_range/llm/settings.py"
LI = "darp_range/llm/limits.py"
BE = "darp_range/llm/backends.py"
CL = "darp_range/llm/client.py"
TK = "darp_range/llm/usage_estimate.py"
MUTANTS = [
    ("NFR-2.5: публичный IP допускается", ST,
     "local = ip.is_loopback or ip.is_private or ip.is_link_local", "local = True"),
    ("NFR-2.5: любое имя хоста допускается", ST,
     'local = host == "localhost" or host.endswith(".localhost")', "local = True"),
    ("NFR-2.5: учётные данные в URL", ST, "if parts.username or parts.password:", "if False:"),
    ("NFR-2.5: плавающее имя модели", ST,
     "if self.model.strip().lower() in FLOATING_VERSIONS:", "if False:"),
    ("NFR-2.6: предел 12B снят", ST, "if params_b <= SIZE_EXCEPTION_B:", "if True:"),
    ("NFR-2.6: 9B без пометки исключения", ST, "if params_b <= SIZE_TARGET_B:", "if params_b <= SIZE_EXCEPTION_B:"),
    ("NFR-2.6: неизвестный размер допускается", ST, "    if params_b is None:\n        raise",
     "    if params_b is None:\n        return SizeVerdict(0.0, 'within_target', None)\n        raise"),
    ("NFR-2.6: размер по наименьшему числу имени", ST, "return max(float(num)", "return min(float(num)"),
    ("ключ в public_dict", ST, '"api_key_set": self.has_api_key}', '"api_key_set": self._api_key}'),
    ("seed не в запросе", BE, '"temperature": s.temperature, "seed": s.seed,', '"temperature": s.temperature,'),
    ("лимит запросов на эпизод не проверяется", LI,
     "if ep is not None and ep.requests >= L.max_requests_per_episode:", "if False:"),
    ("лимит запросов на прогон не проверяется", LI,
     "if self.run.requests >= L.max_requests_per_run:", "if False:"),
    ("бюджет токенов не ограничивает max_tokens", LI, "budget = min(budget, left)", "pass"),
    ("бюджет токенов не блокирует", LI, "if left <= 0:", "if False:"),
    ("сбойные запросы не расходуют лимит", CL,
     "self.meter.record(episode_id, tokens_in=t_in, tokens_out=0, failed=True)", "pass"),
    ("лимит проверяется после запроса", CL,
     "        budget = self.meter.admit(episode_id)  # LimitExceeded — запрос не отправляется\n",
     "        budget = self.limits.max_output_tokens\n"),
    ("токены ответа не учитываются", CL,
     "self.meter.record(episode_id, tokens_in=comp.usage.tokens_in, tokens_out=comp.usage.tokens_out)",
     "self.meter.record(episode_id, tokens_in=comp.usage.tokens_in, tokens_out=0)"),
    ("llm_call не пишется в журнал", CL,
     "            self.writer.emit(LOG_KIND, data, episode_id=episode_id, cost=cost, t_sim=t_sim)",
     "            pass"),
    ("guard-писатель принимается молча", CL,
     "if writer is not None and LOG_KIND not in KINDS[component_class(writer.component)]:", "if False:"),
    ("usage сервера игнорируется", BE,
     "if isinstance(p, int) and isinstance(c, int) and p >= 0 and c >= 0:", "if False:"),
    ("невалидные аргументы инструмента → {}", BE,
     'raise LLMResponseError(f"tool_calls[{i}] {name}: аргументы не JSON") from None', "args = {}"),
    ("HTTP-ошибка не поднимается", BE, "if status != 200:", "if status >= 600:"),
    ("заглушка недетерминирована", BE, "return make_rng(self.seed, self.request_key, *labels)",
     "import random; return random.Random()"),
    ("версия заглушки не зависит от профиля", BE,
     'self.version = hash_obj({"profile": self.profile,', 'self.version = hash_obj({"profile": None,'),
    ("заглушка не обрезает по max_tokens", BE, "if out_tokens > max_tokens:", "if False:"),
    ("заглушка вызывает неописанный инструмент", BE, "if name not in allowed:", "if False:"),
    ("оценка токенов без округления вверх", TK,
     "return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0", "return len(text) // CHARS_PER_TOKEN"),
    ("extra_body не передаётся", BE, 'body: dict[str, Any] = {**s.extra_body, "model": s.model,',
     'body: dict[str, Any] = {"model": s.model,'),
    ("extra_body перекрывает закреплённые поля", ST, "if reserved:", "if False:"),
    ("reasoning_tokens не учитываются", BE, "rt if isinstance(rt, int) and rt >= 0 else 0,", "0,"),
    ("живой режим без отключения рассуждения", "darp_range/llm/factory.py",
     'LIVE_EXTRA_BODY = {"reasoning": {"enabled": False}}', "LIVE_EXTRA_BODY = {}"),
    ("текст промпта в журнале", CL, '"n_tools": len(tls), "max_tokens": budget, "prompt_sha": prompt_sha}',
     '"n_tools": len(tls), "max_tokens": budget, "prompt_sha": prompt_sha, "prompt": [m.content for m in msgs]}'),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    survived = 0
    for name, rel, old, new in MUTANTS:
        with tempfile.TemporaryDirectory() as td:
            for sub in ("darp_range", "tests"):
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
                                "-p", "test_llm.py"], cwd=td, capture_output=True)
            ok = r.returncode != 0
            survived += not ok
            print(("killed:   " if ok else "SURVIVED: ") + name)
    print(f"итог: {len(MUTANTS) - survived}/{len(MUTANTS)} убито")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
