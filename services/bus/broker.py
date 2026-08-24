#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地事件总线 broker：JSON-lines over TCP 广播（纯标准库，跨 Python/Node）。

每个客户端(Python 服务 / Electron 角色 / 控制面板)连到 broker，发一行 JSON 即广播给
其他所有客户端。契约不变，仅提供传输。默认 127.0.0.1:8765。
"""
import socket
import threading


class Broker:
    def __init__(self, host='127.0.0.1', port=8765):
        self.addr = (host, port)
        self.clients = []
        self.lock = threading.Lock()
        self._srv = None
        self._run = False

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(self.addr)
        self._srv.listen()
        self._run = True
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def _accept(self):
        while self._run:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            with self.lock:
                self.clients.append(conn)
            threading.Thread(target=self._recv, args=(conn,), daemon=True).start()

    def _recv(self, conn):
        buf = b''
        while self._run:
            try:
                data = conn.recv(4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                if line.strip():
                    self._broadcast(line + b'\n', conn)
        with self.lock:
            if conn in self.clients:
                self.clients.remove(conn)
        try:
            conn.close()
        except OSError:
            pass

    def _broadcast(self, data, sender):
        with self.lock:
            targets = list(self.clients)
        for t in targets:
            if t is sender:            # 不回发给发送者，避免回声
                continue
            try:
                t.sendall(data)
            except OSError:
                pass

    def stop(self):
        self._run = False
        try:
            self._srv.close()
        except OSError:
            pass


if __name__ == '__main__':
    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    b = Broker().start()
    print(f"[broker] 监听 {b.addr[0]}:{b.addr[1]}（Ctrl+C 退出）")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        b.stop()
