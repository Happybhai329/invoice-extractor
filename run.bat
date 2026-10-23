@echo off
echo Starting Invoice Extraction System...
echo Open your browser at http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
pause
