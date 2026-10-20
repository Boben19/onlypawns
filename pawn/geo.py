"""Small map helpers shared by the customer app and the staff screens.

Ported from the dexTHER project: place search through OpenStreetMap Nominatim and current weather through
Open-Meteo. Both need no API key. The browser never talks to them directly, so we can cache, rate limit and
send the User-Agent that Nominatim asks for.
"""
import json
import urllib.parse
import urllib.request

from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_GET

UA = "OnlyPawns/1.0 (school project; Palawan Pawn Star)"
WEATHER_CODES = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle",
                 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain", 80: "Rain showers", 81: "Rain showers",
                 82: "Heavy showers", 95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm"}


def _get_json(url, params, timeout=6):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _limited(request, scope, limit=30, window=60):
    key = "geo:%s:%s" % (scope, request.session.session_key or request.META.get("REMOTE_ADDR", "x"))
    n = cache.get(key, 0)
    if n >= limit:
        return True
    cache.set(key, n + 1, window)
    return False


def _num(value, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


@require_GET
def search(request):
    """GET /geo/search/?q=puerto princesa  ->  {"items": [{"label", "lat", "lon"}]} (Philippines first)."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Sign in first."}, status=401)
    q = request.GET.get("q", "").strip()[:80]
    if len(q) < 3:
        return JsonResponse({"items": []})
    if _limited(request, "search"):
        return JsonResponse({"error": "Slow down a little."}, status=429)
    key = "geo:s:" + q.lower()
    items = cache.get(key)
    if items is None:
        try:
            data = _get_json("https://nominatim.openstreetmap.org/search",
                             {"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "ph", "addressdetails": 0})
        except Exception:
            return JsonResponse({"error": "Place search is unavailable right now."}, status=502)
        items = [{"label": d.get("display_name", "")[:120], "lat": float(d["lat"]), "lon": float(d["lon"])} for d in data]
        cache.set(key, items, 3600)
    return JsonResponse({"items": items})


@require_GET
def weather(request):
    """GET /geo/weather/?lat=9.74&lon=118.73  ->  {"temp", "feels", "desc", "rain"}  (cached 15 minutes)."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Sign in first."}, status=401)
    lat, lon = _num(request.GET.get("lat"), -90, 90), _num(request.GET.get("lon"), -180, 180)
    if lat is None or lon is None:
        return JsonResponse({"error": "Bad coordinates."}, status=400)
    if _limited(request, "weather", limit=60):
        return JsonResponse({"error": "Slow down a little."}, status=429)
    key = "geo:w:%.2f:%.2f" % (lat, lon)
    out = cache.get(key)
    if out is None:
        try:
            d = _get_json("https://api.open-meteo.com/v1/forecast", {
                "latitude": lat, "longitude": lon, "current": "temperature_2m,apparent_temperature,precipitation,weather_code",
                "timezone": "auto"})["current"]
        except Exception:
            return JsonResponse({"error": "Weather is unavailable right now."}, status=502)
        out = {"temp": round(d["temperature_2m"]), "feels": round(d["apparent_temperature"]),
               "desc": WEATHER_CODES.get(d.get("weather_code"), "Fair"), "rain": (d.get("precipitation") or 0) > 0}
        cache.set(key, out, 900)
    return JsonResponse(out)
