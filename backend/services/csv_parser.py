import pandas as pd
import io
from typing import Optional


class CSVParseError(Exception):
    pass


def _read_csv_any_encoding(content: bytes, **kwargs) -> pd.DataFrame:
    """Shift-JIS→UTF-8-sig→UTF-8の順でCSVを読み込む"""
    last_error = None
    for enc in ("shift_jis", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(io.BytesIO(content), encoding=enc, **kwargs)
        except (UnicodeDecodeError, LookupError) as e:
            last_error = e
            continue
    raise CSVParseError(f"CSVのエンコードを判別できませんでした: {last_error}")


def _split_cash_investment(row) -> tuple[float, float, float]:
    """
    資産推移CSVの1行から (現金相当, 投資資産, 総資産) の円額を計算する。
    現金相当 = 預金・現金 + ポイント（実質現金）
    投資資産 = 株式(現物+信用) + 投資信託 + 年金（iDeCo等、長期運用資産として扱う）
    """
    def yen(col: str) -> float:
        return float(row.get(col, 0) or 0)

    cash = yen("預金・現金（円）")
    stocks = yen("株式(現物)（円）") + yen("株式(信用)（円）")
    funds = yen("投資信託（円）")
    pension = yen("年金（円）")
    points = yen("ポイント（円）")

    investment = stocks + funds + pension
    total = cash + investment + points
    return cash + points, investment, total


def parse_moneyforward(content: bytes) -> dict:
    """
    マネーフォワードCSV（資産推移）。最新1行のみをプロフィール反映用に返す。
    実際の出力形式（Shift-JIS、日付降順）:
      日付, 合計（円）, 預金・現金（円）, 株式(現物)（円）, 株式(信用)（円）,
      投資信託（円）, 年金（円）, ポイント（円）
    """
    df = _read_csv_any_encoding(content)
    if "日付" not in df.columns:
        raise CSVParseError("マネーフォワードの資産推移CSVではないようです（「日付」列が見つかりません）")

    # 念のため日付降順にソートして最新行を取る（ファイル順序に依存しない）
    df = df.copy()
    df["_日付dt"] = pd.to_datetime(df["日付"], format="%Y/%m/%d", errors="coerce")
    df = df.sort_values("_日付dt", ascending=False)
    latest = df.iloc[0]

    cash, investment, total = _split_cash_investment(latest)

    return {
        "total_assets_man": int(total / 10000),
        "cash_assets_man": int(cash / 10000),
        "investment_assets_man": int(investment / 10000),
        "as_of_date": latest["日付"],
        "source": "moneyforward",
    }


def parse_moneyforward_asset_history_full(content: bytes) -> list[dict]:
    """
    マネーフォワードCSV（資産推移）の全履歴を返す（実績投資成績トラッキング用）。
    最新行のみのparse_moneyforwardと異なり、日付昇順で全行を返す。
    """
    df = _read_csv_any_encoding(content)
    if "日付" not in df.columns:
        raise CSVParseError("マネーフォワードの資産推移CSVではないようです（「日付」列が見つかりません）")

    df = df.copy()
    df["_日付dt"] = pd.to_datetime(df["日付"], format="%Y/%m/%d", errors="coerce")
    df = df.dropna(subset=["_日付dt"]).sort_values("_日付dt")

    rows = []
    for _, row in df.iterrows():
        cash, investment, total = _split_cash_investment(row)
        rows.append({
            "date": row["_日付dt"].date(),
            "total_assets_yen": int(total),
            "cash_assets_yen": int(cash),
            "investment_assets_yen": int(investment),
        })
    return rows


def parse_rakuten(content: bytes) -> dict:
    """
    楽天証券CSV（評価額一覧）。
    ※ 実データ未検証。列名・skiprowsは推測値のため、実際のエクスポートと
      形式が違う場合は下の except で明確にエラーを返す（誤った数値を返さない）。
    """
    try:
        df = _read_csv_any_encoding(content, skiprows=1)
        if "評価額" not in df.columns:
            raise CSVParseError(
                "楽天証券CSVの想定形式と一致しませんでした（未検証フォーマットのため要調整）"
            )
        total = df["評価額"].astype(str).str.replace(",", "", regex=False).astype(float).sum()
        return {
            "investment_assets_man": int(total / 10000),
            "source": "rakuten",
        }
    except CSVParseError:
        raise
    except Exception as e:
        raise CSVParseError(f"楽天証券CSV解析エラー: {e}")


def parse_sbi(content: bytes) -> dict:
    """
    SBI証券CSV（保有証券一覧）。
    ※ 実データ未検証。列名・skiprowsは推測値のため、実際のエクスポートと
      形式が違う場合は下の except で明確にエラーを返す（誤った数値を返さない）。
    """
    try:
        df = _read_csv_any_encoding(content, skiprows=8)
        if "評価額(円)" not in df.columns:
            raise CSVParseError(
                "SBI証券CSVの想定形式と一致しませんでした（未検証フォーマットのため要調整）"
            )
        total = df["評価額(円)"].astype(str).str.replace(",", "", regex=False).astype(float).sum()
        return {
            "investment_assets_man": int(total / 10000),
            "source": "sbi",
        }
    except CSVParseError:
        raise
    except Exception as e:
        raise CSVParseError(f"SBI証券CSV解析エラー: {e}")


def _sniff_moneyforward_assets(content: bytes) -> bool:
    """マネーフォワード資産推移CSVのヘッダーを内容から判定する（ファイル名は当てにならないため）"""
    head = content[:1024]
    for enc in ("shift_jis", "utf-8-sig", "utf-8"):
        try:
            first_line = head.decode(enc, errors="strict").splitlines()[0]
        except (UnicodeDecodeError, IndexError):
            continue
        if "日付" in first_line and "合計" in first_line and "預金" in first_line:
            return True
    return False


def detect_and_parse(filename: str, content: bytes) -> Optional[dict]:
    name_lower = filename.lower()

    # ファイル名でわかる場合はそれを優先
    if "rakuten" in name_lower or "楽天" in filename:
        return parse_rakuten(content)
    if "sbi" in name_lower:
        return parse_sbi(content)
    if "moneyforward" in name_lower or "mf" in name_lower or "資産推移" in filename:
        return parse_moneyforward(content)

    # ファイル名が汎用的（ダウンロード直後のランダム名等）な場合は内容で判定
    if _sniff_moneyforward_assets(content):
        return parse_moneyforward(content)

    raise CSVParseError("対応していないCSV形式です。マネーフォワード、楽天証券、SBI証券のCSVをご利用ください。")
