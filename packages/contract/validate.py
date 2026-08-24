#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件契约校验器（纯标准库，无需第三方依赖）。

用法:
    python validate.py <file.jsonl>      # 校验一个 jsonl 文件，每行一条消息
可编程使用:
    from validate import validate_message
    errors = validate_message(msg_dict)   # 返回错误字符串列表，空列表=通过

校验分两层:
    1) 信封: 依据 schema/<channel>.schema.json (channel 常量 / type 枚举 / ts 整数 / data 对象)
    2) 载荷: 依据本文件内置 PAYLOAD_SPEC (每种 type 的必填字段与基本类型)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    with open(os.path.join(HERE, "schema", name), encoding="utf-8") as f:
        return json.load(f)


CHANNEL_SCHEMAS = {
    "perception": _load("perception.schema.json"),
    "command": _load("command.schema.json"),
    "action": _load("action.schema.json"),
}

# 每种 type 的 data 必填字段 -> 允许的 Python 类型
PAYLOAD_SPEC = {
    # perception
    "danmaku.enter": {"user": str},
    "danmaku.chat": {"user": str, "text": str},
    "danmaku.gift": {"user": str, "gift_name": str, "count": int, "value_coins": int},
    "danmaku.like": {"user": str, "count": int},
    "danmaku.follow": {"user": str},
    "danmaku.health": {"ok": bool},   # 弹幕链路断流告警，见 events.md 同名小节
    "face.expression": {"label": str, "confidence": (int, float)},
    "game.scene": {"summary": str, "tags": list, "stuck": bool},
    "game.scare": {"intensity": (int, float)},
    "plugin.state": {},  # 字段均可选
    "audio.command": {"intent": str, "raw_text": str, "speaker_verified": bool},
    "audio.self_speaking": {"on": bool},
    # command
    "mute": {}, "unmute": {}, "sleep": {}, "wake": {},
    "do": {"action": str},
    "mode": {"name": str, "on": bool},
    "calibrate": {"region": str, "box": dict, "layout": str},
    "set_viewer_tier": {"nickname": str, "tier": str},
    "stream_start": {}, "stream_end": {},
    # action
    "play_motion": {"motion": str},
    "set_expression": {"expression": str},
    "show_bubble": {"text": str},
    "speak": {"text": str},
    "stop": {},
}

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _validate_schema(schema, inst, path="$"):
    """一个 JSON-Schema 子集校验器: 支持 const/enum/type/required/properties/items。"""
    errors = []
    if "const" in schema and inst != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {inst!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errors.append(f"{path}: {inst!r} not in enum {schema['enum']}")
    if "type" in schema:
        t = schema["type"]
        if not _TYPE_CHECKS[t](inst):
            errors.append(f"{path}: expected type {t}, got {type(inst).__name__}")
            return errors  # 类型都不对, 后续不再深入
    if schema.get("type") == "object":
        for req in schema.get("required", []):
            if req not in inst:
                errors.append(f"{path}: missing required '{req}'")
        for k, subs in schema.get("properties", {}).items():
            if k in inst:
                errors += _validate_schema(subs, inst[k], f"{path}.{k}")
    if schema.get("type") == "array" and "items" in schema:
        for i, item in enumerate(inst):
            errors += _validate_schema(schema["items"], item, f"{path}[{i}]")
    return errors


def validate_message(msg):
    """校验单条消息, 返回错误字符串列表 (空=通过)。"""
    if not isinstance(msg, dict):
        return ["$: message is not an object"]
    channel = msg.get("channel")
    if channel not in CHANNEL_SCHEMAS:
        return [f"$.channel: unknown channel {channel!r}"]
    errors = _validate_schema(CHANNEL_SCHEMAS[channel], msg)
    mtype = msg.get("type")
    spec = PAYLOAD_SPEC.get(mtype)
    if spec is None:
        if not errors:
            errors.append(f"$.type: no payload spec for {mtype!r}")
        return errors
    data = msg.get("data", {})
    if not isinstance(data, dict):
        errors.append("$.data: not an object")
        return errors
    for key, typ in spec.items():
        if key not in data:
            errors.append(f"$.data: missing '{key}' for type '{mtype}'")
        elif not isinstance(data[key], typ):
            tn = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
            errors.append(f"$.data.{key}: expected {tn}, got {type(data[key]).__name__}")
    return errors


def validate_file(path):
    total, bad = 0, 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                bad += 1
                print(f"  L{lineno}: JSON 解析失败: {e}")
                continue
            errs = validate_message(msg)
            if errs:
                bad += 1
                print(f"  L{lineno} [{msg.get('channel')}/{msg.get('type')}] 不合法:")
                for e in errs:
                    print(f"      - {e}")
    ok = total - bad
    print(f"契约校验: {ok}/{total} 通过, {bad} 失败  <- {path}")
    return bad == 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) != 2:
        print("用法: python validate.py <file.jsonl>")
        sys.exit(2)
    sys.exit(0 if validate_file(sys.argv[1]) else 1)
