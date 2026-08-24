#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境守护：把 DouyinBarrageGrab 留下的「机器级根证书」和「系统代理设置」还原干净。

为什么必须有这个东西（都是读它源码查实的，不是猜的）：
  * 它调 `TrustRootCertificate(true)` 把自签根证书装进 **LocalMachine\\Root**——影响这台
    电脑上所有 Windows 账户，且要管理员权限。
  * 证书有效期设的是 `CertificateValidDays = 365 * 10`，**十年**。
  * 它的代码里**根本没有卸载实现**，Dispose() 只停代理不删证书。装了就永远留着。
  * 它靠改注册表里的系统代理干活，异常退出时设置可能残留，表现是"下播后整机上不了网"。

安全设计（这里的每一条都是为了"绝不误删别人的证书"）：
  1. **不靠名字匹配**。开播前先给根证书存拍一张快照；只有"这次新出现的"才进候选。
  2. 候选还要再过一道主体名特征（默认 titanium，这个库的默认根证书名）。**两个条件同时满足
     才删**；只满足其一的一律不动，只在报告里列出来等人确认。
  3. 自愈：如果上一次没清理干净（比如断电、进程被强杀），下次 arm 时先拿旧快照补做清理。
  4. `--dry-run` 只说要做什么、不真做。

用法：
    python guard.py arm            # 开播前：拍快照
    python guard.py cleanup        # 下播后：还原证书与代理
    python guard.py status         # 看当前状态
    加 --dry-run 可空跑
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
STATE_PATH = os.path.join(REPO, ".cache", "env-guard.json")
LOG_PATH = os.path.join(REPO, ".cache", "env-guard.log")

# 候选证书还必须命中这里的特征才会被删除（小写子串匹配 Subject）
SUSPECT_PATTERNS = ("titanium",)

# 只有当前系统代理**确实指向抓包程序**时才还原快照。抓包程序默认 proxyPort=8827，
# 改了它的配置就要同步改这里。
# 为什么要这道判断：本机平时挂着 Shadowsocks（ProxyServer=localhost:1080）。若在 SS
# 开着时拍快照、之后关掉 SS 再开播，下播时无脑按快照还原，就会把代理指回一个没在运行的
# 端口——表现是"下播后整机上不了网"，而且极难联想到是桌宠干的。
PROXY_MARKERS = (":8827",)

PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_VALUES = ("ProxyEnable", "ProxyServer", "ProxyOverride")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---- 根证书存（可注入，便于离线测试）----
def read_root_store():
    """返回 {thumbprint: subject}。读不到就返回 None——**读不到时绝不做任何删除**。"""
    ps = ("Get-ChildItem Cert:\\LocalMachine\\Root | "
          "Select-Object Thumbprint,Subject | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0 or not out.stdout.strip():
            log(f"读取根证书存失败：rc={out.returncode} {out.stderr.strip()[:200]}")
            return None
        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        return {d["Thumbprint"]: (d.get("Subject") or "") for d in data if d.get("Thumbprint")}
    except Exception as e:
        log(f"读取根证书存异常：{type(e).__name__}: {e}")
        return None


def delete_cert(thumbprint):
    ps = f"Remove-Item -Path 'Cert:\\LocalMachine\\Root\\{thumbprint}' -Force"
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                         capture_output=True, text=True, timeout=60)
    return out.returncode == 0, (out.stderr or "").strip()[:300]


# ---- 系统代理设置 ----
def read_proxy():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_KEY) as k:
            snap = {}
            for name in PROXY_VALUES:
                try:
                    v, t = winreg.QueryValueEx(k, name)
                    snap[name] = {"value": v, "type": t}
                except FileNotFoundError:
                    snap[name] = None
            return snap
    except Exception as e:
        log(f"读取代理设置失败：{type(e).__name__}: {e}")
        return None


def write_proxy(snap):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_KEY, 0, winreg.KEY_SET_VALUE) as k:
            for name, item in snap.items():
                if item is None:
                    try:
                        winreg.DeleteValue(k, name)
                    except FileNotFoundError:
                        pass
                else:
                    winreg.SetValueEx(k, name, 0, item["type"], item["value"])
        return True
    except Exception as e:
        log(f"还原代理设置失败：{type(e).__name__}: {e}")
        return False


# ---- 纯逻辑：决定删哪些（单独抽出来，方便离线测试）----
def _proxy_desc(snap):
    if not snap:
        return "读不到"
    on = ((snap.get("ProxyEnable") or {}).get("value"))
    srv = ((snap.get("ProxyServer") or {}).get("value")) or "—"
    return f"ProxyEnable={on} ProxyServer={srv}"


def _proxy_enabled(snap):
    return bool(((snap or {}).get("ProxyEnable") or {}).get("value"))


def _proxy_server(snap):
    return str((((snap or {}).get("ProxyServer") or {}) or {}).get("value") or "")


def should_restore_proxy(current, baseline=None, markers=PROXY_MARKERS):
    """两种情况才还原，其余一律不动（那是用户自己的设置）：

    1) 当前代理指向抓包程序——它还在跑，或异常退出没清干净。
    2) 基线本来开着代理，而当前被关掉且清空了。**这是实测出来的**：抓包程序退出时
       它的 Watchdog 不是"还原成用户原来的值"，而是直接 ProxyEnable=0 + 清空
       ProxyServer，把本机原有的 Shadowsocks 设置一起抹掉了。
    """
    if current is None:
        return False
    if any(m in _proxy_server(current) for m in markers):
        return True
    if baseline is not None and _proxy_enabled(baseline) \
            and not _proxy_enabled(current) and not _proxy_server(current):
        return True
    return False


def proxy_target_alive(snap, timeout=1.0):
    """基线代理指向的端口现在有没有人在听。

    还原到一个没人监听的代理 = 整机上不了网，而且现象跟"宽带坏了"一模一样，极难联想到
    是桌宠干的。所以写回去之前必须先确认目标活着。
    """
    import socket
    srv = _proxy_server(snap)
    if not _proxy_enabled(snap) or not srv:
        return True                      # 不启用代理就无所谓死活
    host, _, port = srv.rpartition(":")
    host = (host or "127.0.0.1").replace("localhost", "127.0.0.1")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def decide(baseline_certs, current_certs, patterns=SUSPECT_PATTERNS):
    """返回 (要删的 [(thumb,subject)], 只是新增但不匹配特征的 [(thumb,subject)])。"""
    new = {t: s for t, s in current_certs.items() if t not in baseline_certs}
    doomed, unknown = [], []
    for t, s in sorted(new.items()):
        (doomed if any(p in (s or "").lower() for p in patterns) else unknown).append((t, s))
    return doomed, unknown


# ---- 状态文件 ----
def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---- 动作 ----
def cmd_cleanup(dry_run=False, state=None):
    state = state or load_state()
    if not state:
        log("没有快照，跳过清理（说明这台机器还没 arm 过）")
        return 0
    current = read_root_store()
    if current is None:
        log("⚠ 读不到根证书存，本次不做任何删除（宁可不删也不误删）")
        return 1
    doomed, unknown = decide(state.get("certs") or {}, current)
    for t, s in unknown:
        log(f"⚠ 新增但特征不符，**不动**，请人工确认：{t}  {s}")
    rc = 0
    for t, s in doomed:
        if dry_run:
            log(f"[dry-run] 将删除证书 {t}  {s}")
            continue
        ok, err = delete_cert(t)
        log(f"{'✔ 已删除' if ok else '✘ 删除失败'} 证书 {t}  {s}" + (f"  {err}" if err else ""))
        if not ok:
            rc = 1
    if not doomed:
        log("没有需要删除的证书")
    proxy = state.get("proxy")
    if proxy:
        cur = read_proxy()
        if not should_restore_proxy(cur, proxy):
            log(f"当前系统代理不是抓包程序动过的（现在是 {_proxy_desc(cur)}），保持不动")
        elif not proxy_target_alive(proxy):
            log(f"⚠ 快照里的代理 {_proxy_server(proxy)} 现在没人监听，还原过去会导致整机上不了网，"
                f"**跳过**。等那个代理起来后再跑一次 cleanup 即可")
        elif dry_run:
            log(f"[dry-run] 将把系统代理还原为：{_proxy_desc(proxy)}")
        else:
            log("✔ 系统代理已还原" if write_proxy(proxy) else "✘ 系统代理还原失败")
    if not dry_run:
        state["state"] = "clean"
        state["cleaned_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
    return rc


def cmd_arm(dry_run=False):
    old = load_state()
    # 自愈：上次是 armed 状态说明没正常清理过（断电/被强杀），先补做
    if old and old.get("state") == "armed":
        log("发现上次未完成的清理，先补做")
        cmd_cleanup(dry_run=dry_run, state=old)
    certs = read_root_store()
    proxy = read_proxy()
    if certs is None:
        log("⚠ 读不到根证书存，无法建立基线；此时装证书将来就删不掉了，请以管理员身份重试")
        return 1
    state = {"state": "armed", "armed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "certs": certs, "proxy": proxy}
    if dry_run:
        log(f"[dry-run] 将记录 {len(certs)} 张根证书 + 代理设置作为基线")
        return 0
    save_state(state)
    log(f"✔ 已拍快照：根证书 {len(certs)} 张，代理设置 {'已记录' if proxy else '读取失败'}")
    return 0


def cmd_status():
    state = load_state()
    if not state:
        print("没有快照（还没 arm 过）")
        return 0
    print(f"状态: {state.get('state')}  拍摄于 {state.get('armed_at')}  "
          f"基线证书 {len(state.get('certs') or {})} 张")
    current = read_root_store()
    if current is not None:
        doomed, unknown = decide(state.get("certs") or {}, current)
        print(f"当前新增证书: 待删 {len(doomed)} 张, 需人工确认 {len(unknown)} 张")
        for t, s in doomed + unknown:
            print(f"  {t}  {s}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["arm", "cleanup", "status"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    return {"arm": lambda: cmd_arm(a.dry_run),
            "cleanup": lambda: cmd_cleanup(a.dry_run),
            "status": cmd_status}[a.action]()


if __name__ == "__main__":
    sys.exit(main())
