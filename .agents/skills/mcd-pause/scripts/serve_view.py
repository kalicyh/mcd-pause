#!/usr/bin/env python3
"""Temporary loopback page; optional bounded Skill API, never a system service."""
import argparse
import getpass
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def make_server(page=None, session=None, port=0):
    token = secrets.token_urlsafe(32)

    class Preview(BaseHTTPRequestHandler):
        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def reply(self, status, content, kind='application/json; charset=utf-8'):
            body = content.encode() if isinstance(content, str) else json.dumps(content, ensure_ascii=False).encode()
            self.send_response(status)
            for k, v in {'Content-Type': kind, 'Content-Length': str(len(body)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                         'Content-Security-Policy': "frame-ancestors 'none'", 'X-Frame-Options': 'DENY'}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                self.reply(403, {'error': '来源无效'})
                return
            if self.path != '/':
                self.reply(404, {'error': '未找到页面'})
                return
            self.server.last_activity = time.monotonic()
            self.reply(200, session.page(token) if session else page.read_text(), 'text/html; charset=utf-8')

        def do_POST(self):
            if (not session or not self.valid_host() or
                    self.headers.get('Origin') != 'http://' + self.headers.get('Host', '') or
                    not secrets.compare_digest(self.headers.get('X-McPause-Session', ''), token)):
                self.reply(403, {'error': '请从当前点餐页面操作'})
                return
            if not self.path.startswith('/api/') or self.headers.get('Content-Type') != 'application/json':
                self.reply(400, {'error': '请求格式无效'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 131072:
                    raise ValueError('请求大小无效')
                payload = json.loads(self.rfile.read(length))
                self.server.last_activity = time.monotonic()
                self.reply(200, session.handle(self.path[5:], payload))
            except ValueError as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                self.reply(500, {'error': '查询未完成，请回到 Codex 检查连接；不要重复提交订单'})

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', port), Preview)
    server.timeout = 1
    server.last_activity = time.monotonic()
    return server


def run(server):
    print(f'http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        while time.monotonic() - server.last_activity < 2 * 60 * 60:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="临时打开本机点餐页；不安装服务、不修改宿主配置")
    parser.add_argument("file", type=Path, nargs='?')
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument('--session', type=Path, help='任务私有的菜单、详情和已确认门店配置 JSON')
    parser.add_argument('--state-dir', type=Path, help='会话私有状态目录，必须放在任务 work 下')
    parser.add_argument('--token-stdin', action='store_true')
    parser.add_argument('--allow-orders', action='store_true', help='提供已核实配送/取餐参数后，允许页面最终确认提交')
    args = parser.parse_args()
    if args.session:
        if not args.state_dir or args.file:
            parser.error('--session 需要 --state-dir，且不同时传入静态文件')
        from browser_session import BrowserSession
        from mcp_client import Client
        client = Client(getpass.getpass('MCP Token（不保存）: ') if args.token_stdin else None)
        session = BrowserSession(client, json.loads(args.session.read_text()), args.state_dir, args.allow_orders)
        run(make_server(session=session, port=args.port))
    else:
        if not args.file or not args.file.is_file() or args.file.suffix.lower() != '.html':
            parser.error('需要已有 HTML 文件或 --session')
        run(make_server(page=args.file.resolve(), port=args.port))


if __name__ == "__main__":
    main()
