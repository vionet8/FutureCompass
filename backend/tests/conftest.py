"""
pytest全体の共通セットアップ。

Userモデルは文字列参照のrelationship（"UserProfile", "MFTransaction"等）を持つため、
SQLAlchemyのmapper構成時に全モデルクラスが登録済みである必要がある。
main.py の import リストと同じ全モデルをここでimportしておくことで、
どのテストファイルを単体で実行してもmapper解決エラーが起きないようにする。
"""
from backend.models import user, profile, budget, mf_transaction, auto_import, performance, portfolio  # noqa: F401
