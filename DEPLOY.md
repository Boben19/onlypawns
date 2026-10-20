# Deploying Only Pawns on PythonAnywhere

Follows the class handout, with the fixes noted at the end. Replace `yourname` with your PythonAnywhere username.

1. **Push the latest code to GitHub.** `requirements.txt` sits next to `manage.py`. To pin versions the way the handout does, run `pip freeze > requirements.txt` inside your own working virtual environment before you push.
2. **Settings.** In `.env` on the server (never in git) set:
   ```
   SECRET_KEY=<one long random value, keep it forever, it also protects the encrypted ID photos>
   DEBUG=0
   ALLOWED_HOSTS=yourname.pythonanywhere.com
   CSRF_TRUSTED_ORIGINS=https://yourname.pythonanywhere.com
   ```
3. **Bash console on PythonAnywhere:**
   ```
   git clone <your-repo-url> onlypawns
   cd onlypawns
   python3 -m venv ~/onlypawnsenv
   source ~/onlypawnsenv/bin/activate
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py seed          # demo data only, skip for real use
   python manage.py collectstatic
   ```
   Later updates: `git pull`, `pip install -r requirements.txt`, `python manage.py migrate`, `python manage.py collectstatic`, then Reload.
4. **Web tab > Add a new web app > Manual configuration** (pick the same Python version as your console).
5. **Virtualenv:** `/home/yourname/onlypawnsenv`
6. **Static files** (Web tab, Static files section): URL `/static/`, directory `/home/yourname/onlypawns/staticfiles`.
7. **WSGI file** (replace everything in it):
   ```python
   import os
   import sys

   path = "/home/yourname/onlypawns"        # the folder that holds manage.py
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```
8. Press **Reload**, then open `https://yourname.pythonanywhere.com/app/`.

## Things to know

- **Fixes to the handout:** its WSGI snippet uses backticks around the path and the settings name, which is a Python syntax error, so use normal quotes as above. The project is `config.settings`, not `projectsite.settings`. The handout also wraps the app in `StaticFilesHandler`; the static-files mapping in step 6 is the proper way and is what this project expects (`STATIC_ROOT`).
- **Media is not served when `DEBUG=0`.** Receipts are handed out through a permission check (`/receipts/<no>.pdf`), so do not map `/media/` as a static URL, that would expose them. Item photos under `media/items/` are only shown to staff inside the dashboard in development mode; for production serve them through a view or private storage.
- **HTTPS:** with `DEBUG=0` the site redirects to HTTPS and uses secure cookies. PythonAnywhere provides HTTPS on `*.pythonanywhere.com`.
- **Google Sign-In:** add `https://yourname.pythonanywhere.com` as an authorized JavaScript origin and set `GOOGLE_CLIENT_ID` in `.env`. Without it the app falls back to the email-only demo sign-in, which must not be left on a public site.
- **Scheduled jobs:** use the PythonAnywhere Tasks tab for `python manage.py nightly` and `python manage.py backup` (full path to the virtualenv's python).
- **SQLite** is fine for a demo; move to MySQL/PostgreSQL if several branches write at once.
