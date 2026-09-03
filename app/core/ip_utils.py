import ipaddress
from django.conf import settings


def get_client_ip(request):
    """Реальный IP клиента за reverse proxy.

    Если задан TRUSTED_PROXIES и REMOTE_ADDR доверенный — идём по
    X-Forwarded-For справа налево, пропуская доверенные прокси, и берём
    первый валидный недоверенный адрес (его дописывает прокси, клиент не
    может подделать). Иначе — REMOTE_ADDR.
    """
    trusted = set(getattr(settings, "TRUSTED_PROXIES", []))
    remote = request.META.get("REMOTE_ADDR", "")
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if trusted and remote in trusted and xff:
        for candidate in reversed([p.strip() for p in xff.split(",")]):
            if not candidate or candidate in trusted:
                continue
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue
    return remote
