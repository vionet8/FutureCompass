import anthropic
from ..core.config import get_settings

settings = get_settings()
client = anthropic.Anthropic(api_key=settings.anthropic_api_key)


def generate_fp_report(anonymized_profile: dict, simulation_result: dict) -> str:
    """
    個人情報は匿名化済みのデータのみをAIに送信。
    名前・メールアドレスは含まない。
    """
    prompt = f"""あなたはFP（ファイナンシャルプランナー）として、以下のライフプランシミュレーション結果を分析してください。

【プロフィール（匿名）】
{_format_profile(anonymized_profile)}

【シミュレーション結果】
{_format_result(simulation_result)}

以下の形式でレポートを作成してください：

## 現状の強み
（2〜3点、具体的な数値を引用して）

## リスクと注意点
（2〜3点、具体的に）

## 改善提案
（2〜3点、実行可能なアクション）

## FIRE達成見通し
（達成可能か、何歳頃か、条件は何か）

重要：本レポートは情報提供を目的としており、投資助言ではありません。個別銘柄の推奨は行いません。"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def generate_scenario_comparison(
    anonymized_profile: dict,
    scenarios: list[dict],
) -> str:
    scenario_text = "\n".join(
        f"- {s['scenario']}: FIRE {s['fire_age']}歳 / 老後資産{s['retirement_assets_man']}万円 / 不足{s['shortfall_man']}万円"
        for s in scenarios
    )

    prompt = f"""FPとして、以下のシナリオ比較を分析し、それぞれのメリット・デメリットを簡潔に説明してください。

【プロフィール（匿名）】
{_format_profile(anonymized_profile)}

【シナリオ比較】
{scenario_text}

各シナリオについて2〜3行で要点を述べてください。最後に推奨シナリオと理由を述べてください。
投資助言・個別銘柄推奨は行わないこと。"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _format_profile(p: dict) -> str:
    lines = []
    for k, v in p.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def generate_household_analysis(payload: dict) -> str:
    """
    家計データ（匿名化済み）をAIに送り、削減提案・分析を生成する。
    """
    import json
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)

    prompt = f"""あなたは家計改善の専門FP（ファイナンシャルプランナー）です。
以下の家計データを分析し、具体的な改善提案をしてください。

【家計データ（匿名）】
{payload_text}

以下の形式で分析レポートを作成してください：

## 今月の家計診断
（収支状況を3行で評価。数値を引用すること）

## 前年同月と比べて増えている支出
（フラグされた項目を具体的に。なぜ問題か・目安金額も）

## じわじわ増えているカテゴリ
（トレンドデータがあれば分析。なければ省略）

## 削減提案（優先順位順）
1. （カテゴリ名）：現在 X円 → 目標 Y円（月Z円削減可能）
   具体的な方法：
2. （同上）
3. （同上）

## 年間インパクト
（上記削減を実行した場合の年間効果）

重要：本レポートは情報提供を目的としており、投資助言ではありません。"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _format_result(r: dict) -> str:
    return f"""  FIRE達成: {'可能' if r.get('fire_possible') else '困難'} ({r.get('fire_age', 'N/A')}歳)
  老後資産（退職時）: {r.get('retirement_assets_man', 0):,}万円
  月間取り崩し可能額: {r.get('monthly_pension_estimate_man', 0):,}万円
  ベンチマーク比差: {r.get('vs_benchmark_diff_man', 0):+,}万円
  教育費総額（概算）: {r.get('total_education_cost_man', 0):,}万円
  老後不足額: {r.get('shortfall_man', 0):,}万円"""
