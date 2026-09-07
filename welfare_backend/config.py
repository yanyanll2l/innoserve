'''讀取與管理設定'''
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 找出整個專案的根目錄
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 設定預設的來源設定檔位置
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "sources.json"

# 整個 mvp 專案的根目錄
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "welfare.sqlite3"

# 整個 mvp 專案的根目錄
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "data" / "export"

# 讀取 sources.json，並把 JSON 內容轉成 Python 字典
def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as file:
        return json.load(file)

# 從所有資料來源中，挑出目前啟用的來源
def enabled_sources(config: dict[str, Any], kind: str | None = None) -> list[dict[str, Any]]:
    sources = [source for source in config.get("sources", []) if source.get("enabled", True)]
    if kind is not None:
        sources = [source for source in sources if source.get("kind") == kind]
    return sources

