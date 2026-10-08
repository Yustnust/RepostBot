"""FC 自定义容器运行时引导：监听 9000 端口，把调用转发给 index.handler。

FC 的事件函数 + 容器镜像要求容器内实现 HTTP 服务（默认 9000 端口），
FC 会把定时触发器等调用以 HTTP POST 转发进来。本模块：
  - GET  （就绪/健康探测）→ 返回 200
  - POST （调用入口）     → 请求体作为 event 传给 index.handler，返回值写回响应
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(__file__.rsplit("\\", 1)[0] if "\\" in __file__ else __file__.rsplit("/", 1)[0]))

import index  # noqa: E402

PORT = int(os.environ.get("FC_SERVER_PORT", "9000"))


class InvokeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _respond(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        # FC 对容器的就绪探测走 GET，直接回 200 表示容器可用
        self._respond(200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        event = self.rfile.read(length) if length else b""
        try:
            result = index.handler(event, None)
            self._respond(200, result)
        except Exception as e:  # noqa: BLE001
            # handler 内部已兜底，这里只兜 bootstrap 自身的意外
            self._respond(500, {
                "results": [{"firm": "-", "status": "failed",
                             "message": f"bootstrap 异常 {type(e).__name__}: {e}",
                             "trace": traceback.format_exc()[-2000:]}],
            })

    def log_message(self, fmt: str, *args: object) -> None:
        # 静默默认 stderr 访问日志，避免刷屏干扰 SLS 里的业务日志
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), InvokeHandler)
    print(f"bootstrap listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()
