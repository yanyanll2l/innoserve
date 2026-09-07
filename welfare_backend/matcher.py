'''
個人條件與福利資格的初步比對。
這不是政府正式審核，只是提供導覽建議。
可能的結果有：
    可能符合
    需要進一步確認
    目前較不符合
'''

from __future__ import annotations

from typing import Any


DISCLAIMER = "本結果僅供福利導覽與初步篩選，實際資格及補助額度以主管機關評估、核定為準。"


def _alternative_status(alternative: dict[str, Any], profile: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    unknown = False
    for field, expected in alternative.items():
        if field == "minimum_age":
            age = profile.get("age")
            if age is None:
                unknown = True
                reasons.append(f"需要確認年齡是否滿 {expected} 歲")
            elif int(age) < int(expected):
                return "no", [f"年齡未滿 {expected} 歲"]
            else:
                reasons.append(f"年齡已滿 {expected} 歲")
        else:
            actual = profile.get(field)
            if actual is None:
                unknown = True
                reasons.append(f"需要確認 {field}")
            elif bool(actual) != bool(expected):
                return "no", [f"不符合條件 {field}"]
            else:
                reasons.append(f"符合條件 {field}")
    return ("unknown" if unknown else "yes"), reasons


def match_benefit(benefit: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    rules = benefit.get("eligibility_rules") or {}
    reasons: list[str] = []
    hard_mismatch = False
    unknown = False

    required_city = rules.get("city")
    city = profile.get("city")
    if required_city:
        if not city:
            unknown = True
            reasons.append(f"需要確認是否居住於{required_city}")
        elif required_city not in str(city):
            hard_mismatch = True
            reasons.append(f"服務要求居住於{required_city}")
        else:
            reasons.append(f"居住地符合{required_city}")

    minimum_level = rules.get("minimum_long_term_care_level")
    level = profile.get("long_term_care_level")
    if minimum_level is not None:
        if level is None:
            unknown = True
            reasons.append(f"需要由長照管理中心評估長照等級（此服務要求第 {minimum_level} 級以上）")
        elif int(level) < int(minimum_level):
            hard_mismatch = True
            reasons.append(f"長照等級低於第 {minimum_level} 級")
        else:
            reasons.append(f"長照等級達第 {minimum_level} 級以上")

    alternatives = rules.get("any_of") or []
    if alternatives:
        results = [_alternative_status(alternative, profile) for alternative in alternatives]
        if any(status == "yes" for status, _ in results):
            matched_reasons = next(reason for status, reason in results if status == "yes")
            reasons.extend(matched_reasons)
        elif any(status == "unknown" for status, _ in results):
            unknown = True
            reasons.append("服務對象條件尚有資料需要確認")
        else:
            hard_mismatch = True
            reasons.append("目前資料未符合服務對象的任一條件")

    if hard_mismatch:
        status = "目前較不符合"
    elif unknown or rules.get("requires_official_assessment"):
        status = "需要進一步確認"
    else:
        status = "可能符合"

    return {
        "benefit_id": benefit["id"],
        "title": benefit["title"],
        "status": status,
        "reasons": reasons or ["官方頁面未提供可完整機器判斷的結構化資格"],
        "source_url": benefit["source_url"],
        "last_checked_at": benefit["last_seen_at"],
        "disclaimer": DISCLAIMER,
    }


def match_many(benefits: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    priority = {"可能符合": 0, "需要進一步確認": 1, "目前較不符合": 2}
    results = [match_benefit(benefit, profile) for benefit in benefits]
    return sorted(results, key=lambda item: (priority[item["status"]], item["title"]))

