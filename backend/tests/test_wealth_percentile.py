"""年代別資産パーセンタイル推定のテスト"""
from backend.services.wealth_percentile import (
    age_band,
    compute_wealth_percentile,
    compute_wealth_percentile_for_band,
    AGE_BAND_RAW_PCT,
    BUCKET_BOUNDARIES_MAN,
)


class TestAgeBand:
    def test_maps_to_correct_decade(self):
        assert age_band(25) == "20代"
        assert age_band(30) == "30代"
        assert age_band(39) == "30代"
        assert age_band(45) == "40代"

    def test_under_20_returns_none(self):
        assert age_band(19) is None
        assert age_band(0) is None

    def test_70_and_above_capped_at_70s(self):
        assert age_band(70) == "70代"
        assert age_band(85) == "70代"
        assert age_band(120) == "70代"


class TestComputeWealthPercentile:
    def test_unknown_age_returns_none(self):
        assert compute_wealth_percentile(15, 1000) is None

    def test_zero_assets_is_near_bottom(self):
        r = compute_wealth_percentile(45, 0)
        assert r is not None
        assert r.bucket_index == 0
        assert r.percentile_from_bottom < 20  # 非保有層の中間程度

    def test_percentile_monotonically_increases_with_assets(self):
        """同じ年代なら資産額が多いほどpercentile_from_bottomは大きくなる（単調増加）"""
        amounts = [0, 50, 300, 700, 1500, 2500, 5000, 20000]
        results = [compute_wealth_percentile(45, a) for a in amounts]
        percentiles = [r.percentile_from_bottom for r in results]
        assert percentiles == sorted(percentiles)

    def test_top_percent_is_inverse_of_percentile_from_bottom(self):
        r = compute_wealth_percentile(45, 1500)
        assert abs((r.percentile_from_bottom + r.top_percent) - 100) < 0.2

    def test_huge_assets_approach_top_percent_near_zero(self):
        """1億円クラスならほぼ全世代トップ（上位0.1%付近）に近づく"""
        r = compute_wealth_percentile(45, 100_000)
        assert r.top_percent <= 1.0

    def test_bucket_boundaries_are_consistent_with_distribution_length(self):
        """区分数(distribution)は boundaries+1（非保有含む）と一致する"""
        r = compute_wealth_percentile(45, 1500)
        assert len(r.distribution) == len(BUCKET_BOUNDARIES_MAN) + 1

    def test_exactly_at_boundary_is_classified_into_upper_bucket(self):
        """境界値ちょうど(例:1000万円)は上の区分に分類される"""
        r = compute_wealth_percentile(45, 1000)
        assert r.bucket_index == 4  # "1000〜2000万円"区分

    def test_distribution_marks_user_bucket(self):
        r = compute_wealth_percentile(45, 1500)
        marked = [d for d in r.distribution if d["is_user_bucket"]]
        assert len(marked) == 1
        assert r.distribution[r.bucket_index]["is_user_bucket"]

    def test_all_age_bands_produce_valid_result(self):
        """全年代でクラッシュせず妥当な範囲の値を返す（回帰テスト）"""
        for band_label in AGE_BAND_RAW_PCT:
            age = {"20代": 25, "30代": 35, "40代": 45, "50代": 55, "60代": 65, "70代": 75}[band_label]
            r = compute_wealth_percentile(age, 1000)
            assert r is not None
            assert r.age_band == band_label
            assert 0 <= r.percentile_from_bottom <= 100
            assert 0 < r.top_percent <= 100

    def test_richer_person_in_younger_age_band_can_have_higher_top_percent_than_older(self):
        """
        年代別分布が異なるため、同じ資産額でも年代によってtop_percentは変わる
        （20代で1000万円は60代で1000万円よりレアなはず）
        """
        r_20s = compute_wealth_percentile(25, 1000)
        r_60s = compute_wealth_percentile(65, 1000)
        assert r_20s.top_percent < r_60s.top_percent


class TestComputeWealthPercentileForBand:
    def test_lets_user_override_age_band(self):
        """39歳(実年代は30代)でも「40代」の分布と明示的に比較できる"""
        r = compute_wealth_percentile_for_band("40代", 1000)
        assert r is not None
        assert r.age_band == "40代"

    def test_matches_age_based_result_for_same_band(self):
        """年齢経由でも年代直接指定でも同じ結果になる（30代の35歳 == "30代"指定）"""
        via_age = compute_wealth_percentile(35, 1000)
        via_band = compute_wealth_percentile_for_band("30代", 1000)
        assert via_age.top_percent == via_band.top_percent
        assert via_age.percentile_from_bottom == via_band.percentile_from_bottom

    def test_unknown_band_returns_none(self):
        assert compute_wealth_percentile_for_band("100代", 1000) is None
