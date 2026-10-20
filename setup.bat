@echo off
REM One-time setup for Windows. Double-click or run: setup.bat
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
python manage.py makemigrations pawn
python manage.py migrate
python manage.py seed
echo.
echo Done. Start the server with:  .venv\Scripts\activate  then  python manage.py runserver
