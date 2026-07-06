@echo off
echo === Future Compass セットアップ ===
cd /d %~dp0\backend

python -m venv venv
call venv\Scripts\activate

pip install -r requirements.txt

echo.
echo === .env ファイルを作成してください ===
if not exist .env (
    copy .env.example .env
    echo .env.example を .env にコピーしました
    echo ANTHROPIC_API_KEY を設定してください
    echo SECRET_KEY と ENCRYPTION_KEY も設定が必要です
)

echo.
echo Fernet暗号化キーを生成するには:
echo python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

echo.
echo セットアップ完了。start.bat で起動してください。
pause
