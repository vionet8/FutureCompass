"""
セットアップ用キー生成スクリプト
実行: python gen_keys.py
"""
import secrets
from cryptography.fernet import Fernet

print("=== Future Compass キー生成 ===\n")
print("以下を .env に貼り付けてください:\n")
print(f"SECRET_KEY={secrets.token_hex(32)}")
print(f"ENCRYPTION_KEY={Fernet.generate_key().decode()}")
print(f"ANTHROPIC_API_KEY=sk-ant-...")
