"""
マネーフォワードCSV解析・家計分析のテスト
"""
from datetime import date

from backend.services.mf_analyzer import (
    parse_mf_csv,
    monthly_summary,
    compare_yoy,
    detect_trend,
    build_ai_analysis_payload,
)


def make_txn(
    d="2026/05/01", desc="テスト", amount=-1000, major="食費", minor="食料品",
    is_transfer=False, is_target=True, mf_id=None,
):
    y, m, dd = map(int, d.split("/"))
    return {
        "date": date(y, m, dd),
        "description": desc,
        "amount_yen": amount,
        "institution": "テスト銀行",
        "category_major": major,
        "category_minor": minor,
        "memo": "",
        "is_transfer": is_transfer,
        "is_target": is_target,
        "mf_id": mf_id,
    }


# ──────────────────────────────────────────────
# CSVパース
# ──────────────────────────────────────────────

class TestParseCsv:
    HEADER = "計算対象\t日付\t内容\t金額（円）\t保有金融機関\t大項目\t中項目\tメモ\t振替\tID"

    def test_tab_separated_utf8(self):
        csv_text = f"{self.HEADER}\n1\t2026/05/10\tスーパー\t-3,500\t楽天カード\t食費\t食料品\t\t0\tabc123"
        txns = parse_mf_csv(csv_text.encode("utf-8"))
        assert len(txns) == 1
        t = txns[0]
        assert t["date"] == date(2026, 5, 10)
        assert t["amount_yen"] == -3500
        assert t["category_major"] == "食費"
        assert t["mf_id"] == "abc123"
        assert not t["is_transfer"]
        assert t["is_target"]

    def test_cp932_encoding(self):
        csv_text = f"{self.HEADER}\n1\t2026/05/10\tコンビニ\t-500\t現金\t食費\t\t\t0\txyz"
        txns = parse_mf_csv(csv_text.encode("cp932"))
        assert len(txns) == 1
        assert txns[0]["description"] == "コンビニ"

    def test_comma_separated(self):
        header = self.HEADER.replace("\t", ",")
        csv_text = f'{header}\n1,2026/05/10,ドラッグストア,-800,現金,日用品,,,0,id1'
        txns = parse_mf_csv(csv_text.encode("utf-8"))
        assert len(txns) == 1
        assert txns[0]["amount_yen"] == -800

    def test_excel_corrupted_id_treated_as_null(self):
        csv_text = f"{self.HEADER}\n1\t2026/05/10\tテスト\t-100\t現金\t食費\t\t\t0\t#NAME?"
        txns = parse_mf_csv(csv_text.encode("utf-8"))
        assert txns[0]["mf_id"] is None

    def test_transfer_flag(self):
        csv_text = f"{self.HEADER}\n1\t2026/05/10\t口座振替\t-50000\t銀行\t現金・カード\t\t\t1\tid2"
        txns = parse_mf_csv(csv_text.encode("utf-8"))
        assert txns[0]["is_transfer"]

    def test_skips_empty_date_rows(self):
        csv_text = f"{self.HEADER}\n1\t\tメモ行\t0\t\t\t\t\t0\t"
        txns = parse_mf_csv(csv_text.encode("utf-8"))
        assert len(txns) == 0


# ──────────────────────────────────────────────
# 月次集計
# ──────────────────────────────────────────────

class TestMonthlySummary:
    def test_basic_expense_aggregation(self):
        txns = [
            make_txn(amount=-3000, major="食費"),
            make_txn(amount=-2000, major="食費"),
            make_txn(amount=-5000, major="住宅"),
        ]
        s = monthly_summary(txns, 2026, 5)
        assert s["total_expense_yen"] == 10000
        cats = {c["name"]: c["amount_yen"] for c in s["categories"]}
        assert cats["食費"] == 5000
        assert cats["住宅"] == 5000

    def test_refund_nets_against_expense(self):
        """返金・キャッシュバックが支出から相殺される（回帰テスト：以前は無視されていた）"""
        txns = [
            make_txn(amount=-10000, major="趣味・娯楽"),
            make_txn(amount=3000, major="趣味・娯楽", desc="返金"),  # 返品
        ]
        s = monthly_summary(txns, 2026, 5)
        assert s["total_expense_yen"] == 7000

    def test_income_not_counted_as_expense(self):
        txns = [
            make_txn(amount=300000, major="収入", minor="給与"),
            make_txn(amount=-5000, major="食費"),
        ]
        s = monthly_summary(txns, 2026, 5)
        assert s["total_income_yen"] == 300000
        assert s["total_expense_yen"] == 5000

    def test_transfer_excluded(self):
        txns = [
            make_txn(amount=-100000, major="現金・カード", is_transfer=True),
            make_txn(amount=-5000, major="食費"),
        ]
        s = monthly_summary(txns, 2026, 5)
        assert s["total_expense_yen"] == 5000

    def test_not_target_excluded(self):
        txns = [
            make_txn(amount=-9999, major="食費", is_target=False),
            make_txn(amount=-5000, major="食費"),
        ]
        s = monthly_summary(txns, 2026, 5)
        assert s["total_expense_yen"] == 5000

    def test_other_month_excluded(self):
        txns = [
            make_txn(d="2026/04/30", amount=-9999),
            make_txn(d="2026/05/01", amount=-5000),
        ]
        s = monthly_summary(txns, 2026, 5)
        assert s["total_expense_yen"] == 5000


# ──────────────────────────────────────────────
# 前年同月比較
# ──────────────────────────────────────────────

class TestCompareYoy:
    def _summary(self, cats: dict, year=2026, month=5):
        return {
            "year": year, "month": month,
            "total_expense_yen": sum(cats.values()),
            "total_income_yen": 0,
            "categories": [
                {"name": k, "amount_yen": v, "subcategories": []}
                for k, v in cats.items()
            ],
            "transaction_count": len(cats),
        }

    def test_increase_flagged_above_threshold(self):
        cur = self._summary({"食費": 55000})
        prev = self._summary({"食費": 50000}, year=2025)
        result = compare_yoy(cur, prev, threshold=0.10)
        food = next(r for r in result if r["category"] == "食費")
        assert food["flagged"]  # +10%
        assert food["change_pct"] == 10.0

    def test_small_change_not_flagged(self):
        cur = self._summary({"食費": 52000})
        prev = self._summary({"食費": 50000}, year=2025)
        result = compare_yoy(cur, prev, threshold=0.10)
        food = next(r for r in result if r["category"] == "食費")
        assert not food["flagged"]  # +4%

    def test_new_category_flagged(self):
        """前年ゼロ→今年発生のカテゴリがフラグされる（回帰テスト）"""
        cur = self._summary({"サブスク": 5000})
        prev = self._summary({}, year=2025)
        result = compare_yoy(cur, prev, threshold=0.10)
        sub = next(r for r in result if r["category"] == "サブスク")
        assert sub["flagged"]
        assert sub["is_new"]
        assert sub["direction"] == "up"

    def test_tiny_new_category_not_flagged(self):
        """1,000円未満の新出カテゴリはノイズとして無視"""
        cur = self._summary({"雑費": 500})
        prev = self._summary({}, year=2025)
        result = compare_yoy(cur, prev, threshold=0.10)
        z = next(r for r in result if r["category"] == "雑費")
        assert not z["flagged"]

    def test_disappeared_category_flagged(self):
        cur = self._summary({})
        prev = self._summary({"保険": 20000}, year=2025)
        result = compare_yoy(cur, prev, threshold=0.10)
        ins = next(r for r in result if r["category"] == "保険")
        assert ins["flagged"]
        assert ins["change_pct"] == -100.0


# ──────────────────────────────────────────────
# トレンド検出
# ──────────────────────────────────────────────

class TestDetectTrend:
    def _summary(self, cats: dict, month: int):
        return {
            "year": 2026, "month": month,
            "total_expense_yen": sum(cats.values()),
            "total_income_yen": 0,
            "categories": [
                {"name": k, "amount_yen": v, "subcategories": []}
                for k, v in cats.items()
            ],
            "transaction_count": len(cats),
        }

    def test_three_month_increase_detected(self):
        summaries = [
            self._summary({"食費": 40000}, 3),
            self._summary({"食費": 45000}, 4),
            self._summary({"食費": 50000}, 5),
        ]
        trends = detect_trend(summaries)
        assert len(trends) == 1
        assert trends[0]["category"] == "食費"
        assert trends[0]["increase_pct_3m"] == 25.0

    def test_flat_not_detected(self):
        summaries = [
            self._summary({"食費": 40000}, 3),
            self._summary({"食費": 40000}, 4),
            self._summary({"食費": 40000}, 5),
        ]
        assert detect_trend(summaries) == []

    def test_midway_category_no_month_misalignment(self):
        """途中から登場したカテゴリの月がズレない（回帰テスト）

        以前の実装では「初月に存在しないカテゴリ」の系列が
        後ろに詰められ、月の対応がズレて誤検出していた。
        """
        summaries = [
            self._summary({"食費": 40000}, 1),                     # サブスクなし
            self._summary({"食費": 40000, "サブスク": 3000}, 2),
            self._summary({"食費": 40000, "サブスク": 2000}, 3),   # 減少
            self._summary({"食費": 40000, "サブスク": 1000}, 4),   # 減少
        ]
        trends = detect_trend(summaries)
        # サブスクは減少中なのでトレンド検出されてはいけない
        assert all(t["category"] != "サブスク" for t in trends)

    def test_less_than_three_months_returns_empty(self):
        summaries = [self._summary({"食費": 40000}, 5)]
        assert detect_trend(summaries) == []


# ──────────────────────────────────────────────
# AI分析ペイロード
# ──────────────────────────────────────────────

class TestAiPayload:
    def test_payload_contains_no_pii(self):
        """AIに送るペイロードに個人特定情報が含まれない"""
        summary = {
            "year": 2026, "month": 5,
            "total_expense_yen": 300000, "total_income_yen": 500000,
            "categories": [{"name": "食費", "amount_yen": 80000, "subcategories": []}],
            "transaction_count": 50,
        }
        payload = build_ai_analysis_payload(summary, [], [], {"年齢帯": "40代"})
        text = str(payload)
        # 明細レベルの情報（店名・取引内容）は含まれない
        assert "institution" not in text
        assert "description" not in text
        assert payload["支出合計_円"] == 300000
