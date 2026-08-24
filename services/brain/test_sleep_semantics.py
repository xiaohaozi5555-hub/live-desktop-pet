"""Focused regression test for persistent sleep command ordering."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


sys.modules.setdefault("validate", SimpleNamespace(validate_message=lambda _message: []))

MODULE_PATH = Path(__file__).with_name("brain.py")
SPEC = importlib.util.spec_from_file_location("delivery_brain", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

brain = MODULE.Brain()
sleep_actions = brain.handle(
    {"channel": "command", "type": "sleep", "ts": 1000, "data": {}}
)

assert brain.mode == "SLEEP"
assert [action["type"] for action in sleep_actions] == ["stop", "play_motion"]
assert sleep_actions[1]["data"]["motion"] == "sleep"

ignored = brain.handle(
    {
        "channel": "perception",
        "type": "danmaku.gift",
        "ts": 2000,
        "data": {"value_coins": 10000},
    }
)
assert ignored == []

wake_actions = brain.handle(
    {"channel": "command", "type": "wake", "ts": 3000, "data": {}}
)
assert brain.mode == "ACTIVE"
assert wake_actions[0]["type"] == "play_motion"
assert wake_actions[0]["data"]["motion"] == "idle"

print(
    {
        "ok": True,
        "sleep_order": [action["type"] for action in sleep_actions],
        "sleep_ignores_perception": True,
        "wake_motion": wake_actions[0]["data"]["motion"],
    }
)
