import pytest
from backend.services.csv_parser import parse_moneyforward, detect_and_parse, CSVParseError

HEADER = "日付,合計（円）,預金・現金（円）,株式(現物)（円）,株式(信用)（円）,投資信託（円）,年金（円）,ポイント（円）"


def mf_asset_csv(rows: list[str]) -> bytes:
    text = "\n".join([HEADER] + rows)
    return text.encode("shift_jis")


class TestParseMoneyforward:
    def test_real_sample_latest_row(self):
        content = mf_asset_csv([
            "2026/07/07,43413897,7294013,14986670,0,17940800,3062255,130159",
            "2026/07/06,43254045,7287141,14861873,0,17912617,3062255,130159",
        ])
        r = parse_moneyforward(content)
        assert r["source"] == "moneyforward"
        assert r["as_of_date"] == "2026/07/07"
        # cash = 7294013 + points 130159 = 7424172 -> //10000
        assert r["cash_assets_man"] == (7294013 + 130159) // 10000
        # investment = stocks(14986670+0) + funds 17940800 + pension 3062255
        assert r["investment_assets_man"] == (14986670 + 0 + 17940800 + 3062255) // 10000
        # cash/investmentは個別に切り捨てるため合計と±1万円のずれが出ることがある
        assert abs(r["total_assets_man"] - (r["cash_assets_man"] + r["investment_assets_man"])) <= 1

    def test_picks_latest_even_if_out_of_order(self):
        """ファイル内の行順に依存せず日付最新行を選ぶ"""
        content = mf_asset_csv([
            "2026/05/31,100000,50000,20000,0,30000,0,0",
            "2026/07/07,200000,80000,40000,0,80000,0,0",  # 最新だが2行目
        ])
        r = parse_moneyforward(content)
        assert r["as_of_date"] == "2026/07/07"

    def test_missing_date_column_raises(self):
        content = "a,b,c\n1,2,3".encode("utf-8")
        with pytest.raises(CSVParseError):
            parse_moneyforward(content)

    def test_utf8_sig_also_works(self):
        content = ("\n".join([HEADER, "2026/07/07,100000,50000,20000,0,30000,0,0"])).encode("utf-8-sig")
        r = parse_moneyforward(content)
        assert r["source"] == "moneyforward"


class TestDetectAndParse:
    def test_generic_filename_detected_by_content(self):
        """マネフォのダウンロードは 707aad9d- のようなランダムファイル名になりうる。
        ファイル名にヒントがなくても内容で判定できる（回帰テスト）。"""
        content = mf_asset_csv(["2026/07/07,100000,50000,20000,0,30000,0,0"])
        r = detect_and_parse("707aad9d-download.csv", content)
        assert r["source"] == "moneyforward"

    def test_unrelated_csv_rejected(self):
        with pytest.raises(CSVParseError):
            detect_and_parse("data.csv", b"name,age\nAlice,30\n")
