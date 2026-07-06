import pandas as pd
import io
from typing import Optional


class CSVParseError(Exception):
    pass


def parse_moneyforward(content: bytes) -> dict:
    """マネーフォワードCSV（資産残高推移）"""
    try:
        df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
        # MF形式: 日付, 資産合計, 現金・預金, 株式, 投資信託, ...
        latest = df.iloc[0]
        return {
            "total_assets_man": int(float(latest.get("資産合計", 0)) / 10000),
            "cash_man": int(float(latest.get("現金・預金", 0)) / 10000),
            "stocks_man": int(float(latest.get("株式", 0)) / 10000),
            "funds_man": int(float(latest.get("投資信託", 0)) / 10000),
            "source": "moneyforward",
        }
    except Exception as e:
        raise CSVParseError(f"マネーフォワードCSV解析エラー: {e}")


def parse_rakuten(content: bytes) -> dict:
    """楽天証券CSV（評価額一覧）"""
    try:
        df = pd.read_csv(io.BytesIO(content), encoding="shift-jis", skiprows=1)
        total = df["評価額"].replace(",", "", regex=True).astype(float).sum()
        return {
            "investment_assets_man": int(total / 10000),
            "source": "rakuten",
        }
    except Exception as e:
        raise CSVParseError(f"楽天証券CSV解析エラー: {e}")


def parse_sbi(content: bytes) -> dict:
    """SBI証券CSV（保有証券一覧）"""
    try:
        df = pd.read_csv(io.BytesIO(content), encoding="shift-jis", skiprows=8)
        total = df["評価額(円)"].replace(",", "", regex=True).astype(float).sum()
        return {
            "investment_assets_man": int(total / 10000),
            "source": "sbi",
        }
    except Exception as e:
        raise CSVParseError(f"SBI証券CSV解析エラー: {e}")


def detect_and_parse(filename: str, content: bytes) -> Optional[dict]:
    name_lower = filename.lower()
    if "moneyforward" in name_lower or "mf" in name_lower or "資産推移" in filename:
        return parse_moneyforward(content)
    elif "rakuten" in name_lower or "楽天" in filename:
        return parse_rakuten(content)
    elif "sbi" in name_lower:
        return parse_sbi(content)
    raise CSVParseError("対応していないCSV形式です。マネーフォワード、楽天証券、SBI証券のCSVをご利用ください。")
