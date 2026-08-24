#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从备份的 agent 会话记录（.jsonl）里挖出"人说过什么"，用于窗口丢失后恢复认知。

## 为什么有这个脚本

2026-08-10 主播的对话窗口全丢了（只剩 jsonl 备份），恢复项目认知花掉了一整个窗口：
64MB 记录没法直接读，得先想清楚"读哪一部分才有信息量"。答案是：

- **人的原话**（`origin.kind == "human"`）—— 需求、纠正、拍板、抱怨全在这里，
  而 AI 的回复大多是过程性的、且结论早就沉淀进 HANDOFF/CHANGELOG 了。
- **每个窗口最后几条 AI 回复** —— "上次停在哪"只能从这里看出来，
  尤其是那些**只在对话里说过、从没写进仓库**的东西（08-04 修正后的开播顺序就是这么找回来的）。

所以这个脚本只提这两样，把 64MB 压成几十 KB。**下次恢复应该是十分钟，不是一整个窗口。**

## 用法

    python scripts/extract_session_history.py <记录目录> [-o 输出目录] [--tail N]

    # 例（2026-08-10 那次用的就是这个）
    python scripts/extract_session_history.py "D:\\CodexWindowFiles\\直播桌宠-逐窗口原文恢复-2026-08-10\\原始会话"

产物：
    _index.md            全部窗口一览（按时间排序），先读这个挑窗口
    human_<窗口>.txt     该窗口里人的原话，带时间戳
    tail_<窗口>.txt      该窗口最后 N 条 AI 回复（默认 8）

纯标准库，不装任何依赖——恢复认知的工具本身不该有装不上的风险。
"""

import argparse
import glob
import io
import json
import os


def _text_of(message):
    """message.content 可能是字符串，也可能是 [{type:'text',...}, {type:'tool_use',...}] 块列表。"""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def scan(path, tail):
    """读一个 jsonl，返回该窗口的摘要 + 人的原话 + 尾部 AI 回复。"""
    cwds, branches, stamps = set(), set(), []
    humans, assistants = [], []

    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # 备份可能被截断，跳过坏行而不是整个放弃

            stamp = rec.get("timestamp")
            if stamp:
                stamps.append(stamp)
            if rec.get("cwd"):
                cwds.add(rec["cwd"])
            if rec.get("gitBranch"):
                branches.add(rec["gitBranch"])

            kind = rec.get("type")
            # 只要真人打的字。工具返回结果也走 type=="user"，靠 origin.kind 区分。
            if kind == "user" and (rec.get("origin") or {}).get("kind") == "human":
                text = _text_of(rec.get("message", {})).strip()
                if text:
                    humans.append((stamp, text))
            elif kind == "assistant":
                text = _text_of(rec.get("message", {})).strip()
                if text:
                    assistants.append((stamp, text))

    stamps.sort()
    return {
        "file": os.path.basename(path),
        "id": os.path.basename(path)[:8],
        "start": stamps[0] if stamps else "",
        "end": stamps[-1] if stamps else "",
        "cwds": sorted(cwds),
        "branches": sorted(branches),
        "humans": humans,
        "tail": assistants[-tail:],
        "n_assistant": len(assistants),
    }


def write_dump(out_dir, name, rows, clip=None):
    with io.open(os.path.join(out_dir, name), "w", encoding="utf-8") as w:
        for stamp, text in rows:
            w.write("\n===== [%s] =====\n%s\n" % (stamp, text[:clip] if clip else text))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", help="存放 .jsonl 会话备份的目录")
    ap.add_argument("-o", "--out", default="session-history", help="输出目录（默认 ./session-history）")
    ap.add_argument("--tail", type=int, default=8, help="每个窗口保留末尾几条 AI 回复（默认 8）")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.src, "*.jsonl")))
    if not files:
        raise SystemExit("在 %s 里没找到任何 .jsonl" % args.src)

    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    windows = [scan(p, args.tail) for p in files]
    # 按结束时间排序：最后一个窗口就是"上次停在哪"，应该最先被读到。
    windows.sort(key=lambda w: w["end"])

    lines = [
        "# 会话记录一览",
        "",
        "来源：`%s`" % args.src,
        "",
        "**按结束时间升序——最下面那个窗口是最后停的地方，先读它的 `tail_*.txt`。**",
        "",
        "| 窗口 | 起 | 止 | 工作目录 | 分支 | 人的原话 | AI 回复 |",
        "|---|---|---|---|---|---|---|",
    ]
    for w in windows:
        lines.append(
            "| `%s` | %s | %s | %s | %s | %d | %d |"
            % (
                w["id"],
                w["start"][:16].replace("T", " "),
                w["end"][:16].replace("T", " "),
                "<br>".join(os.path.basename(c.rstrip("\\/")) for c in w["cwds"]) or "-",
                "<br>".join(w["branches"]) or "-",
                len(w["humans"]),
                w["n_assistant"],
            )
        )
        write_dump(args.out, "human_%s.txt" % w["id"], w["humans"])
        # 尾部回复单条可能极长，截断，够看出"停在哪"就行
        write_dump(args.out, "tail_%s.txt" % w["id"], w["tail"], clip=6000)

    lines += [
        "",
        "## 怎么用",
        "",
        "1. 先读**最后一个窗口**的 `tail_*.txt` —— 上次停在哪、有没有活干到一半。",
        "2. 再按需读 `human_*.txt` —— 人的需求/纠正/拍板原话，AI 的过程叙述已被过滤掉。",
        "3. 拿它跟仓库现状对账：`git log`、文档 mtime、`.cache/` 产物。",
        "   **对话里说过但仓库里没有的东西，就是真正会丢的东西**——找到就补进 HANDOFF。",
        "",
    ]
    with io.open(os.path.join(args.out, "_index.md"), "w", encoding="utf-8") as w:
        w.write("\n".join(lines))

    print("%d 个窗口 -> %s" % (len(windows), os.path.abspath(args.out)))
    print("先读: %s" % os.path.join(args.out, "_index.md"))
    print("上次停在: 窗口 %s (%s)" % (windows[-1]["id"], windows[-1]["end"][:16].replace("T", " ")))


if __name__ == "__main__":
    main()
