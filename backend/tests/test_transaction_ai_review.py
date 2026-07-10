"""AI取引レビューのテスト（Anthropic APIはモックし、実通信しない）"""
import json
from datetime import date
from unittest.mock import patch, MagicMock

from backend.services.transaction_ai_review import (
    request_ai_suggestions,
    _build_review_payload,
    _parse_ai_json,
    MAX_TRANSACTIONS_PER_REVIEW,
)


def make_txn(**overrides) -> dict:
    base = {
        "id": "t1",
        "date": date(2026, 6, 15),
        "description": "SBI証券 投信積立",
        "amount_yen": -50000,
        "institution": "楽天銀行",
        "category_major": "水道・光熱費",
        "category_minor": "電気",
        "is_transfer": False,
    }
    base.update(overrides)
    return base


def _mock_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


class TestParseAiJson:
    def test_parses_plain_json(self):
        result = _parse_ai_json('[{"transaction_id": "t1"}]')
        assert result == [{"transaction_id": "t1"}]

    def test_strips_markdown_fence(self):
        text = '```json\n[{"transaction_id": "t1"}]\n```'
        result = _parse_ai_json(text)
        assert result == [{"transaction_id": "t1"}]

    def test_empty_array(self):
        assert _parse_ai_json("[]") == []


class TestBuildReviewPayload:
    def test_includes_expected_fields_only(self):
        payload = _build_review_payload([make_txn()])
        assert payload[0]["id"] == "t1"
        assert payload[0]["date"] == "2026-06-15"
        assert "memo" not in payload[0]  # メモは送らない


class TestRequestAiSuggestions:
    def test_empty_transactions_returns_empty_without_api_call(self):
        with patch("backend.services.transaction_ai_review.client") as mock_client:
            result = request_ai_suggestions([])
            assert result == []
            mock_client.messages.create.assert_not_called()

    def test_parses_valid_suggestions(self):
        ai_output = json.dumps([{
            "transaction_id": "t1",
            "issue": "振替漏れの疑い",
            "suggested_category_major": None,
            "suggested_category_minor": None,
            "suggested_is_transfer": True,
            "reasoning": "SBI証券への投信積立に見えるため",
        }])
        with patch("backend.services.transaction_ai_review.client") as mock_client:
            mock_client.messages.create.return_value = _mock_response(ai_output)
            result = request_ai_suggestions([make_txn()])
            assert len(result) == 1
            assert result[0]["transaction_id"] == "t1"
            assert result[0]["suggested_is_transfer"] is True

    def test_api_failure_returns_empty_list_not_exception(self):
        """AI呼び出し失敗時は例外を投げず空リストを返す（グレースフルデグラデーション）"""
        with patch("backend.services.transaction_ai_review.client") as mock_client:
            mock_client.messages.create.side_effect = Exception("network error")
            result = request_ai_suggestions([make_txn()])
            assert result == []

    def test_malformed_json_returns_empty_list(self):
        with patch("backend.services.transaction_ai_review.client") as mock_client:
            mock_client.messages.create.return_value = _mock_response("これはJSONではありません")
            result = request_ai_suggestions([make_txn()])
            assert result == []

    def test_non_list_json_returns_empty_list(self):
        with patch("backend.services.transaction_ai_review.client") as mock_client:
            mock_client.messages.create.return_value = _mock_response('{"not": "a list"}')
            result = request_ai_suggestions([make_txn()])
            assert result == []

    def test_limits_to_max_transactions(self):
        many_txns = [make_txn(id=f"t{i}") for i in range(MAX_TRANSACTIONS_PER_REVIEW + 50)]
        with patch("backend.services.transaction_ai_review.client") as mock_client:
            mock_client.messages.create.return_value = _mock_response("[]")
            request_ai_suggestions(many_txns)
            call_args = mock_client.messages.create.call_args
            prompt = call_args.kwargs["messages"][0]["content"]
            # 送信件数がMAX以下に収まっていること（プロンプト内のtransaction_id出現回数で確認）
            assert prompt.count('"id":') <= MAX_TRANSACTIONS_PER_REVIEW
