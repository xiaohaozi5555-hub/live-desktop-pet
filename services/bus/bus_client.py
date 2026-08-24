#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地总线 Python 客户端：连接 broker，publish/subscribe JSON-lines 消息（纯标准库）。"""
import json
import socket
import threading


class BusClient:
    def __init__(self, host='127.0.0.1', port=8765, source='client'):
        self.addr = (host, port)
        self.source = source
        self.sock = None
        self.handlers = []
        self._run = False
        self._send_lock = threading.Lock()  # publish() 可能被多个线程并发调用（如接收线程 + 外部
                                             # ticker/worker 线程都会发布），保护底层 socket 写入不交错。

    def connect(self, retries=20, delay=0.1):
        import time
        last = None
        for _ in range(retries):
            try:
                self.sock = socket.create_connection(self.addr)
                break
            except OSError as e:
                last = e
                time.sleep(delay)
        if self.sock is None:
            raise last
        self._run = True
        threading.Thread(target=self._recv, daemon=True).start()
        return self

    def subscribe(self, handler):
        """handler(msg_dict) 每收到一条消息调用一次。"""
        self.handlers.append(handler)
        return self

    def publish(self, msg):
        line = (json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8')
        with self._send_lock:
            self.sock.sendall(line)

    def _recv(self):
        buf = b''
        while self._run:
            try:
                data = self.sock.recv(4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for h in self.handlers:
                    try:
                        h(msg)
                    except Exception:
                        pass

    def close(self):
        self._run = False
        try:
            self.sock.close()
        except OSError:
            pass
