#!/usr/bin/env bash
# One-time setup for Linux / macOS. Run:  bash setup.sh
set -e
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
python manage.py makemigrations pawn
python manage.py migrate
python manage.py seed
echo
echo "Done. Start the server with:  . .venv/bin/activate && python manage.py runserver"
