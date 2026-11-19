"""Shared helpers for test modules (no cross-imports between test files)."""


def decisions(events):
    """Guard/policy/process decisions from journal events: (episode, component, kind, signal)."""
    return [
        (
            e["episode_id"],
            e["component"],
            e["kind"],
            e["data"].get("signal") or e["data"].get("action"),
        )
        for e in events
        if e["component"].startswith(("guard", "policy", "process"))
    ]
