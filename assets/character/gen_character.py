#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用国产文生图模型生成桌宠角色立绘。

配好 .env 里的 PET_IMAGE_*（可与视觉同一个 DashScope key，见 .env.example）后：
  python assets/character/gen_character.py candidates           # 同一姿势(idle)按 STYLES 出多版风格候选，供人挑方向
  python assets/character/gen_character.py all --style=A_anime  # 方向定了之后，生成 action-map 需要的全套状态插画
  python assets/character/gen_character.py idle --style=A_anime  # 只生成某一个状态

默认模型 wan2.2-t2i-flash（通义万相），走 DashScope 原生 image-synthesis 异步任务接口（提交任务→轮询结果）——
它不是 OpenAI images.generate：DashScope 的 OpenAI 兼容端点不支持万相系列，直接调会 404。
若把 PET_IMAGE_MODEL 换成 cogview-3-plus 等 OpenAI 兼容图像模型，会自动走 images.generate 老路径。
输出到 assets/character/*.png（已 .gitignore）。生成后交给表现层按状态切图渲染。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# 好看、古灵精怪、女性、2D 插画角色（恐怖游戏直播吉祥物，暗黑可爱基调）；
# 具体画风/头身比例交给 STYLES 决定，方便同一姿势出多版候选给人挑方向。
BASE_DESC = ("好看的古灵精怪女性角色，恐怖游戏直播虚拟吉祥物，暗黑可爱(spooky-cute)基调，"
             "大眼睛神态灵动俏皮、略带小恶魔式狡黠，2D动漫插画风格，全身正面立绘，干净上色，"
             "纯白色背景，健康非性感、吉祥物风格")

# 风格候选方向：同一姿势换风格描述，生成几版供人挑一个方向（脚本不自动选）。
STYLES = {
    "A_anime": "日系动漫赛璐璐上色风格，线条干净利落，色彩鲜明饱和，标准偏可爱头身比例(约5-6头身)",
    "B_painterly": "唯美厚涂插画风格，柔和光影层次细腻，接近游戏立绘/画师原画质感，匀称头身比例(约6-7头身)",
    "C_gothic_chibi": "暗黑哥特可爱Q版风格，头身比例约3头身，紫黑撞色配荧光紫/幽绿点缀，"
                       "蕾丝、蝙蝠、小幽灵等哥特小物件点缀，古灵精怪氛围浓郁",
}
DEFAULT_STYLE = "A_anime"  # 方向定了之后改这里，或用 --style= 指定

# action-map.js 需要的状态：9 个动作(play_motion，内置各自默认表情) +
# 3 个独立表情(set_expression 在 idle 姿势上单独切表情、动作不变时用；
# 其余表情已经随对应动作出现：neutral=idle，scared=scared，blush=beg，sleepy=sleep)。
STATES = {
    "idle": "自然站立，双手交叠身前，嘴角轻轻上扬，眼神灵动带点狡黠",
    "wave": "侧身抬手挥手打招呼，笑眼弯弯，元气满满",
    "scared": "被突然吓到，双手护胸或半遮脸，身体后仰，表情夸张滑稽的害怕，额角冒一滴冷汗",
    "thank_small": "微微鞠躬点头致谢，双手轻叠腹前，笑容温柔",
    "thank_big": "深深弯腰鞠躬，双手压裙摆或交叠致谢，表情感激又浮夸",
    "laugh": "开怀大笑，身体微微后仰，眼睛笑成弯月，双手扶膝或叉腰",
    "praise": "双手举起比心或欢呼庆祝，眼睛闪着星星般的光，得意又可爱",
    "beg": "双手合十歪头卖萌，俏皮 wink，眼角冒出爱心",
    "sleep": "闭眼打盹，头微微歪靠一侧，表情安稳香甜，头顶漂浮着几个 Zzz 符号",
    "idle_happy": "自然站姿，开心大笑表情，笑眼弯弯，两颊泛红晕",
    "idle_smug": "自然站姿，得意看好戏的坏笑表情，嘴角上扬、单边眉挑起",
    "idle_surprised": "自然站姿，惊讶表情，眼睛瞪大、嘴巴微张成 O 形",
}

NEGATIVE_PROMPT = ("文字, 水印, 签名, logo, 边框, 多人, 拼图分格, 裁切不全, 模糊, 变形, "
                    "多余肢体, 手指错误, 低质量, 写实真人, 性暗示, 暴露, 背景渐变, 背景纹理, 背景杂色")

# 桌宠窗口本身透明置顶，插画必须是透明背景，不能带纯色背景块。文生图模型画不出真透明，
# 只能画纯色背景再抠图——这里用 floodfill 从四边向内填充同色区域转 alpha，对干净的纯白背景效果好，
# 画风越"厚涂"/背景越不平整，抠图效果越差，抠完务必肉眼检查边缘。
BG_KEY_THRESH = 24


def _remove_background(path, thresh=BG_KEY_THRESH):
    from PIL import Image, ImageDraw
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
             (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for xy in seeds:
        try:
            ImageDraw.floodfill(img, xy, (0, 0, 0, 0), thresh=thresh)
        except Exception:
            pass
    img.save(path)

DASHSCOPE_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{}"


def _load_dotenv():
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _api_key():
    return os.environ.get("PET_IMAGE_API_KEY") or os.environ.get("PET_VISION_API_KEY")


def _dashscope_request(url, payload=None, extra_headers=None):
    headers = {"Authorization": f"Bearer {_api_key()}"}
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gen_dashscope_wanxiang(model, prompt, out_path, negative_prompt, size):
    payload = {
        "model": model,
        "input": {"prompt": prompt, "negative_prompt": negative_prompt},
        "parameters": {"size": size, "n": 1, "watermark": False},
    }
    try:
        submitted = _dashscope_request(DASHSCOPE_SUBMIT_URL, payload, {"X-DashScope-Async": "enable"})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        if size != "1024*1024" and "size" in body.lower():
            return _gen_dashscope_wanxiang(model, prompt, out_path, negative_prompt, "1024*1024")
        raise RuntimeError(f"提交生图任务失败 HTTP {e.code}: {body}")
    task_id = submitted.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"提交生图任务未返回 task_id: {submitted}")
    for _ in range(60):  # 最长等约 2 分钟
        time.sleep(2)
        result = _dashscope_request(DASHSCOPE_TASK_URL.format(task_id))
        status = result.get("output", {}).get("task_status")
        if status == "SUCCEEDED":
            results = result["output"].get("results") or []
            if not results or not results[0].get("url"):
                raise RuntimeError(f"任务成功但没有图片结果: {result}")
            urllib.request.urlretrieve(results[0]["url"], out_path)
            return out_path
        if status in ("FAILED", "UNKNOWN"):
            raise RuntimeError(f"生图任务失败: {result}")
    raise TimeoutError(f"等待生图任务超时: task_id={task_id}")


def _gen_openai_compatible(model, prompt, out_path):
    """非万相模型(如智谱 cogview-3-plus)走 OpenAI 兼容 images.generate。"""
    from openai import OpenAI
    client = OpenAI(api_key=_api_key(),
                     base_url=os.environ.get("PET_IMAGE_BASE_URL") or os.environ.get("PET_VISION_BASE_URL"))
    r = client.images.generate(model=model, prompt=prompt, size="1024x1024", n=1)
    d = r.data[0]
    if getattr(d, "b64_json", None):
        import base64
        open(out_path, "wb").write(base64.b64decode(d.b64_json))
    else:
        urllib.request.urlretrieve(d.url, out_path)
    return out_path


def gen(prompt, out_path, negative_prompt=NEGATIVE_PROMPT, size=None):
    model = os.environ.get("PET_IMAGE_MODEL", "wan2.2-t2i-flash")
    if model.startswith("wan"):
        result = _gen_dashscope_wanxiang(model, prompt, out_path, negative_prompt,
                                          size or os.environ.get("PET_IMAGE_SIZE", "864*1152"))
    else:
        result = _gen_openai_compatible(model, prompt, out_path)
    _remove_background(result)
    return result


def _parse_args(argv):
    style = DEFAULT_STYLE
    positional = []
    for a in argv:
        if a.startswith("--style="):
            style = a.split("=", 1)[1]
        else:
            positional.append(a)
    return (positional[0] if positional else "idle"), style


if __name__ == "__main__":
    _load_dotenv()
    which, style = _parse_args(sys.argv[1:])

    if which == "candidates":
        for style_key, style_desc in STYLES.items():
            out = os.path.join(HERE, f"candidate_idle__{style_key}.png")
            prompt = f"{BASE_DESC}，{style_desc}。人物动作与表情：{STATES['idle']}。"
            try:
                print("生成候选", style_key, "→", gen(prompt, out))
            except Exception as e:
                print(f"[失败] {style_key}: {e}")
        sys.exit(0)

    if style not in STYLES:
        print(f"未知风格 {style}，可选：{list(STYLES)}")
        sys.exit(1)

    todo = STATES if which == "all" else {which: STATES.get(which)}
    if any(v is None for v in todo.values()):
        print(f"未知状态 {which}，可选：{list(STATES)}")
        sys.exit(1)

    for name, desc in todo.items():
        out = os.path.join(HERE, f"{name}.png")
        prompt = f"{BASE_DESC}，{STYLES[style]}。人物动作与表情：{desc}。"
        try:
            print("生成", name, "→", gen(prompt, out))
        except Exception as e:
            print(f"[失败] {name}: {e}（是否配置 PET_IMAGE_* / 该 key 是否已开通对应图像模型服务?）")
