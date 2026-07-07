"""
ベンチマーク指数の実績値取得・キャッシュ（Yahoo Finance chart API、無料・無認証）

※ 当初stooq.comを想定していたが、Bot対策のJS検証が導入されており
  サーバーサイドの単純GETでは実データを取得できなくなっていたため、
  Yahoo Financeの非公式chart APIに切り替えた。

外部通信に失敗してもホームページ全体を壊さないよう、
ensure_cache_fresh は例外を投げず「エラー文字列 or None」を返す。
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..models.performance import BenchmarkPrice

logger = logging.getLogger("benchmark")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1d"
DEFAULT_SYMBOL = "VT"  # Vanguard Total World Stock ETF（オルカン相当の全世界株プロキシ）


def _fetch_stooq_csv(symbol: str) -> list[dict]:
    """
    Yahoo Financeのchart APIから日次終値を取得する。失敗時は例外を投げる（呼び出し側でキャッチ）。
    関数名は既存の呼び出し元・テストとの互換のため据え置き。
    """
    resp = httpx.get(
        YAHOO_CHART_URL.format(symbol=symbol), timeout=10.0,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    data = resp.json()
    result = data.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"Yahoo Financeから有効なデータを取得できませんでした: {symbol}")

    timestamps = result[0].get("timestamp", [])
    closes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])

    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).date(),
            "close": float(close),
        })
    if not rows:
        raise ValueError(f"Yahoo Financeから有効なデータを取得できませんでした: {symbol}")
    return rows


def refresh_benchmark_cache(db: Session, symbol: str = DEFAULT_SYMBOL) -> int:
    """stooqから最新データを取得し、未キャッシュの日付だけをDBに追加する。追加件数を返す"""
    rows = _fetch_stooq_csv(symbol)

    existing = {
        d for (d,) in db.query(BenchmarkPrice.price_date)
        .filter(BenchmarkPrice.symbol == symbol).all()
    }
    added = 0
    for r in rows:
        if r["date"] in existing:
            continue
        db.add(BenchmarkPrice(symbol=symbol, price_date=r["date"], close_price=r["close"]))
        added += 1
    db.commit()
    return added


def get_price_on_or_before(db: Session, symbol: str, target_date: date) -> Optional[float]:
    """指定日以前で最も近い終値を返す（土日祝は前営業日）"""
    row = (
        db.query(BenchmarkPrice)
        .filter(BenchmarkPrice.symbol == symbol, BenchmarkPrice.price_date <= target_date)
        .order_by(BenchmarkPrice.price_date.desc())
        .first()
    )
    return row.close_price if row else None


def ensure_cache_fresh(db: Session, symbol: str = DEFAULT_SYMBOL, max_age_hours: int = 24) -> Optional[str]:
    """
    直近フェッチが古い場合のみstooqを叩く。失敗しても例外を外に出さず、
    エラーメッセージ文字列を返す（呼び出し側=performanceルーターがgracefulに扱うため）。
    成功時（または再取得不要な場合）はNoneを返す。
    """
    latest = (
        db.query(BenchmarkPrice)
        .filter(BenchmarkPrice.symbol == symbol)
        .order_by(BenchmarkPrice.fetched_at.desc())
        .first()
    )
    if latest and (datetime.utcnow() - latest.fetched_at) < timedelta(hours=max_age_hours):
        return None
    try:
        refresh_benchmark_cache(db, symbol)
        return None
    except Exception as e:
        logger.warning("ベンチマーク取得失敗（%s）: %s", symbol, e)
        return f"ベンチマークデータ取得に失敗しました: {e}"
