'''接收在 PowerShell 輸入的指令'''

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

from .api import serve
from .config import DEFAULT_DB_PATH, DEFAULT_EXPORT_DIR, enabled_sources, load_config
from .db import Database, now_iso
from .matcher import DISCLAIMER, match_many
from .pipeline import refresh


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _database(path: str | None) -> Database:
    return Database(Path(path) if path else DEFAULT_DB_PATH)


def _load_profile(value: str) -> dict[str, Any]:
    """讀取個人條件；支援 PowerShell wrapper 傳入的 base64 JSON。"""
    if value.startswith("base64:"):
        encoded = value.removeprefix("base64:")
        value = base64.b64decode(encoded).decode("utf-8")
    elif value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8-sig")
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("個人條件必須是 JSON 物件")
    return payload


def _export(database: Database, directory: str | None) -> dict[str, Any]:
    export_dir = Path(directory) if directory else DEFAULT_EXPORT_DIR
    export_dir.mkdir(parents=True, exist_ok=True)
    generated_at = now_iso()
    benefits = {
        "generated_at": generated_at,
        "disclaimer": DISCLAIMER,
        "items": database.all_benefits(),
    }
    institutions = {"generated_at": generated_at, "items": database.all_institutions()}
    files = {
        "benefits": export_dir / "benefits.json",
        "institutions": export_dir / "institutions.json",
    }
    files["benefits"].write_text(json.dumps(benefits, ensure_ascii=False, indent=2), encoding="utf-8")
    files["institutions"].write_text(
        json.dumps(institutions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"generated_at": generated_at, "files": {key: str(value) for key, value in files.items()}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="福利導航與服務缺口雷達－資料後端 MVP")
    parser.add_argument("--db", help="SQLite 資料庫路徑")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="建立資料庫")

    refresh_parser = subparsers.add_parser("refresh", help="抓取所有官方來源並更新資料庫")
    refresh_parser.add_argument("--config", help="來源設定 JSON")

    search_parser = subparsers.add_parser("search", help="搜尋福利")
    search_parser.add_argument("query", nargs="?", default="")
    search_parser.add_argument("--audience", default="")
    search_parser.add_argument("--type", dest="service_type", default="")
    search_parser.add_argument("--limit", type=int, default=20)

    institution_parser = subparsers.add_parser("institutions", help="搜尋機構")
    institution_parser.add_argument("query", nargs="?", default="")
    institution_parser.add_argument("--district", default="")
    institution_parser.add_argument("--limit", type=int, default=50)

    match_parser = subparsers.add_parser("match", help="以 JSON 個人條件做初步福利比對")
    match_parser.add_argument("profile", help="JSON 字串，例如 {\"age\":72,\"city\":\"臺北市\"}")

    export_parser = subparsers.add_parser("export", help="匯出前端可用 JSON")
    export_parser.add_argument("--output", help="輸出資料夾")

    serve_parser = subparsers.add_parser("serve", help="啟動 HTTP API")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    subparsers.add_parser("sources", help="列出來源設定")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    database = _database(args.db)
    if args.command == "init":
        database.initialize()
        _print_json({"status": "ok", "database": str(database.path), "counts": database.counts()})
    elif args.command == "refresh":
        _print_json(refresh(database, args.config))
    elif args.command == "search":
        database.initialize()
        _print_json(
            {
                "items": database.search_benefits(
                    args.query, args.audience, args.service_type, args.limit
                ),
                "disclaimer": DISCLAIMER,
            }
        )
    elif args.command == "institutions":
        database.initialize()
        _print_json({"items": database.search_institutions(args.query, args.district, args.limit)})
    elif args.command == "match":
        database.initialize()
        profile = _load_profile(args.profile)
        benefits = database.search_benefits(query=str(profile.get("query", "")), limit=50)
        _print_json({"matches": match_many(benefits, profile), "disclaimer": DISCLAIMER})
    elif args.command == "export":
        database.initialize()
        _print_json(_export(database, args.output))
    elif args.command == "serve":
        serve(database, args.host, args.port)
    elif args.command == "sources":
        _print_json(enabled_sources(load_config()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
