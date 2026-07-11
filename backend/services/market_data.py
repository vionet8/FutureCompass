"""
保有銘柄の最新株価・配当履歴の取得（Yahoo Finance chart API、無料・無認証）。

ベンチマーク取得(benchmark.py)と同じchart APIを使うが、events=divパラメータを
付けることで配当履歴（権利落ち日と1株あたり配当額）も同時に取得できる。
外部通信失敗は例外を投げず銘柄ごとのエラーとして返す（一部銘柄の失敗で
全体を壊さない）。
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from ..models.portfolio import SecurityQuote, DividendEvent

logger = logging.getLogger("market_data")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d&events=div"
QUOTE_MAX_AGE_HOURS = 24


def to_yahoo_symbol(symbol_code: str) -> str:
    """
    マネフォの銘柄コードをYahoo Finance形式に変換する。
    日本株の証券コード（4桁数字。1489のようなETFも含む）は".T"を付け、
    米国ティッカー（アルファベット）はそのまま使う。
    """
    # 東証の証券コード: 4桁数字（8306等）または新形式の数字3桁+英字1文字（135A等）
    if re.fullmatch(r"\d{4}|\d{3}[A-Z]", symbol_code):
        return f"{symbol_code}.T"
    return symbol_code


def fetch_quote_and_dividends(yahoo_symbol: str) -> dict:
    """
    Yahoo Financeから最新株価と過去1年の配当履歴を取得する。失敗時は例外を投げる。
    戻り値: {"price": float, "currency": str, "dividends": [(date, amount), ...]}
    """
    resp = httpx.get(
        YAHOO_CHART_URL.format(symbol=yahoo_symbol), timeout=10.0,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    resp.raise_for_status()
    data = resp.json()
    result = data.get("chart", {}).get("result")
    if not result:
        raise ValueError(f"Yahoo Financeから有効なデータを取得できませんでした: {yahoo_symbol}")

    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    currency = meta.get("currency", "JPY")
    if price is None:
        raise ValueError(f"株価が取得できませんでした: {yahoo_symbol}")

    dividends = []
    events = result[0].get("events", {}).get("dividends", {})
    for _, div in events.items():
        ts = div.get("date")
        amount = div.get("amount")
        if ts is None or amount is None:
            continue
        dividends.append((datetime.fromtimestamp(ts, tz=timezone.utc).date(), float(amount)))

    return {"price": float(price), "currency": currency, "dividends": dividends}


def refresh_market_data(db: Session, symbol_codes: list[str]) -> dict:
    """
    銘柄コードのリストについて株価・配当キャッシュを更新する。
    24時間以内に取得済みの銘柄はスキップ。銘柄単位の失敗はエラーリストに積み、
    処理は継続する（グレースフルデグラデーション）。
    戻り値: {"refreshed": int, "skipped_fresh": int, "errors": [{"symbol", "error"}]}
    """
    refreshed = 0
    skipped = 0
    errors = []

    for code in symbol_codes:
        ysym = to_yahoo_symbol(code)
        quote = db.query(SecurityQuote).filter(SecurityQuote.yahoo_symbol == ysym).first()
        if quote and (datetime.utcnow() - quote.fetched_at) < timedelta(hours=QUOTE_MAX_AGE_HOURS):
            skipped += 1
            continue

        try:
            data = fetch_quote_and_dividends(ysym)
        except Exception as e:
            logger.warning("株価取得失敗（%s）: %s", ysym, e)
            errors.append({"symbol": code, "error": str(e)})
            continue

        if quote:
            quote.latest_price = data["price"]
            quote.currency = data["currency"]
            quote.fetched_at = datetime.utcnow()
        else:
            db.add(SecurityQuote(
                yahoo_symbol=ysym, latest_price=data["price"],
                currency=data["currency"], fetched_at=datetime.utcnow(),
            ))

        existing_dates = {
            d for (d,) in db.query(DividendEvent.ex_date)
            .filter(DividendEvent.yahoo_symbol == ysym).all()
        }
        for ex_date, amount in data["dividends"]:
            if ex_date in existing_dates:
                continue
            db.add(DividendEvent(yahoo_symbol=ysym, ex_date=ex_date, amount=amount))

        refreshed += 1

    db.commit()
    return {"refreshed": refreshed, "skipped_fresh": skipped, "errors": errors}
