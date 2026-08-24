#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网找攻略：搜索 + 抓正文。纯标准库，不加依赖，不需要任何 API key。

**为什么要自己搜，而不是给模型开个"联网开关"**（2026-07-30 六次实测的结论，别再走回头路）：
同一个问题（某游戏谜题碎片几块）在各种"开搜索"的调法下得到六个互相矛盾的答案（7 / 4~5 / 12 /
6 / 7 / 26），没有一次能拿回真实来源。最危险的一次是给 DeepSeek 传了个它并不支持的
`web_search_options`——参数被**静默忽略**，模型却因此开口就说"根据我搜索到的信息"，编出三个
真实域名的链接还附上虚构的原文引述。直播时"看起来有据可查地说错"比"说不知道"糟得多。

所以这里的分工是死的：**搜索和抓正文由本模块做，模型只负责总结我们递给它的真实文字。**
模型手上没有编造的空间；搜不到就如实返回空，让上层说"没搜到"。

**为什么要开一个真实浏览器去搜，而不是发个 HTTP 请求**（2026-07-30 在"关掉代理"＝真实开播
环境下逐个实测，每一条都踩过）：

    裸 HTTP  百度        只返回 1438 字节桩页                        ❌
    裸 HTTP  搜狗        直接返回滑块验证码页                        ❌
    裸 HTTP  Bing 国内版 页面标题是对的，**结果内容却是别的查询的缓存**
                        （搜"层层恐惧攻略"返回蔚来招聘/日本地图/韩语 PDF 阅读器）❌
    裸 HTTP  DuckDuckGo  超时——之前"2 秒 9 条结果"是走了 Shadowsocks ❌
    无头 Chrome + 百度   撞上百度滑块验证码                          ❌
    **真实浏览器 + 百度  结果完全对题，还自带 B 站视频攻略**          ✅

⚠️ Bing 那条尤其阴险：页面能打开、标题正确、看着像成功，只有把结果内容读出来核对才发现是垃圾。
**判断一个搜索接口能不能用，必须核对"结果是否对题"，不能只看 HTTP 200 和页面长度。**

主播开播时会关掉 VPN/Shadowsocks，所以境外服务（含 Tavily/Serper 这类付费搜索 API）一律不可用，
方案必须国内直连可达。

**实现方式**：借桌宠自己的 Electron 跑一次 `electron . --search=<词>`，它内部开一个隐藏的
真实 Chromium 窗口（`show:false` 只是不显示，**不是无头**，行为跟正常浏览器一致），搜完把 JSON
打到 stdout 就退出。这样不必再装 Playwright（要往 C 盘下 150MB，本机 C 盘常年紧张），也不新增
常驻进程和监听端口。代价是每次搜索约 7 秒（Electron 冷启动 + 页面加载），对"卡关"场景可接受。

**撞上验证码就如实返回空**，绝不做任何绕过验证的事。
"""
import gzip
import io
import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
BING = 'https://cn.bing.com/search?q={q}&ensearch=0'

# 直连优先。**不要无脑走环境变量里的代理**：本机 HTTP(S)_PROXY 常年指着 Shadowsocks
# 127.0.0.1:1080，而开播时 SS 是关掉的——那时走代理等于连到一个死端口，整个功能静默失效。
# 目标站点都是国内的，直连本来就通。直连失败再退回系统代理（比如换了网络环境确实要走代理）。
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_VIA_PROXY = urllib.request.build_opener()


def _get(url, timeout=8):
    """取一个页面，返回 (bytes, 最终URL)。直连优先，失败退回系统代理。"""
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'Accept-Encoding': 'gzip',
    })
    last = None
    for opener in (_DIRECT, _VIA_PROXY):
        try:
            with opener.open(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw, r.geturl()
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            last = e
    raise last if last else RuntimeError('取页面失败')


def _decode(raw):
    """中文攻略站 GBK/UTF-8 混杂，按 meta 声明猜，猜不中就 utf-8 忽略错误。"""
    head = raw[:2048].decode('ascii', 'ignore').lower()
    m = re.search(r'charset=["\']?\s*([\w-]+)', head)
    enc = (m.group(1) if m else 'utf-8').lower()
    if enc in ('gb2312', 'gbk', 'gb-2312'):
        enc = 'gb18030'          # gb18030 是 gbk 超集，避免生僻字解不出来
    try:
        return raw.decode(enc, 'ignore')
    except LookupError:
        return raw.decode('utf-8', 'ignore')


def _strip_tags(html):
    t = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = (t.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<')
          .replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'"))
    t = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), t)
    return re.sub(r'\s+', ' ', t).strip()


def _electron():
    """桌宠自带的 Electron 可执行文件。找不到就返回 None，上层据此退化为"没搜到"。"""
    p = os.path.join(HERE, '..', '..', 'apps', 'character', 'node_modules',
                     'electron', 'dist', 'electron.exe')
    p = os.path.normpath(p)
    return p if os.path.exists(p) else None


def search(query, top=5, timeout=45):
    """开一个隐藏的真实浏览器搜一次。任何失败都返回空列表——上层据此如实说"没搜到"。

    stdout 的**最后一行**才是 JSON：Electron/Chromium 会往 stdout 混各种警告，不能整段解析。
    """
    exe = _electron()
    if not exe:
        return []
    app_dir = os.path.normpath(os.path.join(HERE, '..', '..', 'apps', 'character'))
    try:
        r = subprocess.run([exe, '.', f'--search={query}'], cwd=app_dir,
                           capture_output=True, timeout=timeout)
        lines = [ln for ln in r.stdout.decode('utf-8', 'ignore').splitlines() if ln.strip()]
        if not lines:
            return []
        data = json.loads(lines[-1])
        if data.get('error'):
            return []
        return (data.get('results') or [])[:top]
    except Exception:
        return []


def parse_bing(html, top=5):
    """从 Bing 结果页抽 (标题, 链接, 摘要)。单独拆出来是为了能用固定样本离线回归——
    搜索引擎改版是迟早的事，改版时该有测试立刻告诉我们，而不是等直播时才发现搜不出东西。"""
    out = []
    for block in re.findall(r'<li class="b_algo".*?(?=<li class="b_algo"|</ol>)', html, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url, title = m.group(1), _strip_tags(m.group(2))
        snippet = ''
        ms = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        if ms:
            snippet = _strip_tags(ms.group(1))
        if title and url.startswith('http'):
            out.append({'title': title, 'url': url, 'snippet': snippet})
        if len(out) >= top:
            break
    return out


def fetch_text(url, max_chars=3000, timeout=8):
    """抓一个页面的正文纯文本。失败返回空串。"""
    try:
        raw, _ = _get(url, timeout=timeout)
        return _strip_tags(_decode(raw))[:max_chars]
    except Exception:
        return ''


# 主播报游戏名的常见说法。**他说了就以他说的为准**——自动认窗口标题会遇到中英文名不一致、
# 标题带一堆版本号、或者前台压根不是游戏（模拟器、启动器）这些情况，本人说的最可靠。
_GAME_PATTERNS = (
    r'《([^》]{1,30})》',
    r'游戏名(?:是|叫|为)\s*([^，,。.；;！!？?\s]{2,20})',
    r'游戏(?:是|叫)\s*([^，,。.；;！!？?\s]{2,20})',
    r'(?:正在|我在|在)玩(?:的是)?\s*([^，,。.；;！!？?\s]{2,20})',
)
# "我在玩游戏"这种没信息量的话别当成游戏名
_GAME_STOPWORDS = {'游戏', '这个', '那个', '一个', '什么', '啥'}


def extract_game(text):
    """从主播的求助原话里认出游戏名。认不出返回 None，交给窗口标题兜底。"""
    t = (text or '').strip()
    if not t:
        return None
    for pat in _GAME_PATTERNS:
        m = re.search(pat, t)
        if m:
            name = m.group(1).strip(' 　的了呢啊')
            if name and name not in _GAME_STOPWORDS and len(name) >= 2:
                return name
    return None


def build_query(game=None, note=None, scene=None):
    """拼搜索词。

    实测（2026-07-30）直接搜「层层恐惧 攻略」，Bing 返回的是"层层"这个词的**汉语词典和百科**
    条目——所以游戏名要加书名号并显式带上"游戏"两个字，把词义锚定住。
    主播自己补充的那句（note）权重最高：只有他知道"第几关""我已经试过什么"。
    """
    parts = []
    if game:
        parts.append(f'《{game}》游戏')
    if note:
        parts.append(note.strip())
    elif scene:
        parts.append(scene.strip()[:40])
    parts.append('攻略 怎么过')
    return ' '.join(p for p in parts if p)


def gather(game=None, note=None, scene=None, pages=2, top=5):
    """完整的一次"找攻略"：搜 → 抓前几个页面的正文。

    返回 {'query':…, 'results':[…], 'pages':[{'title','url','text'}]}
    results 为空就是真的没搜到，上层必须如实说出来。
    """
    query = build_query(game, note, scene)
    results = search(query, top=top)
    pages_out = []
    for r in results[:pages]:
        text = fetch_text(r['url'])
        if len(text) > 200:                      # 太短多半是跳转页/反爬页，别喂给模型
            pages_out.append({'title': r['title'], 'url': r['url'], 'text': text})
    return {'query': query, 'results': results, 'pages': pages_out}


if __name__ == '__main__':
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    g = sys.argv[1] if len(sys.argv) > 1 else '层层恐惧'
    n = sys.argv[2] if len(sys.argv) > 2 else '画室 拼画 碎片'
    d = gather(game=g, note=n)
    print('查询词:', d['query'])
    for r in d['results']:
        print(f"  - {r['title'][:60]}\n      {r['url'][:90]}")
    print(f"抓到正文页 {len(d['pages'])} 个")
    for p in d['pages']:
        print(f"  [{p['title'][:40]}] 正文 {len(p['text'])} 字：{p['text'][:120]}…")
