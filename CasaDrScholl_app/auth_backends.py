"""Backend Django con limitación de intentos para todos los formularios de acceso."""

import hashlib

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache


def _cache_keys(request, username):
    ip_address = request.META.get('REMOTE_ADDR', 'unknown')
    username_ip = hashlib.sha256(
        f'{(username or "").casefold()}|{ip_address}'.encode('utf-8')
    ).hexdigest()
    ip_hash = hashlib.sha256(ip_address.encode('utf-8')).hexdigest()
    return (
        f'login-throttle:user-ip:{username_ip}',
        f'login-throttle:ip:{ip_hash}',
    )


def login_attempts_blocked(request, username):
    """Limita una combinación usuario/IP y también el volumen total por IP."""
    if request is None:
        return False
    pair_key, ip_key = _cache_keys(request, username)
    return (
        cache.get(pair_key, 0) >= settings.LOGIN_THROTTLE_MAX_ATTEMPTS
        or cache.get(ip_key, 0) >= settings.LOGIN_THROTTLE_MAX_IP_ATTEMPTS
    )


def _record_failure(cache_key, timeout):
    if not cache.add(cache_key, 1, timeout=timeout):
        try:
            cache.incr(cache_key)
        except ValueError:
            # La clave pudo expirar entre add() e incr().
            cache.set(cache_key, 1, timeout=timeout)


class ThrottledModelBackend(ModelBackend):
    """Aplica el mismo límite a /login/ y al inicio de sesión de Django admin."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if login_attempts_blocked(request, username):
            return None

        user = super().authenticate(
            request, username=username, password=password, **kwargs
        )
        if request is None:
            return user

        pair_key, ip_key = _cache_keys(request, username)
        if user is not None:
            cache.delete(pair_key)
            cache.delete(ip_key)
        else:
            timeout = settings.LOGIN_THROTTLE_WINDOW_SECONDS
            _record_failure(pair_key, timeout)
            _record_failure(ip_key, timeout)
        return user
