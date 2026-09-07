'''
對外提供 API
負責啟動 HTTP API，讓前端或其他程式可以透過網址查詢福利資料
'''
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .db import Database
from .matcher import DISCLAIMER, match_many


def _one(params: dict[str, list[str]], key: str, default: str = "") -> str:
    values = params.get(key)
    return values[0] if values else default


class WelfareHandler(BaseHTTPRequestHandler):
    database: Database
    server_version = "WelfareNavigationMVP/0.1"

    def _send(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(HTTPStatus.NO_CONTENT, {})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                self._send(HTTPStatus.OK, {"status": "ok", **self.database.counts()})
                return
            if parsed.path == "/api/v1/benefits":
                limit = int(_one(params, "limit", "20"))
                items = self.database.search_benefits(
                    query=_one(params, "q"),
                    audience=_one(params, "audience"),
                    service_type=_one(params, "service_type"),
                    limit=limit,
                )
                self._send(
                    HTTPStatus.OK,
                    {"count": len(items), "items": items, "disclaimer": DISCLAIMER},
                )
                return
            if parsed.path.startswith("/api/v1/benefits/"):
                benefit_id = int(parsed.path.rsplit("/", 1)[-1])
                item = self.database.get_benefit(benefit_id)
                if item is None:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "找不到福利資料"})
                else:
                    self._send(HTTPStatus.OK, {"item": item, "disclaimer": DISCLAIMER})
                return
            if parsed.path == "/api/v1/institutions":
                limit = int(_one(params, "limit", "50"))
                items = self.database.search_institutions(
                    query=_one(params, "q"), district=_one(params, "district"), limit=limit
                )
                self._send(HTTPStatus.OK, {"count": len(items), "items": items})
                return
            if parsed.path == "/api/v1/changes":
                items = self.database.recent_changes(int(_one(params, "limit", "20")))
                self._send(HTTPStatus.OK, {"count": len(items), "items": items})
                return
            self._send(
                HTTPStatus.NOT_FOUND,
                {
                    "error": "找不到路由",
                    "routes": [
                        "/health", "/api/v1/benefits", "/api/v1/benefits/{id}",
                        "/api/v1/institutions", "/api/v1/changes", "/api/v1/match",
                    ],
                },
            )
        except (ValueError, TypeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/v1/match":
            self._send(HTTPStatus.NOT_FOUND, {"error": "找不到路由"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "請求內容過大"})
                return
            profile = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(profile, dict):
                raise ValueError("JSON 必須是物件")
            benefits = self.database.search_benefits(
                query=str(profile.get("query", "")),
                audience=str(profile.get("audience", "")),
                limit=min(int(profile.get("limit", 30)), 100),
            )
            matches = match_many(benefits, profile)
            self._send(HTTPStatus.OK, {"count": len(matches), "matches": matches, "disclaimer": DISCLAIMER})
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def serve(database: Database, host: str = "127.0.0.1", port: int = 8000) -> None:
    database.initialize()
    handler = type("ConfiguredWelfareHandler", (WelfareHandler,), {"database": database})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"福利導航 API 已啟動：http://{host}:{port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

