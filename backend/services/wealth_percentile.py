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
BUCKET_LABELS = ["資産なし", "〜100万円", "100〜500万円", "500〜1000万円", "1000〜2000万円", "2000〜3000万円", "3000万円〜"]

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


@dataclass
class PercentileResult:
    age_band: str
    percentile_from_bottom: float  # 資産が少ない方から数えて何%目か
    top_percent: float             # 資産が多い方から数えて上位何%か
    bucket_index: int              # ユーザーが属する分布区分のインデックス
    distribution: list[dict]       # チャート描画用の区分別データ


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

    cum = _cumulative_pct(band)  # 6要素: boundary=0,100,500,1000,2000,3000における累積%
    pct = _normalized_pct(band)  # 7要素: 各区分の世帯割合%
    boundaries = BUCKET_BOUNDARIES_MAN

    if total_assets_man <= 0:
        percentile_from_bottom = cum[0] / 2  # 非保有層の中央値的な位置とみなす
        bucket_index = 0
    elif total_assets_man >= boundaries[-1]:
        top_bucket_start_cum = cum[-1]
        frac = min(1.0, (total_assets_man - boundaries[-1]) / (TOP_BUCKET_CAP_MAN - boundaries[-1]))
        percentile_from_bottom = top_bucket_start_cum + frac * (100 - top_bucket_start_cum)
        bucket_index = len(pct) - 1
    else:
        # boundaries[i]は常にcum[i]（その額以下の世帯累積割合）に対応するため、
        # 区間(boundaries[i], boundaries[i+1])内はcum[i]〜cum[i+1]で線形補間する
        percentile_from_bottom = cum[-1]
        bucket_index = len(pct) - 2
        for i in range(len(boundaries) - 1):
            lo, hi = boundaries[i], boundaries[i + 1]
            if lo <= total_assets_man < hi:
                lo_cum, hi_cum = cum[i], cum[i + 1]
                frac = (total_assets_man - lo) / (hi - lo)
                percentile_from_bottom = lo_cum + frac * (hi_cum - lo_cum)
                bucket_index = i + 1
                break

    percentile_from_bottom = max(0.0, min(100.0, percentile_from_bottom))
    top_percent = round(max(0.1, 100 - percentile_from_bottom), 1)

    distribution = [
        {"label": BUCKET_LABELS[i], "pct": round(pct[i], 1), "is_user_bucket": i == bucket_index}
        for i in range(len(pct))
    ]

    return PercentileResult(
        age_band=band,
        percentile_from_bottom=round(percentile_from_bottom, 1),
        top_percent=top_percent,
        bucket_index=bucket_index,
        distribution=distribution,
    )
