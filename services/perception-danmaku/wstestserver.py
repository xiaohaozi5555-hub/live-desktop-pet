#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用的最小 WebSocket 服务端，模拟 DouyinBarrageGrab 的推送端。

抽出来共用，是为了不在两个验证脚本里各写一份帧编码——协议代码写两遍就会长出两种 bug。
仅供验证脚本使用，不参与运行时。
"""
import base64
import hashlib
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def frame(opcode, payload, fin=True):
    """服务端 → 客户端的帧不加掩码。"""
    n = len(payload)
    b0 = (0x80 if fin else 0x00) | opcode
    if n < 126:
        return struct.pack(">BB", b0, n) + payload
    if n < 65536:
        return struct.pack(">BBH", b0, 126, n) + payload
    return struct.pack(">BBQ", b0, 127, n) + payload


def _handshake(conn):
    req = b""
    while b"\r\n\r\n" not in req:
        chunk = conn.recv(4096)
        if not chunk:
            return False
        req += chunk
    key = ""
    for line in req.decode("latin-1").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    return True


def _drain_then_close(conn):
    """不能发完就 close()：客户端回的 pong 还在路上，Windows 对已关闭套接字收到数据会回
    RST，连带把客户端尚未读走的接收缓冲一并丢掉（现象是一条消息都收不到）。"""
    try:
        conn.shutdown(socket.SHUT_WR)
        conn.settimeout(2.0)
        while conn.recv(4096):
            pass
    except OSError:
        pass
    conn.close()


def serve_once(messages, fragment_first=True, send_ping=True, delay=0.0, close_after=True):
    """起一个只服务一次连接的服务端，返回 (port, thread)。

    默认会故意制造两种边角情况：先发一个 ping（客户端必须自动回 pong 且不当成数据）、
    把第一条消息拆成两个分片（考验重组）。
    """
    import time
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        conn, _ = srv.accept()
        try:
            if not _handshake(conn):
                return
            if send_ping:
                conn.sendall(frame(0x9, b"hb"))
            for i, m in enumerate(messages):
                data = m.encode("utf-8")
                if i == 0 and fragment_first and len(data) > 10:
                    conn.sendall(frame(0x1, data[:10], fin=False))
                    conn.sendall(frame(0x0, data[10:], fin=True))
                else:
                    conn.sendall(frame(0x1, data))
                if delay:
                    time.sleep(delay)
            if close_after:
                conn.sendall(frame(0x8, b""))
                _drain_then_close(conn)
            else:
                time.sleep(30)
        except OSError:
            pass
        finally:
            try:
                srv.close()
            except OSError:
                pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return port, t
