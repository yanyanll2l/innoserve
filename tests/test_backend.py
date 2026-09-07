'''
使用假資料，用來離線測試
- 網頁解析是否正常
- PDF 欄位抽取是否正常
- 資料庫是否正常
- 資料更新偵測是否正常
- 搜尋是否正常
- 資格比對是否正常
'''
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from welfare_backend.db import Database
from welfare_backend.matcher import match_benefit
from welfare_backend.parse import content_hash, parse_taipei_detail
from welfare_backend.pdf_extract import extract_welfare_fields, merge_pdf_fields_into_parent


class ParserTests(unittest.TestCase):
    # 測試臺北市福利(假網站) HTML 是否能正確解析
    def test_taipei_detail_fields_and_rules(self) -> None:
        document = """
        <html><head><title>臺北市政府社會局-長期照顧交通接送服務</title></head>
        <body><main><h2>長期照顧交通接送服務</h2>
        <table>
          <tr><th>申請方式</th><td>撥打 1966 申請。</td></tr>
          <tr><th>服務內容說明</th><td>提供就醫交通接送。</td></tr>
          <tr><th>服務對象</th><td>實際居住本市，長照需要等級第2級以上，65歲以上老人；55歲以上原住民。</td></tr>
          <tr><th>收費方式</th><td>依身分別部分負擔。</td></tr>
        </table>
        <p>資料更新：115-06-05 15:53</p><p>此頁資訊有幫助嗎?</p></main></body></html>
        """
        item = parse_taipei_detail(document, "https://example.gov.taipei/item")
        self.assertEqual(item["title"], "長期照顧交通接送服務")
        self.assertEqual(item["service_type"], "交通接送")
        self.assertIn("撥打 1966", item["application_method"])
        self.assertEqual(item["eligibility_rules"]["city"], "臺北市")
        self.assertEqual(item["eligibility_rules"]["minimum_long_term_care_level"], 2)
        self.assertIn("長者", item["audiences"])

    def test_title_has_priority_when_classifying(self) -> None:
        document = """
        <html><body><h2>失能者營養餐飲服務</h2>
        <table><tr><th>服務內容說明</th><td>已有居家服務者仍需個別評估。</td></tr>
        <tr><th>服務對象</th><td>65歲以上失能長者。</td></tr></table>
        <p>此頁資訊有幫助嗎?</p></body></html>
        """
        item = parse_taipei_detail(document, "https://example.gov.taipei/meal", "失能者營養餐飲服務")
        self.assertEqual(item["service_type"], "餐飲服務")

    # 測試 PDF 文字能不能被拆成結構化欄位
    def test_pdf_text_is_structured_as_supplemental_welfare_content(self) -> None:
        text = """
        主管機關：海洋委員會海巡署
        服務對象：參與海上救難的一般民眾與上班族
        補助內容：救助人命每救活 1 人最高 5 萬元。
        申請步驟：事發後立即通報，並於六個月內提出申請。
        必備文件：申請書、身分證影本、救援紀錄。
        申請截止：2026/12/31
        哪裡的人可以申請：不限地區
        """
        fields = extract_welfare_fields(text, "海上救難獎勵金")
        self.assertIn("最高 5 萬元", fields["benefit_content"])
        self.assertIn("上班族", fields["audiences"])
        self.assertEqual(fields["region"], "不限地區")
        self.assertEqual(fields["agency"], "海洋委員會海巡署")

    def test_pdf_heading_and_value_on_same_line(self) -> None:
        fields = extract_welfare_fields(
            "服務對象 65歲以上失能長者\n服務內容 提供送餐服務\n申請方式 撥打1966",
            "營養餐飲服務",
        )
        self.assertEqual(fields["eligibility_text"], "65歲以上失能長者")
        self.assertEqual(fields["benefit_content"], "提供送餐服務")
        self.assertEqual(fields["application_steps"], "撥打1966")

    # 測試 PDF 的內容能不能補到原本缺少資料的福利主體
    def test_pdf_fields_fill_missing_parent_fields(self) -> None:
        parent = {
            "audiences": ["一般民眾"],
            "eligibility_text": "",
            "required_documents": "",
            "attachments": [{
                "extraction_status": "extracted",
                "extracted_fields": {
                    "eligibility_text": "65歲以上長者",
                    "required_documents": "申請書及身分證影本",
                    "audiences": ["長者"],
                },
            }],
        }
        merge_pdf_fields_into_parent(parent)
        self.assertEqual(parent["eligibility_text"], "65歲以上長者")
        self.assertIn("長者", parent["audiences"])
        self.assertIn("申請書", parent["required_documents"])

    def test_nested_attachment_check_time_does_not_change_hash(self) -> None:
        first = {"attachments": [{"url": "https://example/a.pdf", "last_checked_at": "A"}]}
        second = {"attachments": [{"url": "https://example/a.pdf", "last_checked_at": "B"}]}
        self.assertEqual(content_hash(first), content_hash(second))

# 建立資料庫相關的測試類別
class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "test.sqlite3")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def benefit(content: str = "提供交通接送") -> dict:
        return {
            "source_id": "test",
            "external_id": "1",
            "title": "測試交通接送",
            "agency": "測試機關",
            "region": "臺北市",
            "service_type": "交通接送",
            "audiences": ["長者"],
            "tags": ["交通接送"],
            "eligibility_text": "居住臺北市且長照等級第2級以上",
            "eligibility_rules": {
                "city": "臺北市",
                "minimum_long_term_care_level": 2,
                "requires_official_assessment": True,
                "any_of": [{"minimum_age": 65}],
            },
            "service_content": content,
            "application_method": "撥打1966",
            "required_documents": "",
            "fees": "",
            "contact": "1966",
            "source_url": "https://example.gov.taipei/item",
            "source_updated_at": "115-01-01",
            "attachments": [],
            "raw": {},
        }

    # 測試3件事: 1. 新增資料 2. 重複資料不產生更新 3. 內容變更時可以偵測
    def test_upsert_search_and_change_detection(self) -> None:
        self.assertEqual(self.database.upsert_benefit(self.benefit()), "inserted")
        self.assertEqual(self.database.upsert_benefit(self.benefit()), "unchanged")
        self.assertEqual(self.database.upsert_benefit(self.benefit("更新後內容")), "updated")
        results = self.database.search_benefits("交通", audience="長者")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["service_content"], "更新後內容")
        self.assertEqual(len(self.database.recent_changes()), 2)

    def test_raw_or_attachment_order_does_not_trigger_change(self) -> None:
        first = self.benefit()
        first["attachments"] = [{"title": "A", "url": "https://example/a"}, {"title": "B", "url": "https://example/b"}]
        first["raw"] = {"feed_item": {"order": 1}}
        self.database.upsert_benefit(first)
        second = self.benefit()
        second["attachments"] = list(reversed(first["attachments"]))
        second["raw"] = {"feed_item": {"order": 2}}
        self.assertEqual(self.database.upsert_benefit(second), "unchanged")

    def test_missing_record_is_deactivated_and_can_return(self) -> None:
        record = self.benefit()
        self.database.upsert_benefit(record)
        self.assertEqual(self.database.deactivate_missing("benefit", "test", set()), 1)
        self.assertEqual(self.database.search_benefits(), [])
        self.assertEqual(self.database.upsert_benefit(record), "reactivated")
        self.assertEqual(len(self.database.search_benefits()), 1)

    def test_match_is_cautious(self) -> None:
        self.database.upsert_benefit(self.benefit())
        benefit = self.database.search_benefits()[0]
        result = match_benefit(
            benefit,
            {"age": 72, "city": "臺北市", "long_term_care_level": 3},
        )
        self.assertEqual(result["status"], "需要進一步確認")
        self.assertIn("主管機關", result["disclaimer"])


if __name__ == "__main__":
    unittest.main()
