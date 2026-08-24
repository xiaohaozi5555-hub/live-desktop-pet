#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏画面理解（卡关攻略 / 场景标签）：多模态大模型。**支持国产**。

两条后端，按环境变量自动选：
  - openai 兼容（推荐国内）：通义千问 Qwen-VL / 智谱 GLM-4V / 豆包 / Kimi 等。
      PET_VISION_BASE_URL + PET_VISION_API_KEY + PET_VISION_MODEL（见 .env.example）。
  - anthropic（Claude，需海外网络）：ANTHROPIC_API_KEY [+ PET_VISION_MODEL]。
两者都把游戏画面 + 提示 → 结构化 JSON → perception.game.scene。
"""
import base64
import json
import os
import re
import threading

_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "stuck": {"type": "boolean"},
        "hint": {"type": "string"},
    },
    "required": ["summary", "tags", "stuck", "hint"],
    "additionalProperties": False,
}

_PROMPT = (
    "你是恐怖游戏主播的桌宠助手。看这张游戏画面，**只输出一个 JSON 对象**（不要额外文字/代码块）：\n"
    '{"summary":"中文一句话描述场景","tags":["dark/blood/jumpscare/puzzle/combat/safe/menu 里选"],'
    '"stuck":true或false(主播是否可能卡关),"hint":"卡关则给具体可操作攻略,否则给简短操作建议或鼓励"}'
)


def _maybe_downscale(image_bytes, max_edge=1280):
    """截图降分辨率以省 token（图像 token 随分辨率涨）。无 PIL 或失败则原样返回。"""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(image_bytes))
        w, h = im.size
        if max(w, h) <= max_edge:
            return image_bytes
        s = max_edge / max(w, h)
        im = im.convert("RGB").resize((int(w * s), int(h * s)))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return image_bytes


def make_scene(summary, tags, stuck, hint=None, ts=0, sources=None):
    data = {"summary": summary, "tags": list(tags), "stuck": bool(stuck)}
    if hint:
        data["hint"] = hint
    if sources:
        # 攻略出处。带上它主播才能自己去核对——这条链路的全部意义就是"可核对"。
        data["sources"] = sources
    return {"channel": "perception", "type": "game.scene", "ts": ts,
            "source": "perception.game", "data": data}


def _pick_provider():
    p = (os.environ.get("PET_VISION_PROVIDER") or "auto").lower()
    if p in ("openai", "anthropic"):
        return p
    if os.environ.get("PET_VISION_BASE_URL") or os.environ.get("PET_VISION_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError("未配置视觉后端：设 PET_VISION_BASE_URL+PET_VISION_API_KEY(国产) 或 ANTHROPIC_API_KEY(.env)")


def _parse_json(text):
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t.split("\n", 1)[1] if "\n" in t else t
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _analyze_openai(b64, ts):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("PET_VISION_API_KEY"),
                    base_url=os.environ.get("PET_VISION_BASE_URL"))
    model = os.environ.get("PET_VISION_MODEL", "qwen-vl-max")
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": _PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}]
    try:
        resp = client.chat.completions.create(model=model, messages=msgs, max_tokens=800,
                                              response_format={"type": "json_object"})
    except Exception:
        resp = client.chat.completions.create(model=model, messages=msgs, max_tokens=800)
    return _parse_json(resp.choices[0].message.content)


def _analyze_anthropic(b64, ts):
    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("PET_VISION_MODEL", "claude-opus-4-8")
    resp = client.messages.create(
        model=model, max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": _PROMPT},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}})
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return _parse_json(text)


# 总结搜到的材料时用的提示词。**约束写得很死是有意的**：2026-07-30 实测过，模型在没有真实
# 材料时会编得极其自信——同一个问题给出过 7 / 4~5 / 12 / 6 / 26 五个互相矛盾的数字，还伪造过
# 游民星空、3DM 的链接和"原文引述"。所以这里只准它用我们递过去的文字，宁可说不知道。
_ANSWER_PROMPT = """你是恐怖游戏主播的桌宠助手，主播正卡关，需要你把找到的攻略讲给他听。

【铁律】
1. 只能使用下面【攻略材料】里出现的信息。材料里没写的，一个字都不许编。
2. 材料如果答不上主播的问题，就直说"这几篇攻略里没讲到这个"，然后说说材料里相关的部分。
3. 不许编造链接、不许编造"某某攻略里写道"这种引述。
4. 用口语说，因为这段话会被念出来。控制在 3 句以内，要具体可操作（说清去哪、做什么）。
5. 不要出现"玩家""游戏"这类词，用"你"称呼主播。

【主播在玩】{game}
【主播说他卡在】{note}
【当前画面】{scene}

【攻略材料】
{material}

现在用魔丸的口吻（活泼、亲昵、叫主播"主播"）讲给他听："""


def _text_call(prompt, max_tokens=500):
    """纯文本调用，复用视觉后端的 key/base_url（同一家、同一个 key，不额外配置）。"""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("PET_VISION_API_KEY"),
                    base_url=os.environ.get("PET_VISION_BASE_URL"))
    model = os.environ.get("PET_VISION_MODEL", "qwen-vl-max")
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    return (resp.choices[0].message.content or "").strip()


def walkthrough(image_bytes, game=None, note=None, ts=0, searcher=None, analyzer=None):
    """卡关攻略的完整流程：看画面 → 联网找攻略 → **只拿搜到的真材料**总结。

    searcher / analyzer 可注入，方便离线测试（不联网、不调模型也能跑通逻辑）。

    搜不到就如实说搜不到，绝不退回"让模型自由发挥"——那正是这条链路要解决的问题。
    """
    if searcher is None:
        import websearch
        searcher = websearch.gather
    do_analyze = analyzer or analyze

    # 看画面（4~6s）和联网搜（5~8s）**并行跑**：只要主播说了"卡在哪"或者认出了游戏名，
    # 搜索词就不依赖画面内容，没道理串着等。实测串行 18s，并行能省下其中一段。
    # 只有"既没游戏名又没补充"时才必须先看懂画面才知道搜什么，那种情况退回串行。
    if game or note:
        box = {}

        def _search_job():
            try:
                box['found'] = searcher(game=game, note=note, scene=None)
            except Exception:                    # noqa: BLE001
                box['found'] = {'results': [], 'pages': []}

        th = threading.Thread(target=_search_job, daemon=True)
        th.start()
        scene = do_analyze(image_bytes, ts)
        th.join(timeout=60)
        found = box.get('found') or {'results': [], 'pages': []}
    else:
        scene = do_analyze(image_bytes, ts)
        found = searcher(game=game, note=note, scene=scene["data"].get("summary", ""))

    summary = scene["data"].get("summary", "")

    results, pages = found.get("results", []), found.get("pages", [])
    if not results:
        return make_scene(
            summary, scene["data"].get("tags", []), True,
            "主播我没搜到这一关的攻略哎，网络可能不通，要不你说得再具体点我再找一次？", ts)

    # 材料 = 搜索摘要 + 抓到的正文。摘要本身信息量就不小（实测能直接带出"第一张碎片在
    # 同一个房间的储物柜内"这种句子），所以正文抓不到也不影响可用性。
    blocks = []
    for r in results[:5]:
        blocks.append(f"【来源】{r['title']}\n{r.get('snippet', '')}")
    for p in pages[:2]:
        blocks.append(f"【正文·{p['title']}】\n{p['text'][:1800]}")
    material = "\n\n".join(blocks)[:8000]

    prompt = _ANSWER_PROMPT.format(
        game=game or "（没认出来）", note=note or "（没说，看画面判断）",
        scene=summary or "（没看清）", material=material)
    try:
        hint = _text_call(prompt)
    except Exception as e:                       # noqa: BLE001
        hint = f"我搜到几篇攻略，但整理的时候出岔子了（{type(e).__name__}），链接发你控制台了"
    sources = [{"title": r["title"], "url": r["url"]} for r in results[:5]]
    return make_scene(summary, scene["data"].get("tags", []), True, hint, ts, sources=sources)


def analyze(image_bytes, ts=0):
    """游戏画面 → 多模态大模型 → game.scene。后端按环境变量自动选（国产优先）。
    没配置任何视觉后端时，不抛异常打断调用方——返回一句角色口吻提示，让桌宠直接
    在屏幕上说出来，而不是只在你看不到的终端里打印一行错误。"""
    try:
        provider = _pick_provider()
    except RuntimeError:
        return make_scene(
            "视觉功能还没配置好，暂时看不懂攻略画面。", ["unconfigured"], True,
            "主播还没给我配视觉功能的钥匙（API key），去 .env 配一下我才能看攻略哦～", ts,
        )
    b64 = base64.standard_b64encode(_maybe_downscale(image_bytes)).decode("utf-8")
    d = _analyze_openai(b64, ts) if provider == "openai" else _analyze_anthropic(b64, ts)
    return make_scene(d.get("summary", ""), d.get("tags", []), bool(d.get("stuck")), d.get("hint"), ts)
