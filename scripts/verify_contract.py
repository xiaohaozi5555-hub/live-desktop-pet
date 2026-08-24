#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约层离线验证：不依赖任何第三方库、不开播，即可证明脚手架+契约+夹具+mock总线可用。

检查项：
  1. packages/contract/sample_events.jsonl 每条消息都通过契约校验（22 种事件类型样例）。
  2. 弹幕夹具经 mock 总线归一化后全部通过校验，且事件数量>0、类型分布符合预期。
  3. 插件夹具 parsed 字段类型正确。
退出码：0=全部通过，1=有失败。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "packages", "contract"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import validate as contract  # noqa: E402
import mock_bus  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))
    return ok


# --- 1. 契约样例 ---
sample_path = os.path.join(REPO, "packages", "contract", "sample_events.jsonl")
check("契约样例全部合法 (sample_events.jsonl)", contract.validate_file(sample_path),
      "见上方逐行输出")

# --- 2. 弹幕夹具归一化回放 ---
fixture = os.path.join(REPO, "fixtures", "danmaku", "sample-session.jsonl")
bus = mock_bus.Bus()
seen = []
bus.subscribe(lambda m: seen.append(m["type"]))
published, errors = mock_bus.replay(fixture, bus)
check("弹幕夹具归一化后无校验错误", len(errors) == 0, f"{len(errors)} 个错误")
check("弹幕夹具事件数 > 0", len(published) > 0, f"{len(published)} 条")
counts = {t: seen.count(t) for t in sorted(set(seen))}
expected = {"danmaku.enter", "danmaku.chat", "danmaku.gift", "danmaku.like", "danmaku.follow"}
check("弹幕类型覆盖 进场/弹幕/礼物/点赞/关注", expected.issubset(counts.keys()), str(counts))

# --- 3. 插件夹具 ---
plugin_path = os.path.join(REPO, "fixtures", "plugin", "sample-plugin-state.json")
with open(plugin_path, encoding="utf-8") as f:
    plugin = json.load(f)
parsed = plugin.get("parsed", {})
check("插件夹具 countdown_sec 为整数",
      isinstance(parsed.get("countdown_sec"), int) and not isinstance(parsed.get("countdown_sec"), bool),
      str(parsed.get("countdown_sec")))
check("插件夹具 prank_count 为整数",
      isinstance(parsed.get("prank_count"), int) and not isinstance(parsed.get("prank_count"), bool),
      str(parsed.get("prank_count")))

# --- 汇总 ---
passed = sum(1 for r in results if r)
total = len(results)
print(f"\n==== 契约层验证: {passed}/{total} 通过 ====")
sys.exit(0 if passed == total else 1)
