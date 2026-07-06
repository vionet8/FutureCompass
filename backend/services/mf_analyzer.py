"""
マネーフォワードCSV解析・家計分析サービス

CSVパース → カテゴリ集計 → 前年同月比較 → AI提案データ生成
"""

import csv
import io
from datetime import date, datetime
from collections import defaultdict
from typing import Optional


# マネーフォワードの大項目 → 内部カテゴリへのマッピング（表示用）
MF_CATEGORY_MAP = {
    "食費": "食費",
    "日用品": "日用品",
    "住宅": "住宅・家賃",
    "水道・光熱費": "水道・光熱費",
    "通信費": "通信費",
    "交通費": "交通費",
    "医療・健康": "医療・健康",
    "衣服・美容": "衣服・美容",
    "趣味・娯楽": "趣味・娯楽",
    "教養・教育": "教養・教育",
    "特別な支出": "特別な支出",
    "税・社会保障": "税・社会保障",
    "保険": "保険",
    "現金・カード": "現金・カード",
    "収入": "（収入）",
    "未分類": "未分類",
}

# 分析から除外するカテゴリ（収入・振替）
EXCLUDE_FROM_EXPENSE = {"（収入）", "（収入）"}
INCOME_CATEGORIES = {"収入"}


def parse_mf_csv(content: bytes) -> list[dict]:
    """
    マネーフォワードの入出金明細CSVをパースして取引リストを返す。
    エンコード: UTF-8 → CP932 の順で試行。
    戻り値: [{date, description, amount_yen, institution, category_major,
               category_minor, memo, is_transfer, is_target, mf_id}, ...]
    """
    text = None
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            text = content.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise ValueError("CSVのエンコードを判別できませんでした（UTF-8またはShift-JIS）")

    # タブ区切り or カンマ区切りを自動判定
    sample = text[:500]
    delimiter = "\t" if text.count("\t") > text.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    transactions = []

    for row in reader:
        try:
            # 計算対象・振替フラグ
            is_target = str(row.get("計算対象", "1")).strip() == "1"
            is_transfer = str(row.get("振替", "0")).strip() == "1"

            # 日付パース
            date_str = row.get("日付", "").strip()
            if not date_str:
                continue
            try:
                tx_date = datetime.strptime(date_str, "%Y/%m/%d").date()
            except ValueError:
                tx_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            # 金額（カンマ除去、空白除去）
            amount_str = row.get("金額（円）", "0").strip().replace(",", "").replace(" ", "")
            amount_yen = int(amount_str) if amount_str and amount_str != "" else 0

            mf_id_raw = row.get("ID", "").strip()
            # Excelの数式崩れ（#NAME?等）は空文字として扱う
            mf_id = mf_id_raw if (mf_id_raw and not mf_id_raw.startswith("#")) else None

            transactions.append({
                "date": tx_date,
                "description": row.get("内容", "").strip(),
                "amount_yen": amount_yen,
                "institution": row.get("保有金融機関", "").strip(),
                "category_major": row.get("大項目", "未分類").strip(),
                "category_minor": row.get("中項目", "").strip(),
                "memo": row.get("メモ", "").strip(),
                "is_transfer": is_transfer,
                "is_target": is_target,
                "mf_id": mf_id,
            })
        except (ValueError, KeyError):
            continue

    return transactions


def _expense_transactions(transactions: list[dict]) -> list[dict]:
    """支出として集計すべきトランザクションのみ返す"""
    return [
        t for t in transactions
        if t["is_target"]
        and not t["is_transfer"]
        and t["category_major"] not in INCOME_CATEGORIES
        and t["amount_yen"] < 0  # 支出は負
    ]


def _income_transactions(transactions: list[dict]) -> list[dict]:
    return [
        t for t in transactions
        if t["is_target"]
        and not t["is_transfer"]
        and t["amount_yen"] > 0
        and t["category_major"] in INCOME_CATEGORIES
    ]


def monthly_summary(transactions: list[dict], year: int, month: int) -> dict:
    """
    指定月の支出をカテゴリ別に集計する。
    戻り値: {
      "total_expense_yen": int,
      "total_income_yen": int,
      "categories": [{name, amount_yen, subcategories: [{name, amount_yen}]}],
      "transaction_count": int,
    }
    """
    month_txns = [
        t for t in transactions
        if t["date"].year == year and t["date"].month == month
    ]

    expenses = _expense_transactions(month_txns)
    incomes = _income_transactions(month_txns)

    # カテゴリ → サブカテゴリ別集計
    cat_sub: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in expenses:
        major = t["category_major"] or "未分類"
        minor = t["category_minor"] or "（その他）"
        cat_sub[major][minor] += abs(t["amount_yen"])

    categories = []
    for major, subs in sorted(cat_sub.items(), key=lambda x: -sum(x[1].values())):
        total = sum(subs.values())
        categories.append({
            "name": major,
            "amount_yen": total,
            "subcategories": [
                {"name": sub, "amount_yen": amt}
                for sub, amt in sorted(subs.items(), key=lambda x: -x[1])
            ],
        })

    return {
        "year": year,
        "month": month,
        "total_expense_yen": sum(abs(t["amount_yen"]) for t in expenses),
        "total_income_yen": sum(t["amount_yen"] for t in incomes),
        "categories": categories,
        "transaction_count": len(expenses),
    }


def compare_yoy(
    current: dict,
    prev_year: dict,
    threshold: float = 0.10,
) -> list[dict]:
    """
    当月 vs 前年同月のカテゴリ別比較。
    threshold=0.10 → ±10%以上で flagged=True
    """
    cur_map = {c["name"]: c["amount_yen"] for c in current["categories"]}
    prev_map = {c["name"]: c["amount_yen"] for c in prev_year["categories"]}

    all_cats = sorted(set(cur_map) | set(prev_map))
    results = []
    for cat in all_cats:
        cur_amt = cur_map.get(cat, 0)
        prev_amt = prev_map.get(cat, 0)
        if prev_amt == 0:
            change_pct = None
        else:
            change_pct = (cur_amt - prev_amt) / prev_amt
        flagged = change_pct is not None and abs(change_pct) >= threshold
        results.append({
            "category": cat,
            "current_yen": cur_amt,
            "prev_year_yen": prev_amt,
            "change_pct": round(change_pct * 100, 1) if change_pct is not None else None,
            "flagged": flagged,
            "direction": "up" if (change_pct or 0) > 0 else "down",
        })

    # フラグあり → 増加 → 減少 の順でソート
    results.sort(key=lambda x: (not x["flagged"], -abs(x["change_pct"] or 0)))
    return results


def detect_trend(monthly_summaries: list[dict]) -> list[dict]:
    """
    直近Nヶ月のカテゴリ別トレンドを検出。
    3ヶ月以上連続増加しているカテゴリを「じわじわ増加」として返す。
    """
    if len(monthly_summaries) < 3:
        return []

    # カテゴリ × 月の行列
    cat_series: dict[str, list[int]] = defaultdict(list)
    for summary in monthly_summaries:
        seen_cats = {c["name"] for c in summary["categories"]}
        for cat_data in summary["categories"]:
            cat_series[cat_data["name"]].append(cat_data["amount_yen"])
        # 登場しない月は0で補完
        for cat in list(cat_series.keys()):
            if cat not in seen_cats:
                cat_series[cat].append(0)

    trends = []
    for cat, series in cat_series.items():
        if len(series) < 3:
            continue
        recent = series[-3:]
        # 3ヶ月連続増加チェック
        if recent[0] > 0 and recent[1] > recent[0] and recent[2] > recent[1]:
            total_increase_pct = (recent[2] - recent[0]) / recent[0] * 100
            if total_increase_pct >= 10:
                trends.append({
                    "category": cat,
                    "series": series,
                    "increase_pct_3m": round(total_increase_pct, 1),
                })

    trends.sort(key=lambda x: -x["increase_pct_3m"])
    return trends


def build_ai_analysis_payload(
    current_summary: dict,
    yoy_comparison: list[dict],
    trends: list[dict],
    profile_hint: dict,
) -> dict:
    """
    AI分析に送る匿名化済みサマリーを構築する。
    個人を特定できる情報は含めない。
    """
    total_exp = current_summary["total_expense_yen"]
    total_inc = current_summary["total_income_yen"]

    flagged = [x for x in yoy_comparison if x["flagged"]]

    payload = {
        "分析期間": f"{current_summary['year']}年{current_summary['month']}月",
        "支出合計_円": total_exp,
        "収入合計_円": total_inc,
        "収支差_円": total_inc - total_exp,
        "カテゴリ別支出": {
            c["name"]: c["amount_yen"]
            for c in current_summary["categories"]
        },
        "前年同月比_閾値10%以上の変化": [
            {
                "カテゴリ": x["category"],
                "今月_円": x["current_yen"],
                "前年同月_円": x["prev_year_yen"],
                "変化率_pct": x["change_pct"],
                "増減": "増加" if x["direction"] == "up" else "減少",
            }
            for x in flagged
        ],
        "じわじわ増加トレンド": [
            {"カテゴリ": t["category"], "3ヶ月増加率_pct": t["increase_pct_3m"]}
            for t in trends
        ],
        "世帯プロファイル": profile_hint,  # 年齢帯・子供人数など匿名情報のみ
    }
    return payload
