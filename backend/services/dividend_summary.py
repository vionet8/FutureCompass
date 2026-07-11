"""
保有株の配当サマリー（銘柄別の年間配当・利回り・権利落ち月と、月別配当収入カレンダー）。

- 対象は最新スナップショットの「株式」カテゴリで保有株数(quantity)が取れている銘柄。
  投資信託は分配金情報の信頼できる無料ソースが無い（多くは再投資型で分配なし）ため対象外。
- 年間配当は「過去1年の配当実績の合計」（TTM: trailing twelve months）。将来の
  配当予想ではなく実績ベースなので、増配・減配があった銘柄は実際と乖離しうる。
- 月別カレンダーは権利落ち日ベース。日本株の実際の入金は権利確定から
  2〜3ヶ月後が普通なので、入金月とはずれる点に注意。
- 米国株の配当はUSD建てのため、ベンチマーク用に取得済みのドル円レートで円換算する。
"""

from collections import defaultdict
from datetime import date, datetime

from sqlalchemy.orm import Session

from ..models.portfolio import Holding, DividendEvent, SecurityQuote
from .market_data import to_yahoo_symbol, refresh_market_data
from .benchmark import get_price_on_or_before, ensure_cache_fresh, FX_SYMBOL
from .portfolio_analysis import get_latest_snapshot, _excluded_security_keys


def _stock_holdings(db: Session, user_id: str) -> list[dict]:
    """
    最新スナップショットの株式カテゴリ（計算対象外を除く）。
    同一銘柄を複数口座で保有している場合は数量・評価額を合算する。
    ORMオブジェクトを直接書き換えるとcommit時にDBへ意図せず書き戻されるため、
    集計はプレーンなdictで行う。
    """
    snapshot = get_latest_snapshot(db, user_id)
    if snapshot is None:
        return []
    excluded = _excluded_security_keys(db, user_id)
    rows = (
        db.query(Holding)
        .filter(Holding.snapshot_id == snapshot.id, Holding.category == "株式")
        .all()
    )
    merged: dict[str, dict] = {}
    for h in rows:
        if h.security_key in excluded or not h.symbol_code:
            continue
        if h.security_key in merged:
            prev = merged[h.security_key]
            if h.quantity is not None:
                prev["quantity"] = (prev["quantity"] or 0) + h.quantity
            prev["market_value_yen"] += h.market_value_yen
        else:
            merged[h.security_key] = {
                "name": h.name,
                "symbol_code": h.symbol_code,
                "quantity": h.quantity,
                "market_value_yen": h.market_value_yen,
            }
    return list(merged.values())


def refresh_dividend_data(db: Session, user_id: str) -> dict:
    """保有株全銘柄の株価・配当キャッシュと、円換算用のドル円レートを更新する"""
    holdings = _stock_holdings(db, user_id)
    result = refresh_market_data(db, [h["symbol_code"] for h in holdings])
    fx_error = ensure_cache_fresh(db, FX_SYMBOL)
    if fx_error:
        result["errors"].append({"symbol": FX_SYMBOL, "error": fx_error})
    return result


def compute_dividend_summary(db: Session, user_id: str) -> dict | None:
    """
    銘柄別の配当情報と月別配当収入を計算する。
    戻り値: {
      "holdings": [{name, symbol_code, quantity, latest_price, price_currency,
                    annual_dividend_per_share, annual_income_yen, yield_pct, ex_months}, ...],
      "monthly": [{month: 1..12, total_yen}],
      "total_annual_income_yen": int,
      "missing_quantity_count": int,  # 保有株数が無く計算できなかった銘柄数
    }
    保有株データが無い場合はNone。
    """
    holdings = _stock_holdings(db, user_id)
    if not holdings:
        return None

    fx_rate = get_price_on_or_before(db, FX_SYMBOL, date.today()) or 0.0

    result_holdings = []
    monthly_totals: dict[int, float] = defaultdict(float)
    total_annual = 0.0
    missing_quantity = 0

    for h in holdings:
        ysym = to_yahoo_symbol(h["symbol_code"])
        quote = db.query(SecurityQuote).filter(SecurityQuote.yahoo_symbol == ysym).first()
        divs = (
            db.query(DividendEvent)
            .filter(DividendEvent.yahoo_symbol == ysym)
            .order_by(DividendEvent.ex_date)
            .all()
        )

        # 過去1年分に限定（キャッシュには古い履歴も溜まりうるため）
        cutoff = date.today().replace(year=date.today().year - 1)
        recent_divs = [d for d in divs if d.ex_date >= cutoff]

        annual_dps = sum(d.amount for d in recent_divs)  # 1株あたり年間配当（建値通貨）
        ex_months = sorted({d.ex_date.month for d in recent_divs})

        currency = quote.currency if quote else None
        latest_price = quote.latest_price if quote else None
        yield_pct = None
        if latest_price and annual_dps:
            yield_pct = round(annual_dps / latest_price * 100, 2)

        # 円換算係数（USD建て配当はドル円で換算。レート未取得ならUSD銘柄は収入計算不可）
        to_yen = 1.0 if currency != "USD" else (fx_rate if fx_rate > 0 else None)

        annual_income_yen = None
        if h["quantity"] is None:
            missing_quantity += 1
        elif annual_dps and to_yen is not None:
            annual_income_yen = h["quantity"] * annual_dps * to_yen
            total_annual += annual_income_yen
            for d in recent_divs:
                monthly_totals[d.ex_date.month] += h["quantity"] * d.amount * to_yen

        result_holdings.append({
            "name": h["name"],
            "symbol_code": h["symbol_code"],
            "quantity": h["quantity"],
            "latest_price": latest_price,
            "price_currency": currency,
            "annual_dividend_per_share": round(annual_dps, 4) if annual_dps else 0,
            "annual_income_yen": round(annual_income_yen) if annual_income_yen else None,
            "yield_pct": yield_pct,
            "ex_months": ex_months,
        })

    # 年間配当収入が多い順（Noneは末尾）
    result_holdings.sort(key=lambda x: -(x["annual_income_yen"] or 0))

    return {
        "holdings": result_holdings,
        "monthly": [{"month": m, "total_yen": round(monthly_totals.get(m, 0))} for m in range(1, 13)],
        "total_annual_income_yen": round(total_annual),
        "missing_quantity_count": missing_quantity,
    }
