'''
資料庫管理中心，負責：
- 建立 SQLite 資料庫
- 建立福利、機構、異動紀錄資料表
- 新增資料
- 更新資料
- 判斷資料是否沒變
- 搜尋福利與機構
- 記錄歷史異動
'''

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .parse import content_hash


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS benefits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    agency TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    service_type TEXT NOT NULL DEFAULT '',
                    audiences_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    eligibility_text TEXT NOT NULL DEFAULT '',
                    eligibility_rules_json TEXT NOT NULL DEFAULT '{}',
                    service_content TEXT NOT NULL DEFAULT '',
                    application_method TEXT NOT NULL DEFAULT '',
                    required_documents TEXT NOT NULL DEFAULT '',
                    fees TEXT NOT NULL DEFAULT '',
                    contact TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL,
                    source_updated_at TEXT NOT NULL DEFAULT '',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    content_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(source_id, external_id)
                );

                CREATE INDEX IF NOT EXISTS idx_benefits_title ON benefits(title);
                CREATE INDEX IF NOT EXISTS idx_benefits_type ON benefits(service_type);
                CREATE INDEX IF NOT EXISTS idx_benefits_active ON benefits(active);

                CREATE TABLE IF NOT EXISTS institutions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    district TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    capacity INTEGER,
                    attributes_json TEXT NOT NULL DEFAULT '{}',
                    source_url TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(source_id, external_id)
                );

                CREATE INDEX IF NOT EXISTS idx_institutions_name ON institutions(name);
                CREATE INDEX IF NOT EXISTS idx_institutions_district ON institutions(district);

                CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER NOT NULL,
                    change_type TEXT NOT NULL,
                    old_hash TEXT,
                    new_hash TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_changes_detected ON changes(detected_at DESC);

                CREATE TABLE IF NOT EXISTS source_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    fetched_count INTEGER NOT NULL DEFAULT 0,
                    inserted_count INTEGER NOT NULL DEFAULT 0,
                    updated_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS source_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    etag TEXT,
                    last_modified TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                """
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS benefits_fts "
                    "USING fts5(benefit_id UNINDEXED, title, body, tokenize='trigram')"
                )
            except sqlite3.OperationalError:
                # 搜尋仍可使用 LIKE；某些 Python 發行版沒有 FTS5/trigram。
                pass

    def start_run(self, source_id: str) -> int:
        started_at = now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO source_runs(source_id, started_at, status) VALUES (?, ?, 'running')",
                (source_id, started_at),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        fetched: int = 0,
        inserted: int = 0,
        updated: int = 0,
        error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE source_runs
                SET finished_at = ?, status = ?, fetched_count = ?, inserted_count = ?,
                    updated_count = ?, error = ?
                WHERE id = ?
                """,
                (now_iso(), status, fetched, inserted, updated, error, run_id),
            )

    def upsert_benefit(self, record: dict[str, Any]) -> str:
        timestamp = now_iso()
        digest = content_hash(record)
        key = (record["source_id"], str(record["external_id"]))
        json_fields = {
            "audiences_json": record.get("audiences", []),
            "tags_json": record.get("tags", []),
            "eligibility_rules_json": record.get("eligibility_rules", {}),
            "attachments_json": record.get("attachments", []),
            "raw_json": record.get("raw", {}),
        }
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, content_hash, active FROM benefits WHERE source_id = ? AND external_id = ?",
                key,
            ).fetchone()
            values = (
                record["source_id"], str(record["external_id"]), record.get("title", ""),
                record.get("agency", ""), record.get("region", ""), record.get("service_type", ""),
                json.dumps(json_fields["audiences_json"], ensure_ascii=False),
                json.dumps(json_fields["tags_json"], ensure_ascii=False),
                record.get("eligibility_text", ""),
                json.dumps(json_fields["eligibility_rules_json"], ensure_ascii=False),
                record.get("service_content", ""), record.get("application_method", ""),
                record.get("required_documents", ""), record.get("fees", ""),
                record.get("contact", ""), record.get("source_url", ""),
                record.get("source_updated_at", ""),
                json.dumps(json_fields["attachments_json"], ensure_ascii=False),
                json.dumps(json_fields["raw_json"], ensure_ascii=False), digest,
            )
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO benefits(
                        source_id, external_id, title, agency, region, service_type,
                        audiences_json, tags_json, eligibility_text, eligibility_rules_json,
                        service_content, application_method, required_documents, fees, contact,
                        source_url, source_updated_at, attachments_json, raw_json, content_hash,
                        active, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (*values, timestamp, timestamp),
                )
                entity_id = int(cursor.lastrowid)
                self._record_change(connection, "benefit", entity_id, "created", None, digest, record)
                self._update_fts(connection, entity_id, record)
                return "inserted"

            entity_id = int(existing["id"])
            if existing["content_hash"] == digest:
                connection.execute(
                    "UPDATE benefits SET active = 1, last_seen_at = ? WHERE id = ?",
                    (timestamp, entity_id),
                )
                if not existing["active"]:
                    self._record_change(
                        connection, "benefit", entity_id, "reactivated", digest, digest, record
                    )
                    return "reactivated"
                return "unchanged"

            connection.execute(
                """
                UPDATE benefits SET
                    title=?, agency=?, region=?, service_type=?, audiences_json=?, tags_json=?,
                    eligibility_text=?, eligibility_rules_json=?, service_content=?,
                    application_method=?, required_documents=?, fees=?, contact=?, source_url=?,
                    source_updated_at=?, attachments_json=?, raw_json=?, content_hash=?,
                    active=1, last_seen_at=?
                WHERE id=?
                """,
                (*values[2:], timestamp, entity_id),
            )
            self._record_change(
                connection, "benefit", entity_id, "updated", existing["content_hash"], digest, record
            )
            self._update_fts(connection, entity_id, record)
            return "updated"

    def _update_fts(self, connection: sqlite3.Connection, entity_id: int, record: dict[str, Any]) -> None:
        try:
            connection.execute("DELETE FROM benefits_fts WHERE benefit_id = ?", (entity_id,))
            body = " ".join(
                str(record.get(key, ""))
                for key in ("eligibility_text", "service_content", "application_method", "fees", "contact")
            )
            body += " " + " ".join(record.get("tags", []))
            connection.execute(
                "INSERT INTO benefits_fts(benefit_id, title, body) VALUES (?, ?, ?)",
                (entity_id, record.get("title", ""), body),
            )
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _record_change(
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: int,
        change_type: str,
        old_hash: str | None,
        new_hash: str,
        snapshot: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO changes(entity_type, entity_id, change_type, old_hash, new_hash,
                                detected_at, snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type, entity_id, change_type, old_hash, new_hash, now_iso(),
                json.dumps(snapshot, ensure_ascii=False),
            ),
        )

    def upsert_institution(self, record: dict[str, Any]) -> str:
        timestamp = now_iso()
        digest = content_hash(record)
        key = (record["source_id"], str(record["external_id"]))
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, content_hash, active FROM institutions WHERE source_id=? AND external_id=?",
                key,
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO institutions(
                        source_id, external_id, name, kind, district, address, phone, capacity,
                        attributes_json, source_url, content_hash, active, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        *key, record.get("name", ""), record.get("kind", ""),
                        record.get("district", ""), record.get("address", ""),
                        record.get("phone", ""), record.get("capacity"),
                        json.dumps(record.get("attributes", {}), ensure_ascii=False),
                        record.get("source_url", ""), digest, timestamp, timestamp,
                    ),
                )
                entity_id = int(cursor.lastrowid)
                self._record_change(connection, "institution", entity_id, "created", None, digest, record)
                return "inserted"
            entity_id = int(existing["id"])
            if existing["content_hash"] == digest:
                connection.execute(
                    "UPDATE institutions SET active=1, last_seen_at=? WHERE id=?", (timestamp, entity_id)
                )
                if not existing["active"]:
                    self._record_change(
                        connection, "institution", entity_id, "reactivated", digest, digest, record
                    )
                    return "reactivated"
                return "unchanged"
            connection.execute(
                """
                UPDATE institutions SET name=?, kind=?, district=?, address=?, phone=?, capacity=?,
                    attributes_json=?, source_url=?, content_hash=?, active=1, last_seen_at=?
                WHERE id=?
                """,
                (
                    record.get("name", ""), record.get("kind", ""), record.get("district", ""),
                    record.get("address", ""), record.get("phone", ""), record.get("capacity"),
                    json.dumps(record.get("attributes", {}), ensure_ascii=False),
                    record.get("source_url", ""), digest, timestamp, entity_id,
                ),
            )
            self._record_change(
                connection, "institution", entity_id, "updated", existing["content_hash"], digest, record
            )
            return "updated"

    def deactivate_missing(self, entity_type: str, source_id: str, seen_external_ids: set[str]) -> int:
        if entity_type not in {"benefit", "institution"}:
            raise ValueError("entity_type 必須是 benefit 或 institution")
        table = "benefits" if entity_type == "benefit" else "institutions"
        count = 0
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT id, external_id, content_hash FROM {table} WHERE source_id=? AND active=1",
                (source_id,),
            ).fetchall()
            for row in rows:
                if str(row["external_id"]) in seen_external_ids:
                    continue
                connection.execute(f"UPDATE {table} SET active=0 WHERE id=?", (int(row["id"]),))
                snapshot = {"source_id": source_id, "external_id": str(row["external_id"]), "active": False}
                self._record_change(
                    connection, entity_type, int(row["id"]), "deactivated",
                    row["content_hash"], row["content_hash"], snapshot,
                )
                count += 1
        return count

    def upsert_source_snapshot(
        self,
        source_id: str,
        name: str,
        url: str,
        digest: str,
        content_type: str = "",
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> str:
        timestamp = now_iso()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, content_hash FROM source_snapshots WHERE source_id=?", (source_id,)
            ).fetchone()
            snapshot = {"source_id": source_id, "name": name, "url": url, "content_hash": digest}
            if existing is None:
                cursor = connection.execute(
                    """
                    INSERT INTO source_snapshots(
                        source_id, name, url, content_hash, content_type, etag, last_modified,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source_id, name, url, digest, content_type, etag, last_modified, timestamp, timestamp),
                )
                self._record_change(
                    connection, "source", int(cursor.lastrowid), "created", None, digest, snapshot
                )
                return "inserted"
            if existing["content_hash"] == digest:
                connection.execute(
                    "UPDATE source_snapshots SET last_seen_at=?, etag=?, last_modified=? WHERE id=?",
                    (timestamp, etag, last_modified, int(existing["id"])),
                )
                return "unchanged"
            connection.execute(
                """
                UPDATE source_snapshots SET name=?, url=?, content_hash=?, content_type=?, etag=?,
                    last_modified=?, last_seen_at=? WHERE id=?
                """,
                (name, url, digest, content_type, etag, last_modified, timestamp, int(existing["id"])),
            )
            self._record_change(
                connection, "source", int(existing["id"]), "updated", existing["content_hash"], digest, snapshot
            )
            return "updated"

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in list(item):
            if key.endswith("_json"):
                output_key = key[:-5]
                try:
                    item[output_key] = json.loads(item.pop(key))
                except (json.JSONDecodeError, TypeError):
                    item[output_key] = item.pop(key)
        if "active" in item:
            item["active"] = bool(item["active"])
        return item

    def search_benefits(
        self,
        query: str = "",
        audience: str = "",
        service_type: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        conditions = ["active = 1"]
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            conditions.append(
                "(title LIKE ? OR eligibility_text LIKE ? OR service_content LIKE ? "
                "OR application_method LIKE ? OR tags_json LIKE ?)"
            )
            params.extend([like] * 5)
        if audience:
            conditions.append("audiences_json LIKE ?")
            params.append(f'%"{audience}"%')
        if service_type:
            conditions.append("service_type = ?")
            params.append(service_type)
        params.append(limit)
        sql = f"SELECT * FROM benefits WHERE {' AND '.join(conditions)} ORDER BY title LIMIT ?"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._decode_row(row) for row in rows]

    def get_benefit(self, benefit_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM benefits WHERE id=?", (benefit_id,)).fetchone()
        return self._decode_row(row) if row else None

    def search_institutions(self, query: str = "", district: str = "", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        conditions = ["active = 1"]
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            conditions.append("(name LIKE ? OR kind LIKE ? OR address LIKE ? OR attributes_json LIKE ?)")
            params.extend([like] * 4)
        if district:
            conditions.append("district LIKE ?")
            params.append(f"%{district}%")
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM institutions WHERE {' AND '.join(conditions)} ORDER BY district, name LIMIT ?",
                params,
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def recent_changes(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM changes ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            benefits = connection.execute("SELECT COUNT(*) FROM benefits WHERE active=1").fetchone()[0]
            institutions = connection.execute("SELECT COUNT(*) FROM institutions WHERE active=1").fetchone()[0]
            changes = connection.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
        return {"benefits": benefits, "institutions": institutions, "changes": changes}

    def all_benefits(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM benefits WHERE active=1 ORDER BY title").fetchall()
        return [self._decode_row(row) for row in rows]

    def all_institutions(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM institutions WHERE active=1 ORDER BY district, name"
            ).fetchall()
        return [self._decode_row(row) for row in rows]
