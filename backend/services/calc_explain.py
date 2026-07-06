"""
シミュレーション結果の計算根拠を、ステップバイステップで生成する。
AIは使わない。数式と実際の数値だけ。
"""


def generate_explanation(result: dict, snapshots: list[dict]) -> dict:
    cur = snapshots[0]
    ret_snap = next((s for s in snapshots if s["age"] == result.get("retirement_age_used", 65)), snapshots[-1])

    # ── FIRE達成年齢の根拠 ──────────────────────────
    fire_steps = []
    exp = cur["total_expense_real"]
    assets_now = cur["assets"]
    fire_threshold = cur["fire_threshold"]
    fire_pct = cur["fire_progress_pct"]

    fire_steps.append({
        "title": "STEP 1｜FIREに必要な資産額を計算（4%ルール）",
        "formula": "必要資産 = 年間支出 × 25",
        "calc": f"{exp:,}万円 × 25 = {fire_threshold:,}万円",
        "note": "4%ルール：毎年4%ずつ取り崩せば資産が尽きないという経験則（米Trinity Study）",
    })
    fire_steps.append({
        "title": "STEP 2｜現在の達成率を確認",
        "formula": "達成率 = 現在資産 ÷ 必要資産 × 100",
        "calc": f"{assets_now:,}万円 ÷ {fire_threshold:,}万円 × 100 = {fire_pct:.1f}%",
        "note": None,
    })

    if result.get("fire_age"):
        fire_age = result["fire_age"]
        years_to_fire = fire_age - cur["age"]
        fire_steps.append({
            "title": "STEP 3｜複利計算で達成年齢を試算",
            "formula": "毎年：前年資産 × (1 + 運用利回り) + 年間純余剰 + 年間投資額",
            "calc": f"現在{cur['age']}歳から{years_to_fire}年後（{fire_age}歳）に{fire_threshold:,}万円を超える見込み",
            "note": f"インフレ率を加味した実質支出が毎年上昇するため、FIREラインも年々上がります",
        })

    # ── 家計健全度の根拠 ──────────────────────────
    household_steps = []
    income = cur["total_income_nominal"]
    total_exp = cur["total_expense_real"]
    invest = cur["investment"]
    net = cur["net_cashflow"]
    sr = cur["savings_rate"]

    household_steps.append({
        "title": "STEP 1｜今年の収入合計（名目）",
        "formula": "本人年収（ピーク係数調整後）+ 配偶者年収",
        "calc": f"本人 {cur['income_nominal']:,}万円 + 配偶者 {cur['spouse_income_nominal']:,}万円 = {income:,}万円",
        "note": "年功序列カーブで現在の年齢における収入係数を掛けて推計しています",
    })
    household_steps.append({
        "title": "STEP 2｜今年の支出合計（インフレ調整済）",
        "formula": "生活費 + 教育費（インフレ率で毎年増加）",
        "calc": f"生活費 {cur['living_expense_real']:,}万円 + 教育費 {cur['education_expense_real']:,}万円 = {total_exp:,}万円",
        "note": "教育費は子供の学校ステージ（小・中・高・大）ごとの文科省調査データを使用",
    })
    household_steps.append({
        "title": "STEP 3｜純余剰と貯蓄率",
        "formula": "純余剰 = 収入 − 支出 − 投資額　／　貯蓄率 = 純余剰 ÷ 収入",
        "calc": f"{income:,} − {total_exp:,} − {invest:,} = {net:,}万円　→　貯蓄率 {sr*100:.1f}%",
        "note": "貯蓄率20%以上でA、10〜20%でB、0〜10%でC、マイナスでD",
    })

    # ── 資産形成力の根拠 ──────────────────────────
    wealth_steps = []
    assets_before = cur["assets"] - cur["asset_change"]
    market_gain = cur["market_gain"]
    behavior_gain = cur["behavior_gain"]
    asset_change = cur["asset_change"]
    r_pct = round(market_gain / assets_before * 100, 1) if assets_before else 0

    wealth_steps.append({
        "title": "STEP 1｜市場要因（運用利回りによる増加）",
        "formula": "市場要因 = 前期末資産 × 運用利回り",
        "calc": f"{assets_before:,}万円 × {r_pct}% = {market_gain:,}万円",
        "note": "あなたがコントロールできない部分。市場が良ければ大きく、悪ければマイナスになります",
    })
    wealth_steps.append({
        "title": "STEP 2｜行動要因（あなたの行動による増加）",
        "formula": "行動要因 = 純余剰 + 積立投資額",
        "calc": f"{net:,}万円（純余剰）+ {invest:,}万円（投資）= {behavior_gain:,}万円",
        "note": "あなたがコントロールできる部分。支出を減らす・積立を増やすで直接改善できます",
    })
    wealth_steps.append({
        "title": "STEP 3｜資産変動の合計",
        "formula": "資産増減 = 市場要因 + 行動要因",
        "calc": f"{market_gain:,}万円 + {behavior_gain:,}万円 = {asset_change:,}万円",
        "note": None,
    })

    # ── 退職時資産の根拠 ──────────────────────────
    ret_steps = []
    ret_assets = result["retirement_assets_man"]
    total_edu = result["total_education_cost_man"]

    ret_steps.append({
        "title": "STEP 1｜毎年の資産変動を複利で積み上げ",
        "formula": "各年：前年資産 × (1+利回り) + 純余剰 + 投資",
        "calc": f"現在{cur['age']}歳から退職まで毎年計算",
        "note": "収入は年功序列カーブで推移。支出はインフレ率で毎年上昇。教育費は各ステージで加算",
    })
    if total_edu > 0:
        ret_steps.append({
            "title": "STEP 2｜教育費の総額を差し引き",
            "formula": "子供の教育費（保育〜大学）を各年の支出に加算",
            "calc": f"教育費総計: {total_edu:,}万円（全期間）",
            "note": "文科省「子供の学習費調査」の年間単価 × 在学年数。インフレ調整済み",
        })
    ret_steps.append({
        "title": f"STEP {3 if total_edu > 0 else 2}｜退職時（{ret_snap['age']}歳）の資産",
        "formula": "上記を退職年齢まで繰り返した結果",
        "calc": f"退職時推計資産: {ret_assets:,}万円",
        "note": f"月間取り崩し可能額: 約{result['monthly_pension_estimate_man']:,}万円（{result.get('life_expectancy', 90) - ret_snap['age']}年間で均等割り）",
    })

    return {
        "fire": fire_steps,
        "household": household_steps,
        "wealth": wealth_steps,
        "retirement": ret_steps,
    }
