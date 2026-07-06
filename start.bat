@echo off
set PYTHONPATH=C:\Apps\FutureCompass
cd /d C:\Apps\FutureCompass
backend\venv\Scripts\uvicorn.exe backend.main:app --reload --host 0.0.0.0 --port 8080
