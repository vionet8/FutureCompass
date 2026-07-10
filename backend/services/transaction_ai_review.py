"""
AIによる取引の異常検知（振替漏れ・分類誤りの疑いがある取引を検出し、修正案を提案する）。

全件をAIに送るとコスト・レイテンシが大きいため、直近N件に絞って送信する。
出力は「疑わしい取引のみ」に限定するようプロンプトで指示し、レスポンスサイズを抑える。
"""

import json
import logging

import anthropic
from ..core.config import get_settings

logger = logging.getLogger("transaction_ai_review")
settings = get_settings()
client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

MAX_TRANSACTIONS_PER_REVIEW = 150


def _build_review_payload(transactions: list[dict]) -> list[dict]:
    """AIに送る最小限の取引情報（内部IDはtransaction_idとして含める。個人特定情報は含まない）"""
    return [
        {
            "id": t["id"],
            "date": t["date"].isoformat() if hasattr(t["date"], "isoformat") else str(t["date"]),
            "description": t["description"],
            "amount_yen": t["amount_yen"],
            "institution": t["institution"],
            "category_major": t["category_major"],
            "category_minor": t["category_minor"],
            "is_transfer": t["is_transfer"],
        }
        for t in transactions
    ]


def _parse_ai_json(text: str) -> list[dict]:
    """Claudeの応答からJSON配列を抽出する（```json フェンス付きでも対応）"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    return json.loads(cleaned)


def request_ai_suggestions(transactions: list[dict], user_context: str | None = None) -> list[dict]:
    """
    取引リストをAIに送り、振替漏れ・分類誤りの疑いがある取引の修正案を返す。
    戻り値: [{transaction_id, issue, suggested_category_major, suggested_category_minor,
              suggested_is_transfer, reasoning}, ...]
    AI呼び出し失敗時は例外を投げず空リストを返す（家計分析全体を壊さないため）。

    user_context: 取引データだけでは読み取れない、ユーザー固有の資金の流れに関する
      補足情報（例:「楽天ペイの残高は楽天証券への投資資金として使うことがある」）。
      これが無いと、例えば楽天ペイ経由の支払いが投資向けなのか純粋な生活費なのか
      AIには判断のしようがない。
    """
    batch = transactions[-MAX_TRANSACTIONS_PER_REVIEW:]
    payload = _build_review_payload(batch)
    if not payload:
        return []

    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    context_block = (
        f"\n【ユーザーからの補足情報（資金の流れについて）】\n{user_context}\n"
        if user_context and user_context.strip()
        else ""
    )

    prompt = f"""あなたは家計簿データのクレンジングを支援するアシスタントです。
以下はマネーフォワードから取り込んだ取引データです。自動分類が誤っている可能性がある
取引だけを検出してください。特に次の2パターンに注目してください：

1. 振替漏れの疑い: 証券会社・銀行間の資金移動（投資信託の買付、口座間振替など）に見えるのに
   is_transferがfalseになっている取引。これらは家計の収支に混ざると実態と乖離する。
2. 分類誤りの疑い: descriptionの内容とcategory_major/category_minorが明らかに合っていない取引。
{context_block}
【取引データ】
{payload_text}

疑わしい取引が無ければ空配列 [] を返してください。
出力が長くなりすぎないよう、最も確信度が高いものから最大20件までに絞ってください。
出力は以下のJSON配列のみとし、説明文やMarkdownのコードフェンスは付けないでください。
reasoningは20文字程度の簡潔な一言にしてください：
[
  {{
    "transaction_id": "取引のid",
    "issue": "振替漏れの疑い または 分類誤りの疑い",
    "suggested_category_major": "修正後の大項目（変更不要ならnull）",
    "suggested_category_minor": "修正後の中項目（変更不要ならnull）",
    "suggested_is_transfer": true/false/null（変更不要ならnull）,
    "reasoning": "判断理由を簡潔に"
  }}
]"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestions = _parse_ai_json(message.content[0].text)
        if not isinstance(suggestions, list):
            return []
        return suggestions
    except Exception as e:
        logger.warning("AI取引レビューに失敗しました: %s", e)
        return []
