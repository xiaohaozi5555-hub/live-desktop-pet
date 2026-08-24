#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境守护离线验证：把"决定删哪张证书"的逻辑按死。

这段逻辑一旦出错，后果是**误删用户本来就有的根证书**（可能导致某些网站/软件无法连接），
所以这里的用例都往"会不会多删"的方向压，而不是只测正常路径。
全程不碰真实证书存和注册表：证书存的读写、代理的读写都被替换成假的。

运行: python verify_env_guard.py   退出码 0=全过。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import guard  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


TITAN = "CN=Titanium Root Certificate Authority, O=Titanium"

# ---- 1) decide()：只删"新增 且 特征匹配"的 ----
base = {"AAA": "CN=Microsoft Root CA", "BBB": TITAN}
cur = {"AAA": "CN=Microsoft Root CA", "BBB": TITAN,
       "CCC": TITAN, "DDD": "CN=Some Other Root CA"}
doomed, unknown = guard.decide(base, cur)
check("只删新增的 titanium 证书", [t for t, _ in doomed] == ["CCC"], str(doomed))
check("基线里本来就有的 titanium 绝不删（哪怕特征匹配）", "BBB" not in [t for t, _ in doomed])
check("新增但特征不符的不删，只列为待确认", [t for t, _ in unknown] == ["DDD"], str(unknown))

check("没有新增时不删任何东西", guard.decide(base, base) == ([], []))
check("空基线空现状不炸", guard.decide({}, {}) == ([], []))
check("Subject 为 None 不炸且不删", guard.decide({}, {"E": None}) == ([], [("E", None)]))
check("特征匹配大小写不敏感",
      guard.decide({}, {"F": "cn=TITANIUM ROOT"})[0] == [("F", "cn=TITANIUM ROOT")])
check("基线证书被用户手动删掉了也不会误判成新增", guard.decide({"A": TITAN, "B": TITAN}, {"A": TITAN}) == ([], []))

# ---- 1b) 系统代理只还原"我们造成的改动" ----
SS = {"ProxyEnable": {"value": 1, "type": 4}, "ProxyServer": {"value": "localhost:1080", "type": 1}}
GRAB = {"ProxyEnable": {"value": 1, "type": 4}, "ProxyServer": {"value": "127.0.0.1:8827", "type": 1}}
OFF = {"ProxyEnable": {"value": 0, "type": 4}, "ProxyServer": {"value": "", "type": 1}}
check("当前代理指向抓包程序 → 还原", guard.should_restore_proxy(GRAB, SS) is True)
check("当前代理是用户自己的 Shadowsocks → 不动", guard.should_restore_proxy(SS, SS) is False)
check("读不到当前代理 → 不动", guard.should_restore_proxy(None, SS) is False)
# 实测行为：抓包程序退出时 Watchdog 会把代理整个关掉+清空，而不是还原成用户原来的值
check("基线开着、当前被抹平 → 还原（抓包程序退出时的真实行为）", guard.should_restore_proxy(OFF, SS) is True)
check("基线本来就没开代理、当前也没开 → 不动", guard.should_restore_proxy(OFF, OFF) is False)
check("没给基线时退化为只认 8827", guard.should_restore_proxy(OFF) is False)

# 还原前必须确认目标端口活着，否则等于把用户网络掐断
check("基线代理端口没人监听 → 判定为不可还原",
      guard.proxy_target_alive({"ProxyEnable": {"value": 1, "type": 4},
                               "ProxyServer": {"value": "127.0.0.1:1", "type": 1}}) is False)
check("基线未启用代理 → 无所谓死活", guard.proxy_target_alive(OFF) is True)

# ---- 2) 读不到证书存时，绝不删 ----
tmp = tempfile.mkdtemp()
guard.STATE_PATH = os.path.join(tmp, "env-guard.json")
guard.LOG_PATH = os.path.join(tmp, "env-guard.log")

deleted = []
written = []
guard.delete_cert = lambda t: (deleted.append(t), (True, ""))[1]
guard.write_proxy = lambda snap: (written.append(snap), True)[1]
live = {"proxy": GRAB, "alive": True}        # 假装"当前"的系统代理与目标端口存活状态
guard.read_proxy = lambda: live["proxy"]
guard.proxy_target_alive = lambda snap, timeout=1.0: live["alive"]   # 上面已用真实实现测过

guard.read_root_store = lambda: None
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": None})
rc = guard.cmd_cleanup()
check("读不到证书存时不删任何证书", deleted == [] and rc == 1)
check("读不到证书存时状态保持 armed（下次还会重试）", guard.load_state()["state"] == "armed")

# ---- 3) 正常清理路径 ----
deleted.clear(); written.clear()
guard.read_root_store = lambda: {"AAA": "x", "NEW": TITAN, "OTHER": "CN=别人的根证书"}
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": SS})
live["proxy"] = GRAB                          # 抓包程序把代理改成了自己的
rc = guard.cmd_cleanup()
check("正常清理只删掉新增的 titanium", deleted == ["NEW"], str(deleted))
check("别人的新增证书没被碰", "OTHER" not in deleted)
check("代理被抓包程序改过 → 还原成快照里的原值", written == [SS], str(written))
check("清理后状态置为 clean", guard.load_state()["state"] == "clean")

# 抓包程序退出时把代理抹平了（实测行为）→ 应当还原成快照
deleted.clear(); written.clear()
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": SS})
live["proxy"] = OFF
guard.cmd_cleanup()
check("代理被抹平 → 还原回快照里的原值", written == [SS], str(written))

# 但如果快照里的代理现在没人监听，还原过去等于断网 → 宁可不动
deleted.clear(); written.clear()
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": SS})
live["proxy"] = OFF; live["alive"] = False
guard.cmd_cleanup()
check("快照代理已死 → 一个字都不写（避免还原后整机断网）", written == [], str(written))
live["alive"] = True

# 用户自己改成了别的代理 → 不能覆盖
deleted.clear(); written.clear()
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": SS})
live["proxy"] = {"ProxyEnable": {"value": 1, "type": 4}, "ProxyServer": {"value": "127.0.0.1:7890", "type": 1}}
guard.cmd_cleanup()
check("用户换成了别的代理 → 保持不动", written == [], str(written))

# ---- 4) dry-run 不产生任何真实副作用 ----
deleted.clear()
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": None})
guard.cmd_cleanup(dry_run=True)
check("dry-run 不真的删证书", deleted == [])
check("dry-run 不修改状态文件", guard.load_state()["state"] == "armed")

# ---- 5) 自愈：上次没清理干净，下次 arm 时补做 ----
deleted.clear()
guard.read_proxy = lambda: {"ProxyEnable": {"value": 0, "type": 4}}
guard.read_root_store = lambda: {"AAA": "x", "LEFTOVER": TITAN}
guard.save_state({"state": "armed", "certs": {"AAA": "x"}, "proxy": None})
guard.cmd_arm()
check("arm 时自动补做上次遗漏的清理", deleted == ["LEFTOVER"], str(deleted))
check("补做后重新拍了新快照且状态为 armed", guard.load_state()["state"] == "armed")
check("新快照包含当前全部证书", set(guard.load_state()["certs"]) == {"AAA", "LEFTOVER"})

# ---- 6) 上次是 clean 时不重复清理 ----
deleted.clear()
guard.read_root_store = lambda: {"AAA": "x"}
guard.save_state({"state": "clean", "certs": {"AAA": "x"}, "proxy": None})
guard.cmd_arm()
check("上次已 clean 时 arm 不做多余清理", deleted == [])

print(f"\n==== 环境守护离线验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
