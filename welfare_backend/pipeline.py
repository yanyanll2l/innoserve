'''
它把前面的功能串起來：
    讀取設定
       ↓
    下載官方資料
       ↓
    解析福利或機構
       ↓
    解析 PDF
       ↓
    寫入 SQLite
       ↓
    記錄新增與更新
       ↓
    回傳執行報告
'''
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from typing import Any

from .config import enabled_sources, load_config
from .db import Database, now_iso
from .fetch import FetchResponse, fetch
from .parse import clean_inline, extract_eligibility_rules, normalize_csv_key, parse_taipei_detail
from .pdf_extract import enrich_attachments, is_pdf_attachment, merge_pdf_fields_into_parent


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_result(counter: Counter[str], result: str) -> None:
    counter[result] += 1


def _deduplicate_attachments(items: list[dict[str, str]]) -> list[dict[str, str]]:
    deduplicated: dict[str, dict[str, str]] = {}
    for item in items:
        url = item.get("url", "")
        if url:
            deduplicated[url] = {"title": clean_inline(item.get("title", "")), "url": url}
    return [deduplicated[url] for url in sorted(deduplicated)]


def _contextualize_attachments(
    attachments: list[dict[str, Any]], parent: dict[str, Any]
) -> list[dict[str, Any]]:
    """不下載附件時，仍保留附件與福利主體之間的完整關係。"""
    contextualized: list[dict[str, Any]] = []
    for attachment in attachments:
        item = dict(attachment)
        item.update(
            {
                "relation": "supplemental_document",
                "document_type": "pdf" if is_pdf_attachment(item) else "other",
                "parent_benefit_title": parent.get("title", ""),
                "audiences": list(parent.get("audiences", [])),
                "region": parent.get("region", ""),
                "agency": parent.get("agency", ""),
                "source_url": item.get("url", ""),
                "official_page_url": parent.get("source_url", ""),
                "last_checked_at": now_iso(),
                "extraction_status": "not_extracted",
            }
        )
        contextualized.append(item)
    return contextualized


def _stable_response_hash(response: FetchResponse, payload: Any | None = None) -> str:
    """對 JSON 來源先排序鍵與資料，避免來源只換排列順序就被判定為異動。"""
    if payload is not None:
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return _sha256(normalized.encode("utf-8"))
    return _sha256(response.body)


def ingest_benefit_feed(source: dict[str, Any], database: Database) -> Counter[str]:
    counter: Counter[str] = Counter()
    seen_external_ids: set[str] = set()
    response = fetch(source["url"])
    payload = json.loads(response.text().lstrip("\ufeff"))
    if not isinstance(payload, list):
        raise ValueError(f"{source['id']} 應回傳 JSON 陣列")
    database.upsert_source_snapshot(
        source["id"], source["name"], source["url"], _stable_response_hash(response, payload),
        response.content_type, response.etag, response.last_modified,
    )

    for item in payload:
        if not isinstance(item, dict):
            continue
        detail_url = clean_inline(item.get("Link")) or clean_inline(item.get("Source"))
        title = clean_inline(item.get("title"))
        agency = clean_inline(item.get("發布單位"))
        parsed: dict[str, Any]
        detail_error = ""
        if detail_url:
            try:
                detail_response = fetch(detail_url)
                parsed = parse_taipei_detail(detail_response.text(), detail_url, title, agency)
            except Exception as error:  # 保留 feed 資料，避免單頁失敗拖垮整批更新。
                detail_error = str(error)
                parsed = parse_taipei_detail("", detail_url, title, agency)
        else:
            parsed = parse_taipei_detail("", source["url"], title, agency)

        feed_content = clean_inline(item.get("內容"))
        if feed_content and not parsed.get("service_content"):
            parsed["service_content"] = feed_content
        feed_attachments = item.get("相關檔案") if isinstance(item.get("相關檔案"), list) else []
        parsed["attachments"] = _deduplicate_attachments(parsed.get("attachments", []) + feed_attachments)
        external_id = str(item.get("DataSN") or item.get("Source") or title)
        seen_external_ids.add(external_id)
        parsed.update(
            {
                "source_id": source["id"],
                "external_id": external_id,
                "source_url": detail_url or source["catalog_url"],
                "last_checked_at": now_iso(),
                "raw": {"feed_item": item, "detail_fetch_error": detail_error},
            }
        )
        if source.get("extract_pdfs", True):
            parsed["attachments"] = enrich_attachments(parsed["attachments"], parsed)
            merge_pdf_fields_into_parent(parsed)
            parsed["eligibility_rules"] = extract_eligibility_rules(parsed.get("eligibility_text", ""))
            for attachment in parsed["attachments"]:
                if attachment.get("document_type") == "pdf":
                    counter[f"pdf_{attachment.get('extraction_status', 'unknown')}"] += 1
        result = database.upsert_benefit(parsed)
        _record_result(counter, result)
        counter["fetched"] += 1
    counter["deactivated"] += database.deactivate_missing("benefit", source["id"], seen_external_ids)
    return counter


def ingest_benefit_pages(source: dict[str, Any], database: Database) -> Counter[str]:
    """把設定檔列出的官方申辦頁面逐頁整理成可查詢福利。"""
    counter: Counter[str] = Counter()
    configured_items = source.get("items", [])
    if not isinstance(configured_items, list) or not configured_items:
        raise ValueError(f"{source['id']} 必須提供非空白 items 陣列")

    seen_external_ids: set[str] = set()
    page_snapshots: list[dict[str, str]] = []
    for item in configured_items:
        if not isinstance(item, dict):
            continue
        external_id = clean_inline(str(item.get("id", "")))
        page_url = clean_inline(str(item.get("url", "")))
        if not external_id or not page_url:
            counter["invalid_items"] += 1
            continue
        seen_external_ids.add(external_id)
        try:
            response = fetch(page_url)
            parsed = parse_taipei_detail(
                response.text(), page_url, clean_inline(item.get("title")), clean_inline(item.get("agency"))
            )
            page_snapshots.append({"id": external_id, "hash": _sha256(response.body)})

            for key in ("title", "agency", "region", "service_type"):
                if item.get(key):
                    parsed[key] = item[key]
            if item.get("audiences"):
                parsed["audiences"] = sorted(set(item["audiences"]))
            if item.get("tags"):
                parsed["tags"] = sorted(set(item["tags"]))

            parsed.update(
                {
                    "source_id": source["id"],
                    "external_id": external_id,
                    "source_url": page_url,
                    "last_checked_at": now_iso(),
                    "raw": {
                        "catalog_item": item,
                        "raw_text": parsed.pop("raw_text", ""),
                    },
                }
            )
            if item.get("extract_pdfs", source.get("extract_pdfs", False)):
                parsed["attachments"] = enrich_attachments(parsed.get("attachments", []), parsed)
                merge_pdf_fields_into_parent(parsed)
                for attachment in parsed["attachments"]:
                    if attachment.get("document_type") == "pdf":
                        counter[f"pdf_{attachment.get('extraction_status', 'unknown')}"] += 1
            else:
                parsed["attachments"] = _contextualize_attachments(
                    parsed.get("attachments", []), parsed
                )
            parsed["eligibility_rules"] = extract_eligibility_rules(parsed.get("eligibility_text", ""))
            _record_result(counter, database.upsert_benefit(parsed))
            counter["fetched"] += 1
        except Exception as error:
            counter["failed_items"] += 1
            counter[f"failed_{external_id}"] += 1

    if not page_snapshots:
        raise RuntimeError(f"{source['id']} 的所有官方頁面都下載失敗")
    database.upsert_source_snapshot(
        source["id"], source["name"], source["url"],
        _sha256(json.dumps(page_snapshots, sort_keys=True).encode("utf-8")),
        "application/json", None, None,
    )
    counter["deactivated"] += database.deactivate_missing(
        "benefit", source["id"], seen_external_ids
    )
    return counter


def _parse_int(value: str) -> int | None:
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else None


def _pick(row: dict[str, str], aliases: list[str]) -> str:
    for alias in aliases:
        if alias in row and clean_inline(row[alias]):
            return clean_inline(row[alias])
    return ""


def _decode_csv(response: FetchResponse) -> list[dict[str, str]]:
    text = response.text().lstrip("\ufeff")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        row = {
            normalize_csv_key(str(key)): clean_inline(value)
            for key, value in raw_row.items()
            if key is not None
        }
        if any(row.values()):
            rows.append(row)
    return rows


def ingest_institution_csv(source: dict[str, Any], database: Database) -> Counter[str]:
    counter: Counter[str] = Counter()
    seen_external_ids: set[str] = set()
    response = fetch(source["url"])
    database.upsert_source_snapshot(
        source["id"], source["name"], source["url"], _sha256(response.body),
        response.content_type, response.etag, response.last_modified,
    )
    rows = _decode_csv(response)
    for index, row in enumerate(rows, start=1):
        name = _pick(row, ["機構名稱", "名稱", "機構"])
        if not name:
            continue
        external_id = _pick(row, ["編號", "序號", "機構代碼"]) or str(index)
        seen_external_ids.add(external_id)
        capacity_text = _pick(row, ["核定總床位數量", "核定床數", "開放床數", "核定總床位"])
        record = {
            "source_id": source["id"],
            "external_id": external_id,
            "name": name,
            "kind": _pick(row, ["屬性", "類型", "機構類型"]),
            "district": _pick(row, ["區域別", "行政區", "區別", "區"]),
            "address": _pick(row, ["地址", "機構地址"]),
            "phone": _pick(row, ["電話", "聯絡電話"]),
            "capacity": _parse_int(capacity_text),
            "attributes": row,
            "source_url": source.get("catalog_url", source["url"]),
            "last_checked_at": now_iso(),
        }
        _record_result(counter, database.upsert_institution(record))
        counter["fetched"] += 1
    counter["deactivated"] += database.deactivate_missing("institution", source["id"], seen_external_ids)
    return counter


def monitor_reference(source: dict[str, Any], database: Database) -> Counter[str]:
    counter: Counter[str] = Counter()
    response = fetch(source["url"])
    result = database.upsert_source_snapshot(
        source["id"], source["name"], source["url"], _sha256(response.body),
        response.content_type, response.etag, response.last_modified,
    )
    _record_result(counter, result)
    counter["fetched"] = 1
    return counter


def refresh(database: Database, config_path: str | None = None) -> dict[str, Any]:
    database.initialize()
    config = load_config(config_path)
    report: dict[str, Any] = {"started_at": now_iso(), "sources": [], "totals": Counter()}

    for source in enabled_sources(config):
        run_id = database.start_run(source["id"])
        try:
            kind = source.get("kind")
            if kind == "benefit_json_feed":
                counter = ingest_benefit_feed(source, database)
            elif kind == "benefit_pages":
                counter = ingest_benefit_pages(source, database)
            elif kind == "institution_csv":
                counter = ingest_institution_csv(source, database)
            else:
                counter = monitor_reference(source, database)
            database.finish_run(
                run_id, "success", counter["fetched"], counter["inserted"], counter["updated"]
            )
            item = {"source_id": source["id"], "status": "success", **dict(counter)}
            report["totals"].update(counter)
        except Exception as error:
            database.finish_run(run_id, "failed", error=str(error))
            item = {"source_id": source["id"], "status": "failed", "error": str(error)}
            report["totals"]["failed_sources"] += 1
        report["sources"].append(item)

    report["totals"] = dict(report["totals"])
    report["finished_at"] = now_iso()
    report["database_counts"] = database.counts()
    return report
