from django.conf import settings


def shared(request):
    return {"GOOGLE_CLIENT_ID": settings.GOOGLE_CLIENT_ID, "APP_NAME": "Only Pawns"}
