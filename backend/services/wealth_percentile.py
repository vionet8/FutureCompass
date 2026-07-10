"""
年代別の金融資産保有額分布と比較し、ユーザーの資産が同世代の上位何%かを推定する。

出典: 金融経済教育推進機構(J-FLEC)/金融広報中央委員会
    「家計の金融行動に関する世論調査」（二人以上世帯調査）年代別データ
分布区分（万円）: 非保有 / 〜100 / 100〜500 / 500〜1000 / 1000〜2000 / 2000〜3000 / 3000〜
※ 公表されている区分別世帯割合(%)からの近似値。区分内は線形補間、
  最上位区分(3000万円〜)は1億円で分布がほぼ収束すると仮定した近似。
  「不動産を除く金融資産」の統計であり、本アプリのtotal_assets_yen（現金+投資）と対応する。
"""

from dataclasses import dataclass
from typing import Optional

# 区分の上限（万円）。インデックスiは「boundaries[i-1]〜boundaries[i]」区分に対応（0番目は非保有=0円）
BUCKET_BOUNDARIES_MAN = [0, 100, 500, 1000, 2000, 3000]

# 年代別・区分別の世帯割合(%)。合計が100%にならない分は「無回答」のため正規化して使う
AGE_BAND_RAW_PCT: dict[str, list[float]] = {
    "20代": [21.6, 16.4, 32.7, 8.8, 5.9, 4.1, 4.1],
    "30代": [17.6, 12.7, 25.6, 15.7, 13.3, 4.6, 7.9],
    "40代": [18.8, 10.0, 18.3, 13.4, 16.2, 8.2, 13.1],
    "50代": [18.2, 6.5, 16.2, 14.4, 15.4, 8.1, 18.8],
    "60代": [12.8, 4.7, 11.5, 12.5, 16.9, 12.4, 27.2],
    "70代": [10.9, 4.5, 15.6, 13.1, 17.8, 12.3, 25.2],
}

TOP_BUCKET_CAP_MAN = 10000  # 3000万円超の分布を近似するための上限（1億円でほぼ全員を上回るとみなす）

# ピラミッド表示用の閾値（万円）。「◯万円以上の世帯は何%か」を計算する基準点。
# 3000万円超は公表データが無いため、TOP_BUCKET_CAP_MANまでの線形補間で近似する。
PYRAMID_THRESHOLDS_MAN = [0, 100, 500, 1000, 2000, 3000, 4000, 5000, 7000, 10000]


def age_band(age: int) -> Optional[str]:
    """年齢から年代ラベルを返す。20歳未満はNone、70歳以上は"70代"に丸める"""
    if age < 20:
        return None
    decade = min(age, 79) // 10 * 10
    decade = max(20, decade)
    label = f"{decade}代"
    return label if label in AGE_BAND_RAW_PCT else "70代"


def _normalized_pct(band: str) -> list[float]:
    raw = AGE_BAND_RAW_PCT[band]
    total = sum(raw)
    return [p / total * 100 for p in raw]


def _cumulative_pct(band: str) -> list[float]:
    """区分ごとの世帯割合から累積分布(各boundaryにおける「その額以下の世帯割合」)を返す"""
    pct = _normalized_pct(band)
    cum = []
    running = 0.0
    for p in pct[:-1]:  # 最終区分(3000万円〜)は別扱いなので除く
        running += p
        cum.append(running)
    return cum  # len == len(BUCKET_BOUNDARIES_MAN)


def _percentile_from_bottom_at(band: str, value_man: float) -> float:
    """
    年代bandにおいて、資産額value_man(万円)が「資産が少ない方から数えて何%目」に
    位置するかを、公表区分の累積%を用いた線形補間で推定する（0〜100の範囲）。
    """
    cum = _cumulative_pct(band)
    boundaries = BUCKET_BOUNDARIES_MAN

    if value_man <= 0:
        result = cum[0] / 2  # 非保有層の中央値的な位置とみなす
    elif value_man >= boundaries[-1]:
        top_bucket_start_cum = cum[-1]
        frac = min(1.0, (value_man - boundaries[-1]) / (TOP_BUCKET_CAP_MAN - boundaries[-1]))
        result = top_bucket_start_cum + frac * (100 - top_bucket_start_cum)
    else:
        # boundaries[i]は常にcum[i]（その額以下の世帯累積割合）に対応するため、
        # 区間(boundaries[i], boundaries[i+1])内はcum[i]〜cum[i+1]で線形補間する
        result = cum[-1]
        for i in range(len(boundaries) - 1):
            lo, hi = boundaries[i], boundaries[i + 1]
            if lo <= value_man < hi:
                lo_cum, hi_cum = cum[i], cum[i + 1]
                frac = (value_man - lo) / (hi - lo)
                result = lo_cum + frac * (hi_cum - lo_cum)
                break

    return max(0.0, min(100.0, result))


@dataclass
class PercentileResult:
    age_band: str
    percentile_from_bottom: float  # 資産が少ない方から数えて何%目か
    top_percent: float             # 資産が多い方から数えて上位何%か
    pyramid: list[dict]            # ピラミッド表示用: 各閾値以上の世帯割合（累積、下ほど大きい）
    user_threshold_man: int        # ユーザーが到達している最大の閾値（ピラミッドのハイライト用）


def compute_wealth_percentile(age: int, total_assets_man: float) -> Optional[PercentileResult]:
    """年齢と総資産額(万円)から、同世代内でのパーセンタイル位置を推定する"""
    band = age_band(age)
    if band is None:
        return None
    return compute_wealth_percentile_for_band(band, total_assets_man)


def compute_wealth_percentile_for_band(band: str, total_assets_man: float) -> Optional[PercentileResult]:
    """
    年代ラベル（例:"40代"）を直接指定してパーセンタイル位置を推定する。
    年齢が世代境界に近い場合など、比較対象の年代をユーザーが選び直せるようにするための入口。
    """
    if band not in AGE_BAND_RAW_PCT:
        return None

    percentile_from_bottom = round(_percentile_from_bottom_at(band, total_assets_man), 1)
    top_percent = round(max(0.1, 100 - percentile_from_bottom), 1)

    user_threshold_man = 0
    pyramid = []
    for threshold in PYRAMID_THRESHOLDS_MAN:
        # 「threshold万円以上の世帯は何%か」= 100 - (threshold未満の累積%)
        # threshold自体をvalue_manとして評価すると「threshold以下」の累積%になるため、
        # 「以上」を得るには境界のわずか下の値を評価してから100から引く
        pct_below = _percentile_from_bottom_at(band, threshold - 0.01) if threshold > 0 else 0.0
        pct_at_or_above = round(max(0.1, 100 - pct_below), 1)
        pyramid.append({
            "threshold_man": threshold,
            "label": f"{threshold:,}万円以上" if threshold > 0 else "すべての世帯",
            "pct_at_or_above": pct_at_or_above,
        })
        if total_assets_man >= threshold:
            user_threshold_man = threshold

    for row in pyramid:
        row["is_user_level"] = row["threshold_man"] == user_threshold_man

    return PercentileResult(
        age_band=band,
        percentile_from_bottom=percentile_from_bottom,
        top_percent=top_percent,
        pyramid=pyramid,
        user_threshold_man=user_threshold_man,
    )
