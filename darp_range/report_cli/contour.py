"""Проверки «единого локального контура» (NFR-2.5) для сквозного прогона.

NFR-2.5: все проверяемые компоненты запускаются локально в едином контуре и фиксируются
конкретной версией. В zero-версии это сводится к четырём проверяемым фактам:

1. `no_network()` — офлайн-прогон выполняется с запретом сетевых соединений на уровне
   `socket`: любая попытка (кроме AF_UNIX) обрывается `NetworkBlocked` и записывается.
   Это доказательство, а не обещание: если какой-то компонент полезет в сеть, прогон упадёт.
2. `import_audit()` — статический разбор импортов пакета `darp_range` (ast): на уровне
   модулей допустимы только стандартная библиотека и сам пакет; динамические импорты
   (`importlib.import_module("…")`) перечисляются как необязательные зависимости
   выключенных адаптеров.
3. `guard_audit()` — каждый гвардрейл в выбранных конфигурациях: развёртывание
   `builtin`/`self_hosted`, закреплённая версия, лицензия не «proprietary»; закрытые API
   (NFR-2.4) не проходят.
4. `endpoint_audit()` — для живого режима: endpoint RESEARCH_LLM_* в локальном контуре
   (`llm.check_local_endpoint`), модель с закреплённым именем; ключ не раскрывается.
"""

from __future__ import annotations

import ast
import socket
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

PACKAGE = "darp_range"
ALLOWED_DEPLOYMENTS = ("builtin", "self_hosted")
FORBIDDEN_LICENSE_WORDS = ("proprietary", "commercial", "closed")


class NetworkBlocked(ConnectionError):
    """Попытка сетевого соединения при офлайн-прогоне."""


@dataclass
class NetworkGuard:
    attempts: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"network_blocked": True, "attempts": len(self.attempts),
                "targets": sorted(set(self.attempts))}


@contextmanager
def no_network() -> Iterator[NetworkGuard]:
    """Запрет TCP/UDP-соединений внутри блока (AF_UNIX разрешён). `socket.create_connection`
    и urllib проходят через `socket.connect` и перехватываются им."""
    guard = NetworkGuard()
    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex
    orig_sendto = socket.socket.sendto

    def _deny(target: Any) -> None:
        guard.attempts.append(repr(target)[:120])
        raise NetworkBlocked(f"сеть запрещена в офлайн-прогоне: {target!r}")

    def connect(self, address):  # noqa: ANN001 — сигнатура socket.connect
        if self.family == getattr(socket, "AF_UNIX", None):
            return orig_connect(self, address)
        _deny(address)

    def connect_ex(self, address):  # noqa: ANN001
        if self.family == getattr(socket, "AF_UNIX", None):
            return orig_connect_ex(self, address)
        _deny(address)

    def sendto(self, data, *args):  # noqa: ANN001
        if self.family == getattr(socket, "AF_UNIX", None):
            return orig_sendto(self, data, *args)
        _deny(args[-1] if args else None)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.socket.sendto = sendto
    try:
        yield guard
    finally:
        socket.socket.connect, socket.socket.connect_ex = orig_connect, orig_connect_ex
        socket.socket.sendto = orig_sendto


def _dynamic_import(node: ast.Call) -> str | None:
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    if name not in ("import_module", "__import__") or not node.args:
        return None
    a = node.args[0]
    return a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else "<dynamic>"


def import_audit(package_dir: str | Path) -> dict[str, Any]:
    """Сторонние импорты пакета: модульного уровня (недопустимы) и динамические (перечень)."""
    root = Path(package_dir)
    stdlib = set(sys.stdlib_module_names)
    third_party: list[dict[str, Any]] = []
    dynamic: list[dict[str, Any]] = []
    files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        rel = p.relative_to(root.parent).as_posix()
        for n in ast.walk(tree):
            mods: list[str] = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
                mods = [n.module]
            elif isinstance(n, ast.Call):
                d = _dynamic_import(n)
                if d is not None:
                    dynamic.append({"file": rel, "line": n.lineno, "module": d})
                continue
            for m in mods:
                top = m.split(".")[0]
                if top != PACKAGE and top not in stdlib:
                    third_party.append({"file": rel, "line": n.lineno, "module": m})
    return {"files": len(files), "third_party": third_party, "dynamic": dynamic,
            "ok": not third_party,
            "note": "module-level imports: stdlib + darp_range only; dynamic imports are "
                    "optional deps of disabled-by-default adapters"}


def guard_audit(configs: Iterable[Any], catalog: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Гвардрейлы выбранных конфигураций против каталога реестра (NFR-2.4/2.5)."""
    from darp_range.repro.manifest import FLOATING_VERSIONS
    meta = {m["plugin_id"]: m for m in catalog}
    used: dict[str, list[str]] = {}
    for cfg in configs:
        for g in cfg.to_json()["guards"]:
            used.setdefault(g["id"], []).append(cfg.name)
    rows, problems = [], []
    for pid in sorted(used):
        m = meta.get(pid)
        if m is None:
            problems.append(f"{pid}: нет в реестре плагинов")
            continue
        issues = []
        if m["deployment"] not in ALLOWED_DEPLOYMENTS:
            issues.append(f"deployment {m['deployment']!r} вне локального контура")
        if str(m["version"]).strip().lower() in FLOATING_VERSIONS:
            issues.append(f"плавающая версия {m['version']!r}")
        if any(w in str(m["license"]).lower() for w in FORBIDDEN_LICENSE_WORDS):
            issues.append(f"лицензия {m['license']!r} не OSS")
        problems += [f"{pid}: {i}" for i in issues]
        rows.append({"plugin_id": pid, "version": m["version"], "deployment": m["deployment"],
                     "license": m["license"] or "project-internal", "mechanism": m["mechanism"],
                     "configs": sorted(used[pid]), "ok": not issues})
    return {"guards": rows, "problems": problems, "ok": not problems}


def endpoint_audit(environ: Mapping[str, str]) -> dict[str, Any]:
    """Живой режим: локальность endpoint и закреплённость модели (без раскрытия ключа)."""
    from darp_range.llm import LLMConfigError, LLMSettings
    try:
        s = LLMSettings.from_env(environ)
    except LLMConfigError as exc:
        return {"ok": False, "error": str(exc)}
    d = s.public_dict()
    return {"ok": True, "base_url": d["base_url"], "model": d["model"], "params_b": d["params_b"],
            "size_status": d["size_status"], "api_key_set": d["api_key_set"],
            "size_warning": s.size.warning}


# Признаки внешнего исполнителя модели в ответах/ошибках сервера (прокси-маршрутизаторы).
EXTERNAL_MARKERS = ("openrouter", "together.ai", "fireworks", "deepinfra", "groq", "anyscale",
                    "api.openai.com", "anthropic", "darkbloom")


def classify_model_contour(upstream_providers: Mapping[str, int], error_texts: Iterable[str] = (),
                           served_models: Iterable[str] = ()) -> dict[str, Any]:
    """Флаг контура NFR-2.5 по ФАКТИЧЕСКОМУ исполнителю модели (ревизия 1, критик №6).

    Адрес endpoint на loopback ничего не говорит о том, где исполняется модель: локальный
    прокси может пересылать запросы внешнему провайдеру. Поэтому:
    - `external` — сервер сообщил upstream-провайдера (поле `provider` ответа) или текст
      ошибки называет внешний маршрутизатор/провайдера;
    - `unverified` — признаков внешнего провайдера нет, но и подтверждения локального
      исполнения нет (по API его получить нельзя);
    - `local` не выводится из ответов API вовсе: локальность модели подтверждается только
      вне полигона (паспорт развёртывания), поэтому `local_execution_confirmed` всегда False.
    Хеш весов через API недоступен и не придумывается (`weights_sha256: None`)."""
    ev = []
    if upstream_providers:
        ev.append("server reported upstream provider(s): "
                  + ", ".join(f"{k}×{v}" for k, v in sorted(upstream_providers.items())))
    hits = sorted({m for t in error_texts for m in EXTERNAL_MARKERS if m in (t or "").lower()})
    if hits:
        ev.append("error messages name external routers/providers: " + ", ".join(hits))
    served = sorted({m for m in served_models if m})
    return {"model_contour": "external" if ev else "unverified",
            "local_execution_confirmed": False, "evidence": ev,
            "served_model_ids": served, "weights_sha256": None,
            "weights_note": "weight hash/revision is not exposed by the OpenAI-compatible API; "
                            "only the served model id is pinned"}
