"""Loopback-only, standard-library UI for a local synthetic market.

Only connects to the local browser over HTTP on 127.0.0.1. Never connects
outward, touches live orders or reads exchange/AI credentials.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock

from .interactive import SessionConfig, SyntheticMarket

_STATIC = Path(__file__).with_name('static')


def create_handler():
    market = SyntheticMarket()
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'KairosMarketLab/0.3'

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                             "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: dict) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

        def _local(self) -> bool:
            # Prevent DNS rebinding from arbitrary hostnames.
            host = self.headers.get('Host', '').split(':')[0]
            return host in ('127.0.0.1', 'localhost')

        def do_GET(self) -> None:
            if not self._local():
                self._json(403, {'error': 'LOCAL_ONLY'})
                return
            if self.path == '/api/state':
                with lock:
                    payload = market.state()
                self._json(200, payload)
                return
            allowed = {'/': ('index.html', 'text/html; charset=utf-8'),
                       '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
                       '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if self.path not in allowed:
                self._json(404, {'error': 'NOT_FOUND'})
                return
            filename, content_type = allowed[self.path]
            self._send(200, (_STATIC / filename).read_bytes(), content_type)

        def do_POST(self) -> None:
            if not self._local():
                self._json(403, {'error': 'LOCAL_ONLY'})
                return
            # JSON-only, no CORS, same-origin requests; reject cross-site attempts.
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                self._json(415, {'error': 'JSON_REQUIRED'})
                return
            site = self.headers.get('Sec-Fetch-Site')
            if site and site not in ('same-origin', 'none'):
                self._json(403, {'error': 'CROSS_SITE_FORBIDDEN'})
                return
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')
            if origin and origin not in (f'http://{host}',):
                self._json(403, {'error': 'CROSS_ORIGIN_FORBIDDEN'})
                return
            if self.path not in ('/api/step', '/api/reset'):
                self._json(404, {'error': 'NOT_FOUND'})
                return
            try:
                n = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= n <= 4096:
                    raise ValueError('INVALID_CONTENT_LENGTH')
                raw = json.loads(self.rfile.read(n))
                if not isinstance(raw, dict):
                    raise ValueError('JSON_OBJECT_REQUIRED')
                with lock:
                    nonlocal market
                    if self.path == '/api/reset':
                        if set(raw) - set(SessionConfig.__dataclass_fields__):
                            raise ValueError('UNKNOWN_CONFIG_FIELD')
                        config = SessionConfig(**raw)
                        market = SyntheticMarket(config)
                        state = market.state()
                    else:
                        if set(raw) != {'count'}:
                            raise ValueError('COUNT_REQUIRED')
                        state = market.advance(raw['count'])
            except (ValueError, TypeError, KeyError) as exc:
                self._json(400, {'error': str(exc)[:220]})
                return
            self._json(200, state)

        def log_message(self, format: str, *args: object) -> None:
            # No browser history, no access logs by default.
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description='Kairos Market Lab • synthetic local UI')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be 1024..65535')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), create_handler())
    print(f'Kairos Market Lab — SIMULATED ONLY — http://127.0.0.1:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
