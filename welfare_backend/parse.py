'''
負責把官方 HTML 網頁整理成系統統一格式。
處理：
    - 網頁文字
    - 標題
    - 福利內容
    - 服務對象
    - 申請方式
    - 收費方式
    - 聯絡資料
    - PDF 連結
    - 福利分類
    - 資格規則
'''

from __future__ import annotations

import hashlib
import html
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin


SPACE_RE = re.compile(r"[\t\r\f\v ]+")
BLANK_RE = re.compile(r"\n{3,}")


class _VisibleTextParser(HTMLParser):
    block_tags = {
        "article", "br", "dd", "div", "dl", "dt", "footer", "h1", "h2", "h3",
        "h4", "header", "li", "main", "p", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._ignored_depth = 0
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored_depth += 1
        if self._ignored_depth:
            return
        if tag in self.block_tags:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self._link_href = urljoin(self.base_url, href) if href else None
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag == "a" and self._link_href:
            label = clean_inline("".join(self._link_text))
            self.links.append({"title": label, "url": self._link_href})
            self._link_href = None
            self._link_text = []
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.parts.append(data)
        if self._link_href:
            self._link_text.append(data)


def clean_inline(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value).replace("\u3000", " ").replace("\xa0", " ")
    return SPACE_RE.sub(" ", value).strip()


def visible_text(document: str, base_url: str = "") -> tuple[str, list[dict[str, str]]]:
    parser = _VisibleTextParser(base_url)
    parser.feed(document)
    lines = [clean_inline(line) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return BLANK_RE.sub("\n\n", text), parser.links


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return clean_inline(match.group(1))
    return ""


def _main_section(text: str, title: str) -> str:
    end_markers = ["此頁資訊有幫助嗎", "滿意度調查", "回上一頁"]
    end = len(text)
    for marker in end_markers:
        position = text.find(marker)
        if position >= 0:
            end = min(end, position)
    start = 0
    field_starts: list[int] = []
    numbered_prefix = r"(?:[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*)?"
    for label in [
        "申請方式", "申辦流程", "申辦資格", "業務聯絡窗口", "聯絡窗口",
        "服務內容說明", "服務內容", "標準及規定", "洽辦單位", "服務對象",
    ]:
        match = re.search(
            rf"(?:^|\n){numbered_prefix}{re.escape(label)}(?:\s*[：:]|\n|\s)", text[:end]
        )
        if match:
            field_starts.append(match.start())
    if field_starts:
        start = min(field_starts)
    elif title:
        match = re.search(rf"(?:^|\n){re.escape(title)}(?:\n|$)", text[:end])
        if match:
            start = match.start()
    return text[start:end].strip()


FIELD_LABELS = [
    "申請方式", "申辦流程", "洽辦單位", "服務內容說明", "服務內容", "服務對象",
    "申辦資格", "標準及規定", "應備文件", "應備物品", "收費方式", "補助標準", "給付標準",
    "聯絡電話", "業務聯絡窗口", "聯絡窗口", "地址", "電子信箱",
    "作業天數", "參考資料", "備註", "更新日期", "長照交通預約平台",
]


def _extract_fields(text: str) -> dict[str, str]:
    labels_pattern = "|".join(re.escape(label) for label in sorted(FIELD_LABELS, key=len, reverse=True))
    numbered_prefix = r"(?:[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*)?"
    pattern = re.compile(
        rf"(?:^|\n){numbered_prefix}(?P<label>{labels_pattern})\s*[：:]?\s*"
        rf"(?P<value>.*?)(?=\n{numbered_prefix}(?:{labels_pattern})\s*[：:]?\s*|$)",
        re.DOTALL,
    )
    fields: dict[str, str] = {}
    for match in pattern.finditer(text):
        label = match.group("label")
        value = clean_inline(match.group("value").replace("\n", " "))
        if value and len(value) < 12000:
            fields[label] = value
    return fields


def classify(title: str, text: str) -> tuple[str, list[str], list[str]]:
    haystack = f"{title} {text}"
    categories = [
        ("獎學金", ["獎學金", "獎助學金"]),
        ("就學貸款", ["就學貸款", "助學貸款"]),
        ("就學補助", ["弱勢學生", "弱勢助學", "學雜費減免", "就學費用補助", "助學金"]),
        ("住宿補助", ["住宿補貼", "住宿補助", "住宿優惠", "宿舍"]),
        ("勞工補助", ["勞工補助", "就業獎勵", "就業促進"]),
        ("失業給付", ["失業給付", "失業認定"]),
        ("職業訓練", ["職業訓練", "職訓"]),
        ("育兒與就業支持", ["育嬰留職停薪", "就業支持", "友善職場"]),
        ("老人津貼", ["老人生活津貼", "老人特別照顧津貼", "老人津貼"]),
        ("急難救助", ["急難救助", "急難紓困"]),
        ("身心障礙福利", ["身心障礙者生活補助", "身心障礙福利", "身心障礙年金"]),
        ("住宅補助", ["租金補貼", "住宅補助", "房屋租金補貼"]),
        ("育兒福利", ["育兒津貼", "幼兒就學補助", "托育補助"]),
        ("申請與給付", ["申請及給付", "如何申請", "申請長照"]),
        ("交通接送", ["交通接送", "接送"]),
        ("居家照顧", ["居家服務", "居家照顧"]),
        ("日間照顧", ["日間照顧", "日照"]),
        ("家庭托顧", ["家庭托顧"]),
        ("餐飲服務", ["營養餐飲", "送餐"]),
        ("輔具與無障礙", ["輔具", "無障礙"]),
        ("機構住宿", ["住宿式", "收容安置", "機構服務", "護理之家"]),
        ("照顧者支持", ["家庭照顧者", "喘息"]),
        ("失智照顧", ["失智"]),
        ("醫療照護", ["醫師照護", "醫療費用", "氣切"]),
    ]
    service_type = "其他福利"
    tags: list[str] = []
    for category, keywords in categories:
        if any(keyword in haystack for keyword in keywords):
            tags.append(category)
    for category, keywords in categories:
        if any(keyword in title for keyword in keywords):
            service_type = category
            break
    if service_type == "其他福利" and tags:
        service_type = tags[0]

    audiences: list[str] = []
    if any(keyword in haystack for keyword in ["學生", "就學", "大專院校"]):
        audiences.append("學生")
    if any(keyword in haystack for keyword in ["勞工", "就業", "職場", "上班族"]):
        audiences.append("上班族")
    if any(keyword in haystack for keyword in ["老人", "長者", "65歲", "長照", "失能", "失智"]):
        audiences.append("長者")
    if not audiences:
        audiences.append("一般民眾")
    return service_type, sorted(set(tags)), audiences


def extract_eligibility_rules(text: str) -> dict[str, Any]:
    rules: dict[str, Any] = {"machine_extracted": True, "requires_official_assessment": False}
    if any(
        phrase in text
        for phrase in ("實際居住本市", "居住本市", "設籍本市", "設籍臺北市", "設籍台北市")
    ):
        rules["city"] = "臺北市"
    levels = [int(value) for value in re.findall(r"長照(?:需要)?等級第\s*(\d+)\s*級", text)]
    if levels:
        rules["minimum_long_term_care_level"] = min(levels)
        rules["requires_official_assessment"] = True

    alternatives: list[dict[str, Any]] = []
    if re.search(r"65\s*歲以上", text):
        alternatives.append({"minimum_age": 65})
    if re.search(r"55\s*歲以上原住民|原住民.{0,10}55\s*歲以上", text):
        alternatives.append({"minimum_age": 55, "indigenous": True})
    if re.search(r"50.{0,8}失智|失智.{0,8}50\s*歲", text):
        alternatives.append({"minimum_age": 50, "dementia": True})
    if "身心障礙" in text:
        alternatives.append({"disability": True})
    if "失智" in text and not any(item.get("dementia") for item in alternatives):
        alternatives.append({"dementia": True})
    if alternatives:
        unique = {json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in alternatives}
        rules["any_of"] = list(unique.values())
    return rules


def content_hash(record: dict[str, Any]) -> str:
    # raw 是供除錯的原始來源快照；查核時間與欄位排列也不應觸發內容異動。
    ignored = {"last_checked_at", "first_seen_at", "last_seen_at", "id", "raw"}

    def canonicalize(value: Any, parent_key: str = "") -> Any:
        if isinstance(value, dict):
            return {
                key: canonicalize(item, key)
                for key, item in sorted(value.items())
                if key not in ignored
            }
        if isinstance(value, list):
            items = [canonicalize(item) for item in value]
            if parent_key == "attachments":
                return sorted(
                    items,
                    key=lambda item: str(item.get("url", "")) if isinstance(item, dict) else str(item),
                )
            if parent_key in {"audiences", "tags", "alternate_urls"}:
                return sorted(items, key=str)
            return items
        return value

    payload = canonicalize(record)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_taipei_detail(
    document: str,
    source_url: str,
    fallback_title: str = "",
    agency: str = "",
) -> dict[str, Any]:
    text, links = visible_text(document, source_url)
    title = fallback_title or _first_match(
        [r"<h2[^>]*>\s*(?:<[^>]+>)*\s*([^<]+)", r"<title[^>]*>\s*([^<\-|]+)"],
        document,
    )
    title = re.sub(r"^(臺北市政府(?:社會局|衛生局)[－-])", "", title).strip()
    main = _main_section(text, title)
    fields = _extract_fields(main)
    service_content = (
        fields.get("服務內容說明") or fields.get("服務內容")
        or fields.get("標準及規定") or main
    )
    eligibility = fields.get("服務對象") or fields.get("申辦資格", "")
    application = fields.get("申請方式") or fields.get("申辦流程") or fields.get("洽辦單位", "")
    documents = fields.get("應備文件") or fields.get("應備物品", "")
    fees = fields.get("收費方式") or fields.get("補助標準") or fields.get("給付標準", "")
    contact_parts = [
        fields.get(key, "")
        for key in ("業務聯絡窗口", "聯絡窗口", "聯絡電話", "地址", "電子信箱")
    ]
    contact = "；".join(part for part in contact_parts if part)
    updated = _first_match([r"資料更新[:：]\s*([^\n]+)", r"更新日期\s*([^\n]+)"], text)
    service_type, tags, audiences = classify(title, f"{main} {eligibility}")
    attachments = [
        item for item in links
        if item["url"].lower().endswith((".pdf", ".doc", ".docx", ".odt", ".xls", ".xlsx"))
        or "Download.ashx" in item["url"]
    ]
    return {
        "title": title or fallback_title or "未命名福利",
        "agency": agency,
        "region": "臺北市",
        "service_type": service_type,
        "audiences": audiences,
        "tags": tags,
        "eligibility_text": eligibility,
        "eligibility_rules": extract_eligibility_rules(eligibility),
        "service_content": service_content,
        "application_method": application,
        "required_documents": documents,
        "fees": fees,
        "contact": contact,
        "source_url": source_url,
        "source_updated_at": updated,
        "attachments": attachments,
        "raw_text": main[:20000],
    }


def normalize_csv_key(value: str) -> str:
    return clean_inline(value).replace("\ufeff", "")
