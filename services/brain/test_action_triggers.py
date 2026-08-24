"""Regression coverage for the functional action trigger matrix."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


sys.modules.setdefault("validate", SimpleNamespace(validate_message=lambda _message: []))

MODULE_PATH = Path(__file__).with_name("brain.py")
SPEC = importlib.util.spec_from_file_location("delivery_brain_triggers", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def event(type_: str, data: dict, ts: int = 1000) -> dict:
    return {"channel": "perception", "type": type_, "ts": ts, "data": data}


def motions(actions: list[dict]) -> list[str]:
    return [
        action["data"]["motion"]
        for action in actions
        if action["type"] == "play_motion"
    ]


assert motions(MODULE.Brain().handle(event("danmaku.enter", {"user": "viewer"}))) == ["wave"]
assert motions(MODULE.Brain().handle(event("danmaku.gift", {"value_coins": 999}))) == ["thank_small"]
assert motions(MODULE.Brain().handle(event("danmaku.gift", {"value_coins": 1000}))) == ["thank_big"]
assert motions(MODULE.Brain().handle(event("danmaku.gift", {"value_coins": 10000}))) == ["thank_big"]
assert motions(MODULE.Brain().handle(event("danmaku.like", {}))) == ["praise"]
assert motions(MODULE.Brain().handle(event("game.scare", {"intensity": 0.49}))) == []
assert motions(MODULE.Brain().handle(event("game.scare", {"intensity": 0.5}))) == ["scared"]

prank_brain = MODULE.Brain()
assert motions(prank_brain.handle(event("plugin.state", {"prank_count": 1}, 1000))) == []
assert motions(prank_brain.handle(event("plugin.state", {"prank_count": 2}, 2000))) == ["scared"]

# "求礼物"能力已移除(诱导送礼合规红线)；beg 动作(wink+爱心+"爱你哟！")保留，
# 改为关注答谢时的撒娇反应，不再由 gift_begging 模式的 tick 周期触发。
assert motions(MODULE.Brain().handle(event("danmaku.follow", {"user": "viewer"}))) == ["beg"]

expression_cases = {
    "happy": "happy",
    "surprise": "surprised",
}
for detected, expected in expression_cases.items():
    actions = MODULE.Brain().handle(
        event("face.expression", {"label": detected, "confidence": 0.9})
    )
    expressions = [
        action["data"]["expression"]
        for action in actions
        if action["type"] == "set_expression"
    ]
    assert expressions == [expected]

# fear 调侃现在是"坏笑表情(smug) / 真大笑动作(laugh)"随机二选一，多次采样确认
# 两种都会出现、且每次都是这两者之一（不是别的东西）。
fear_reactions = set()
for _ in range(60):
    actions = MODULE.Brain().handle(
        event("face.expression", {"label": "fear", "confidence": 0.9})
    )
    reaction = next(a for a in actions if a["type"] != "speak")
    if reaction["type"] == "set_expression":
        assert reaction["data"]["expression"] == "smug"
        fear_reactions.add("smug")
    elif reaction["type"] == "play_motion":
        assert reaction["data"]["motion"] == "laugh"
        fear_reactions.add("laugh")
    else:
        raise AssertionError(f"unexpected fear reaction: {reaction}")
assert fear_reactions == {"smug", "laugh"}, fear_reactions

manual_laugh = MODULE.Brain().handle(
    {
        "channel": "command",
        "type": "do",
        "ts": 1000,
        "data": {"action": "laugh"},
    }
)
assert motions(manual_laugh) == ["laugh"]

print(
    {
        "ok": True,
        "covered": [
            "enter->wave",
            "gift tiers",
            "like->praise",
            "scare threshold",
            "prank increase->scared",
            "follow->beg (关注答谢)",
            "face expression mappings (fear->smug/laugh random)",
            "manual laugh",
        ],
    }
)
