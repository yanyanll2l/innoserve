'''
負責處理福利頁面中的 PDF。
        - 判斷附件是不是 PDF
        - 下載 PDF
        - 讀取 PDF 文字
        - 找出資格與申請資訊
        - 將 PDF 掛到福利主體下
        - 判斷掃描型 PDF
        - 合併重複 PDF
'''

from __future__ import annotations

import hashlib
import io
import re
from typing import Any
from urllib.parse import unquote

from .db import now_iso
from .fetch import fetch
from .parse import classify, clean_inline


MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 60
MAX_EXTRACTED_CHARS = 120_000


SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "eligibility_text": ("服務對象", "補助對象", "申請資格", "適用對象", "資格條件"),
    "benefit_content": ("補助內容", "補助項目", "服務內容", "給付內容", "獎勵內容"),
    "application_steps": ("申請步驟", "申請流程", "辦理方式", "申請方式", "申辦流程"),
    "required_documents": ("必備文件", "應備文件", "檢附文件", "申請文件", "所需文件"),
    "deadline": ("申請截止", "申請期限", "受理期間", "申請期間", "截止日期"),
    "contact": ("聯絡方式", "洽詢電話", "聯絡電話", "洽辦資訊", "承辦單位"),
}


def is_pdf_attachment(attachment: dict[str, Any]) -> bool:
    url = unquote(str(attachment.get("url", ""))).lower()
    title = str(attachment.get("title", "")).lower()
    if ".pdf" in url or "icon=..pdf" in url:
        return True
    return title.startswith("pdf") or title.endswith(".pdf")


def _normalize_pdf_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ").replace("\u200b", "")
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_EXTRACTED_CHARS]


def _section_heading(line: str) -> tuple[str, str] | None:
    normalized = clean_inline(line)
    normalized = re.sub(r"^(?:[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*)", "", normalized)
    for field, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if normalized.startswith(alias):
                remainder = normalized[len(alias):]
                if not remainder or remainder[0] in "：: \t":
                    return field, clean_inline(remainder.lstrip("：: \t"))
    return None


def _extract_sections(text: str) -> dict[str, str]:
    values: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = _section_heading(line)
        if heading:
            current, inline_value = heading
            values.setdefault(current, [])
            if inline_value:
                values[current].append(inline_value)
            continue
        if current:
            values[current].append(line)
    result: dict[str, str] = {}
    for field, lines in values.items():
        value = clean_inline(" ".join(lines))
        if value:
            result[field] = value[:20_000]
    return result


def _sentences_with_amount(text: str) -> list[str]:
    sentences = re.split(r"(?<=[。；;])|\n", text)
    matched: list[str] = []
    for sentence in sentences:
        sentence = clean_inline(sentence)
        if not sentence:
            continue
        has_amount = bool(re.search(r"(?:NT\$|新臺幣)?\s*[\d,]+\s*(?:萬)?元|\d+\s*%", sentence))
        if has_amount and any(word in sentence for word in ("補助", "給付", "獎勵", "額度", "最高")):
            matched.append(sentence)
    return matched[:8]


def _find_deadline(text: str) -> str:
    patterns = [
        r"(?:申請截止|截止日期|申請期限)\s*[：:]?\s*([^\n。；]{4,80})",
        r"(?:受理期間|申請期間)\s*[：:]?\s*([^\n。；]{4,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_inline(match.group(1))
    return ""


def _find_agency(text: str) -> str:
    match = re.search(r"(?:主管機關|主辦機關|辦理機關)\s*[：:]\s*([^\n]{2,100})", text)
    return clean_inline(match.group(1)) if match else ""


def extract_welfare_fields(text: str, title: str = "") -> dict[str, Any]:
    normalized = _normalize_pdf_text(text)
    fields: dict[str, Any] = _extract_sections(normalized)
    amounts = _sentences_with_amount(normalized)
    if amounts:
        fields["subsidy_amounts"] = amounts
    if not fields.get("deadline"):
        deadline = _find_deadline(normalized)
        if deadline:
            fields["deadline"] = deadline
    agency = _find_agency(normalized)
    if agency:
        fields["agency"] = agency

    _, tags, audiences = classify(title, normalized)
    fields["audiences"] = [audience for audience in audiences if audience != "一般民眾"]
    fields["tags"] = tags
    if "不限地區" in normalized or "全國" in normalized:
        fields["region"] = "不限地區"
    elif "臺北市" in normalized or "台北市" in normalized:
        fields["region"] = "臺北市"
    return fields


def extract_pdf_attachment(
    attachment: dict[str, Any],
    parent: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(attachment)
    enriched.update(
        {
            "relation": "supplemental_document",
            "document_type": "pdf",
            "parent_benefit_title": parent.get("title", ""),
            "audiences": list(parent.get("audiences", [])),
            "region": parent.get("region", ""),
            "agency": parent.get("agency", ""),
            "source_url": attachment.get("url", ""),
            "official_page_url": parent.get("source_url", ""),
            "last_checked_at": now_iso(),
        }
    )
    try:
        from pypdf import PdfReader
    except ImportError:
        enriched.update({"extraction_status": "parser_unavailable", "extraction_error": "缺少 pypdf"})
        return enriched

    try:
        response = fetch(str(attachment.get("url", "")))
        if len(response.body) > MAX_PDF_BYTES:
            enriched.update(
                {
                    "extraction_status": "too_large",
                    "file_size_bytes": len(response.body),
                    "extraction_error": f"PDF 超過 {MAX_PDF_BYTES // 1024 // 1024} MB 限制",
                }
            )
            return enriched
        if not response.body.startswith(b"%PDF"):
            enriched.update(
                {
                    "extraction_status": "not_pdf",
                    "file_size_bytes": len(response.body),
                    "extraction_error": f"來源回傳 {response.content_type}，不是 PDF",
                }
            )
            return enriched

        digest = hashlib.sha256(response.body).hexdigest()
        reader = PdfReader(io.BytesIO(response.body))
        page_count = len(reader.pages)
        pages_to_read = min(page_count, MAX_PDF_PAGES)
        page_texts: list[str] = []
        for page in reader.pages[:pages_to_read]:
            page_texts.append(page.extract_text() or "")
        extracted_text = _normalize_pdf_text("\n".join(page_texts))
        status = "extracted" if extracted_text else "needs_ocr"
        fields = extract_welfare_fields(extracted_text, str(attachment.get("title", ""))) if extracted_text else {}
        enriched.update(
            {
                "extraction_status": status,
                "mime_type": "application/pdf",
                "file_size_bytes": len(response.body),
                "page_count": page_count,
                "pages_extracted": pages_to_read,
                "content_hash": digest,
                "extracted_text": extracted_text,
                "extracted_fields": fields,
            }
        )
        if fields.get("audiences"):
            enriched["audiences"] = sorted(set(enriched["audiences"] + fields["audiences"]))
        if fields.get("region"):
            enriched["region"] = fields["region"]
        if fields.get("agency"):
            enriched["agency"] = fields["agency"]
        return enriched
    except Exception as error:
        enriched.update({"extraction_status": "failed", "extraction_error": str(error)[:500]})
        return enriched


def enrich_attachments(attachments: list[dict[str, Any]], parent: dict[str, Any]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for attachment in attachments:
        if is_pdf_attachment(attachment):
            enriched.append(extract_pdf_attachment(attachment, parent))
        else:
            item = dict(attachment)
            item.update(
                {
                    "relation": "supplemental_document",
                    "document_type": "other",
                    "parent_benefit_title": parent.get("title", ""),
                    "audiences": list(parent.get("audiences", [])),
                    "region": parent.get("region", ""),
                    "agency": parent.get("agency", ""),
                    "source_url": attachment.get("url", ""),
                    "official_page_url": parent.get("source_url", ""),
                    "extraction_status": "unsupported_type",
                }
            )
            enriched.append(item)

    # 同一份 PDF 可能由多個局處網址重複提供；保留一份內容並記下其他官方網址。
    deduplicated: list[dict[str, Any]] = []
    by_content_hash: dict[str, dict[str, Any]] = {}
    for item in enriched:
        digest = str(item.get("content_hash", ""))
        if digest and digest in by_content_hash:
            existing = by_content_hash[digest]
            urls = set(existing.get("alternate_urls", []))
            urls.add(str(item.get("source_url", "")))
            urls.discard(str(existing.get("source_url", "")))
            existing["alternate_urls"] = sorted(url for url in urls if url)
            continue
        deduplicated.append(item)
        if digest:
            by_content_hash[digest] = item
    return deduplicated


def merge_pdf_fields_into_parent(parent: dict[str, Any]) -> dict[str, Any]:
    attachments = parent.get("attachments", [])
    pdf_fields = [
        attachment.get("extracted_fields", {})
        for attachment in attachments
        if attachment.get("extraction_status") == "extracted"
    ]
    field_map = {
        "eligibility_text": "eligibility_text",
        "benefit_content": "service_content",
        "application_steps": "application_method",
        "required_documents": "required_documents",
    }
    for pdf_key, parent_key in field_map.items():
        if parent.get(parent_key):
            continue
        value = next((fields.get(pdf_key) for fields in pdf_fields if fields.get(pdf_key)), "")
        if value:
            parent[parent_key] = value

    if not parent.get("fees"):
        amounts: list[str] = []
        for fields in pdf_fields:
            amounts.extend(fields.get("subsidy_amounts", []))
        if amounts:
            parent["fees"] = " ".join(dict.fromkeys(amounts))[:20_000]

    inherited_audiences = set(parent.get("audiences", []))
    for fields in pdf_fields:
        inherited_audiences.update(fields.get("audiences", []))
    if inherited_audiences:
        parent["audiences"] = sorted(inherited_audiences)
    return parent
