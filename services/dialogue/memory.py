#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三层记忆里的第 3 层：观众画像表（SQLite，跨场持久化，不需要 FTS5/向量检索）。
单次查询/写入都是毫秒级同步操作，量级小用不上后台异步预取。见 SPEC.md。"""
import os
import sqlite3

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dialogue_memory.db')

# ---- 淘汰阈值（主播定的规则，可调）----
# 「超过 10 场没来就忘掉」。**按场次算而不是按天数算**：主播不是每天开播，休息两周再回来，
# 按日期判定会把所有人一次性判成"很久没来"全部清空——那不是"没来"，是主播自己没播。
MAX_ABSENT_SESSIONS = 10
# 「最多只记 30 人」。观众画像是给 LLM 当上下文用的，人数无上限地涨下去，库会变大、
# 查询也会慢，而真正认得出的老面孔本来就是几十人量级。
MAX_VIEWERS = 30

_SESSION_KEY = 'session_no'

_SCHEMA = """
CREATE TABLE IF NOT EXISTS viewers (
  nickname TEXT PRIMARY KEY,
  tier TEXT DEFAULT 'normal',   -- normal / member / star_guardian；只由 set_tier() 写
                                 -- （主播在 control-panel 打标，或 perception-danmaku 依据
                                 -- 粉丝团等级自动判定，都经 command.set_viewer_tier 传入），
                                 -- upsert_viewer() 不碰这个字段。
  tier_source TEXT DEFAULT 'manual',      -- manual / auto。**手动优先**：自动判定不会覆盖
                                 -- 主播亲手打过的标（主播看得见弹幕之外的东西，机器不该推翻他）。
  gift_total_session INTEGER DEFAULT 0,   -- 本场送礼累计，stream_end 时归零
  last_seen_ts INTEGER,
  note TEXT,                    -- 只由规则/代码拼装结构化内容写入（stream_end 常客判定），不让
                                 -- LLM 自由写（弹幕是不可信输入）
  sessions_seen INTEGER DEFAULT 0,        -- 跨场累计出现次数，不随 stream_end 清零
  gift_total_lifetime INTEGER DEFAULT 0,  -- 跨场送礼累计，不随 stream_end 清零
  last_seen_session INTEGER DEFAULT 0     -- 最后一次见到这人是第几场（配 meta.session_no 用）。
                                 -- 跟 last_seen_ts 并存而不是替代它：时间戳还要给"同人冷却"
                                 -- 之类的实时逻辑用，场次号只服务于跨场淘汰。
)
"""

_SCHEMA_META = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
)
"""


def connect(db_path=None):
    """建连接（含建表）。同一 Dialogue 实例应复用同一个连接以保证同进程内数据一致
    （尤其 db_path=':memory:' 时，每次新连接都是全新空库）。"""
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH, check_same_thread=False)
    conn.execute(_SCHEMA)
    conn.execute(_SCHEMA_META)
    # 老库迁移：tier_source 是后加的（2026-07-29 自动分级），已有的 dialogue_memory.db
    # 不会因为 CREATE TABLE IF NOT EXISTS 而长出新列，必须显式补。已存在的行默认按
    # 'manual' 处理——它们本来就都是主播手动打的，不能被自动判定推翻。
    cols = {r[1] for r in conn.execute("PRAGMA table_info(viewers)")}
    if "tier_source" not in cols:
        conn.execute("ALTER TABLE viewers ADD COLUMN tier_source TEXT DEFAULT 'manual'")
    if "last_seen_session" not in cols:
        conn.execute("ALTER TABLE viewers ADD COLUMN last_seen_session INTEGER DEFAULT 0")
        # 回填成**当前场次号**而不是留着 DEFAULT 的 0。老库里的人是真来过的，只是那会儿
        # 还没有这个计数器，没法知道他们上次是第几场来的。留 0 等于宣称"他们从第 0 场起
        # 就没露过面"：只要计数器已经跑过 10，下一次下播 evict_viewers() 会把整张表清空，
        # 主播积攒的观众画像一次没了。回填当前场次 = 当作"刚见过"，给满一个完整的宽限期，
        # 最坏情况只是多留 10 场早该忘的人，代价不对称，宁可多留。
        conn.execute("UPDATE viewers SET last_seen_session=?", (get_session_no(conn),))
    conn.commit()
    return conn


def get_session_no(conn):
    """当前场次号。0 表示还没下播过任何一场（计数器在 stream_end 才 +1）。"""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (_SESSION_KEY,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0        # 值被写坏了也不能让下播流程崩，退回 0 顶多是少淘汰几个人


def bump_session_no(conn):
    """场次 +1，返回新的场次号。只该在下播收尾时调一次（evict_viewers() 里已经调了）。"""
    n = get_session_no(conn) + 1
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (_SESSION_KEY, str(n)))
    conn.commit()
    return n


def get_viewer(conn, nickname):
    row = conn.execute(
        "SELECT nickname, tier, gift_total_session, last_seen_ts, note, sessions_seen, gift_total_lifetime, "
        "last_seen_session FROM viewers WHERE nickname=?",
        (nickname,)).fetchone()
    if not row:
        return None
    return {'nickname': row[0], 'tier': row[1], 'gift_total_session': row[2], 'last_seen_ts': row[3],
            'note': row[4], 'sessions_seen': row[5], 'gift_total_lifetime': row[6],
            'last_seen_session': row[7]}


def upsert_viewer(conn, nickname, ts, gift_total=None, session_no=None):
    """回复某观众前查一条、回复后更新一条；送礼时也用来记账。gift_total=None 时不改该字段。
    不碰 tier/sessions_seen/gift_total_lifetime/note——那几个字段各自有专门的写入函数。

    session_no=None 时自己去读当前场次号。调用方（dialogue.py）每次写都是"此刻见到了这个人"，
    这个值除了当前场次不会是别的，与其让每个调用点都去查一遍再传进来，不如默认就取对；
    显式传值留给测试构造"上次是第几场来的"这种历史数据。"""
    if session_no is None:
        session_no = get_session_no(conn)
    existing = get_viewer(conn, nickname)
    if existing is None:
        conn.execute(
            "INSERT INTO viewers(nickname, tier, gift_total_session, last_seen_ts, note, last_seen_session) "
            "VALUES (?,?,?,?,?,?)",
            (nickname, 'normal', gift_total or 0, ts, None, session_no))
    else:
        new_gift = gift_total if gift_total is not None else existing['gift_total_session']
        conn.execute("UPDATE viewers SET gift_total_session=?, last_seen_ts=?, last_seen_session=? "
                     "WHERE nickname=?", (new_gift, ts, session_no, nickname))
    conn.commit()


def set_tier(conn, nickname, tier, source='manual'):
    """分级的唯一写入口（跟 upsert_viewer() 分开，那个函数明确"不碰 tier"）。

    两个来源：主播在 control-panel 手动打标（source='manual'），或 perception-danmaku
    依据粉丝团等级自动判定（source='auto'）。

    **手动优先**：source='auto' 时，若这条记录的 tier 是主播亲手打过的，直接跳过不覆盖。
    理由是主播掌握弹幕之外的信息（谁是老朋友、谁刚吵过架），机器不该拿一个等级数字去推翻他。
    返回 True=写入了，False=被手动值挡下了。
    """
    row = conn.execute("SELECT tier, tier_source FROM viewers WHERE nickname=?", (nickname,)).fetchone()
    if row is not None and source == 'auto' and (row[1] or 'manual') == 'manual':
        return False
    if row is None:
        conn.execute(
            "INSERT INTO viewers(nickname, tier, tier_source, gift_total_session, last_seen_ts, note) "
            "VALUES (?,?,?,?,?,?)", (nickname, tier, source, 0, None, None))
    else:
        conn.execute("UPDATE viewers SET tier=?, tier_source=? WHERE nickname=?", (tier, source, nickname))
    conn.commit()
    return True


def sediment_session(conn, nickname, ts, sessions_seen, gift_total_lifetime, note=None, session_no=None):
    """stream_end 收尾专用：写跨场累计字段（sessions_seen/gift_total_lifetime）+ 可选的常客
    结构化 note，同时把本场计数器 gift_total_session 归零。字段集合跟 upsert_viewer() 不一样，
    分开一个函数，不给那个的职责加码。

    session_no 语义同 upsert_viewer()。注意这里写的是**本场**的场次号：沉淀发生在 stream_end
    收尾时、evict_viewers() 把计数器 +1 **之前**，所以顺序不能反——先 +1 再沉淀的话，本场
    露过面的人会被记成"下一场见过的"，场次差整体偏移一格。"""
    if session_no is None:
        session_no = get_session_no(conn)
    existing = get_viewer(conn, nickname)
    if existing is None:
        conn.execute(
            "INSERT INTO viewers(nickname, tier, gift_total_session, last_seen_ts, note, "
            "sessions_seen, gift_total_lifetime, last_seen_session) VALUES (?,?,?,?,?,?,?,?)",
            (nickname, 'normal', 0, ts, note, sessions_seen, gift_total_lifetime, session_no))
    else:
        conn.execute(
            "UPDATE viewers SET gift_total_session=0, last_seen_ts=?, sessions_seen=?, "
            "gift_total_lifetime=?, note=?, last_seen_session=? WHERE nickname=?",
            (ts, sessions_seen, gift_total_lifetime, note, session_no, nickname))
    conn.commit()


# 淘汰豁免：tier 是 member / star_guardian 的观众不参与自动淘汰。
#
# ⚠️ 这里**不能**再 OR 一个 tier_source='manual'，虽然直觉上"主播手动打过标的要保住"是对的：
# tier_source 这一列的 DEFAULT 就是 'manual'，而 upsert_viewer() 建档时根本不写这一列，
# 于是每个路过弹幕一次的路人落库时 tier_source 都是 'manual'。真按 OR 写，整张表都被豁免，
# 淘汰函数变成一个什么都不删的空函数——而且它还"跑通了"，不会报错，只会在几个月后表涨到
# 几千人时才被发现。要判"主播亲手认定的重要观众"，能用的信号只有 tier 本身。
#
# 选择豁免而不是一视同仁，理由是代价不对称：多留一个早该忘的 VIP 只是几百字节，
# 而把主播亲手标的星守护自动删掉，下次人家来了桌宠完全不认识，直播现场就是事故。
# 自动判定出来的 member/star_guardian 一并豁免——他们是真金白银的粉丝团/大哥，
# 且就算误删了，下次一发言 perception-danmaku 也会立刻把标重新打回来，风险更低。
_EXEMPT_SQL = "COALESCE(tier,'normal') IN ('member','star_guardian')"


def evict_viewers(conn, max_absent=MAX_ABSENT_SESSIONS, max_viewers=MAX_VIEWERS):
    """下播收尾的最后一步：场次 +1，然后淘汰长期不来的和超出上限的观众。返回删掉几条。

    必须排在 sediment_session() 之后调用（见那边的注释：先 +1 会让本场的人整体偏移一格）。

    场次差的读法：刚在上一场露过面的人，+1 之后差值是 1，不是 0。所以 `差值 > 10` 恰好
    是"中间整整 10 场都没来"，与主播说的"超过 10 场没来就忘掉"一致。
    """
    current = bump_session_no(conn)

    removed = conn.execute(
        f"DELETE FROM viewers WHERE ? - COALESCE(last_seen_session, 0) > ? AND NOT ({_EXEMPT_SQL})",
        (current, max_absent)).rowcount

    # 再按上限裁。豁免的 VIP 也算进总数、但不参与被裁——所以万一 VIP 本身就超过 30 人，
    # 表会停在 VIP 数量上、超出上限，这是有意的：上限是为了压住路人无限堆积，
    # 不该反过来变成删主播重要观众的理由。
    total = conn.execute("SELECT COUNT(*) FROM viewers").fetchone()[0]
    overflow = total - max_viewers
    if overflow > 0:
        # 最久没见的先删；同一场次里再用时间戳、昵称兜底排序，保证结果稳定可测。
        doomed = conn.execute(
            f"SELECT nickname FROM viewers WHERE NOT ({_EXEMPT_SQL}) "
            "ORDER BY COALESCE(last_seen_session, 0) ASC, COALESCE(last_seen_ts, 0) ASC, nickname ASC "
            "LIMIT ?", (overflow,)).fetchall()
        conn.executemany("DELETE FROM viewers WHERE nickname=?", doomed)
        removed += len(doomed)

    conn.commit()
    return removed
