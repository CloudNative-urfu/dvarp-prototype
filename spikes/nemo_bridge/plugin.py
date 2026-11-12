"""Spike #13: bridge plugin NVIDIA NeMo Guardrails -> DARP guard contract (variant A).

NeMo rails run as an ordinary `GuardPlugin`: the polygon's `GuardHost` calls `inspect`, the
bridge asks `LLMRails.check(messages, rail_types=[...])` (nemoguardrails 0.24.1,
`rails/llm/llmrails.py`) and maps `RailsResult.status` to a signal:
PASSED -> allow, BLOCKED -> block (rail name in reasons/labels), MODIFIED -> flag
(our contract forbids rewriting text, so the modified content is dropped). NeMo's own
refusal message ("bot refuse to respond") is also dropped: the reaction is chosen by our
`ReactionPolicy`, not by the framework (FR-1.8).

Freeze: the rails config directory hash and the installed nemoguardrails version are plugin
params, so they enter `describe()` -> plugin/GuardSet/config fingerprints; a changed
config.yml or library version fails at plugin construction.

Not part of develop: needs the isolated uv environment in this directory (see
docs/guardrails-integration-spike.md). NeMo is imported lazily, as in guard_plugins/adapters.py.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import os
from pathlib import Path

from darp_range.guard_contract import (
    GuardContractError, GuardInput, GuardPlugin, GuardRuntime, GuardSignal, register,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = "spikes/nemo_bridge/rails"
_RAILS: dict[str, object] = {}


def config_sha256(rel_dir: str) -> str:
    """sha256 over (relative path, bytes) of every file in the rails config directory."""
    h = hashlib.sha256()
    base = ROOT / rel_dir
    for p in sorted(x for x in base.rglob("*") if x.is_file()):
        h.update(p.relative_to(base).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


@register
class NemoRailsBridge(GuardPlugin):
    plugin_id = "nemo-rails-bridge"
    version = "0.1-spike"
    mechanism = "rules"            # the configured rails are regex only; an LLM rail -> llm_detector
    stages = ("input", "output")   # LLMRails.check supports only RailType.INPUT/OUTPUT
    deployment = "self_hosted"
    license = "Apache-2.0"
    origin = "NVIDIA NeMo Guardrails (pypi nemoguardrails), LLMRails.check; spike #13"
    enabled_by_default = False
    default_params = {
        "config_dir": CONFIG_DIR,
        "config_sha256": "42bbce5a53ad3a04a890bcb57986e58b89b7b4c2650d377f575ea3c89c59df31",
        "nemo_version": "0.24.1",
        "on_blocked": "block",
        "on_modified": "flag",
    }

    def __init__(self, **params):
        super().__init__(**params)
        p = self.params
        got = importlib.metadata.version("nemoguardrails")
        if got != p["nemo_version"]:
            raise GuardContractError(f"nemoguardrails {got} != pinned {p['nemo_version']}")
        sha = config_sha256(p["config_dir"])
        if sha != p["config_sha256"]:
            raise GuardContractError(f"rails config {p['config_dir']} sha256 {sha} != pinned")
        if sha not in _RAILS:   # one-time setup, not charged to a guard call
            os.environ.setdefault("NEMO_GUARDRAILS_NO_USAGE_STATS", "1")  # no telemetry
            nemo = importlib.import_module("nemoguardrails")
            _RAILS[sha] = nemo.LLMRails(nemo.RailsConfig.from_path(str(ROOT / p["config_dir"])))

    def inspect(self, inp: GuardInput, rt: GuardRuntime) -> GuardSignal:
        opts = importlib.import_module("nemoguardrails.rails.llm.options")
        explain = importlib.import_module("nemoguardrails.logging.explain")
        ctx = importlib.import_module("nemoguardrails.context")
        rails = _RAILS[self.params["config_sha256"]]
        msgs = [{"role": "user", "content": inp.text or " "}]
        if inp.stage == "output":
            msgs.append({"role": "assistant", "content": inp.candidate_output or " "})
        kind = opts.RailType.OUTPUT if inp.stage == "output" else opts.RailType.INPUT
        ctx.explain_info_var.set(explain.ExplainInfo())   # per-call LLM stats, not cumulative
        res = rails.check(msgs, rail_types=[kind])
        calls = rails.explain().llm_calls
        if calls:   # LLM-based rails: tokens go to C_guard; latency/CPU are measured by the host
            rt.charge(tokens_in=sum(c.prompt_tokens or 0 for c in calls),
                      tokens_out=sum(c.completion_tokens or 0 for c in calls),
                      requests=len(calls))
        status = res.status.value
        if status == "passed":
            return GuardSignal("allow", reasons=("nemo: passed",))
        if status == "blocked":
            label = "nemo." + "".join(ch if ch.isalnum() else "_" for ch in res.rail.lower())
            return GuardSignal(self.params["on_blocked"], score=1.0,
                               reasons=(f"nemo: blocked by '{res.rail}'",), labels=(label[:64],))
        return GuardSignal(self.params["on_modified"], score=0.5, reasons=("nemo: modified",))
