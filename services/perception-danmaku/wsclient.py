#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极简 WebSocket 客户端（RFC6455），只做我们需要的那部分：连本机服务、收文本帧。

为什么不用现成库：本项目其它感知/总线代码都是纯标准库（见 services/bus/bus_client.py），
而这个客户端要在主播开播时**无人值守**地跑一整场，少一个 pip 依赖就少一个失败点
（真实教训：本机 Shadowsocks 代理会让 pip 联网时好时坏）。服务端在 127.0.0.1、
帧都是几百字节的 JSON，用不到完整库的能力。

只实现客户端必需的：握手、掩码发送、分片重组、自动回 pong、收 close。
"""
import base64
import hashlib
import os
import socket
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WSError(Exception):
    pass


class WSClient:
    def __init__(self, host="127.0.0.1", port=8888, path="/", timeout=30.0):
        self.host, self.port, self.path, self.timeout = host, port, path, timeout
        self.sock = None
        # 收流缓冲。**必须有**：握手时一次 recv 很可能把服务端紧跟着推来的数据帧一起读进来
        # （DouyinBarrageGrab 就是连上即推），只取响应头、丢掉多读的部分会导致永远等不到
        # 那几条消息——自检里正是靠这个才发现的。
        self._buf = b""

    # ---- 连接 ----
    def connect(self, handshake_timeout=5.0):
        self.sock = socket.create_connection((self.host, self.port), timeout=handshake_timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\n"
               f"Host: {self.host}:{self.port}\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = self._read_until(b"\r\n\r\n", limit=65536).decode("latin-1")
        first = head.split("\r\n", 1)[0]
        if "101" not in first:
            raise WSError(f"握手失败，服务端返回：{first}")
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        got = ""
        for line in head.split("\r\n")[1:]:
            if line.lower().startswith("sec-websocket-accept:"):
                got = line.split(":", 1)[1].strip()
        if got != expect:
            raise WSError("握手校验失败：Sec-WebSocket-Accept 不匹配")
        self.sock.settimeout(self.timeout)
        return self

    def _read_until(self, marker, limit):
        """读到 marker 为止，marker 之后多读到的字节留在 self._buf 里给后续帧解析用。"""
        while marker not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("握手期间连接被关闭")
            self._buf += chunk
            if len(self._buf) > limit:
                raise WSError("握手响应过大")
        head, self._buf = self._buf.split(marker, 1)
        return head + marker

    def _read_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WSError("连接已关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    # ---- 收 ----
    def _read_frame(self):
        b0, b1 = self._read_exact(2)
        fin, opcode = b0 & 0x80, b0 & 0x0F
        masked, length = b1 & 0x80, b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length) if length else b""
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return bool(fin), opcode, payload

    def recv_text(self):
        """收一条完整文本消息（自动重组分片、自动回 pong）。收到 close 抛 WSError。"""
        data, op = b"", None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise WSError("服务端主动关闭了连接")
            if opcode != OP_CONT:
                op, data = opcode, payload
            else:
                data += payload
            if fin:
                if op == OP_TEXT:
                    return data.decode("utf-8", "replace")
                # 二进制帧不是我们要的，丢掉继续等下一条
                data, op = b"", None

    def __iter__(self):
        while True:
            yield self.recv_text()

    # ---- 发 ----
    def _send_frame(self, opcode, payload=b""):
        mask = os.urandom(4)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        n = len(payload)
        if n < 126:
            head = struct.pack(">BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(head + mask + masked)

    def close(self):
        try:
            if self.sock:
                self._send_frame(OP_CLOSE)
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
