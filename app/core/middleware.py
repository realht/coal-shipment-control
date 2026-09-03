from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse

from .models import SystemState
from .system_ops import can_view_system_status, get_system_state_readonly


class RequestBodySizeLimitMiddleware:
    """V18-MED-5: отклоняет запросы с телом больше MAX_REQUEST_BODY_SIZE_BYTES.

    Второй эшелон защиты (после reverse-proxy client_max_body_size). Проверяет
    Content-Length ДО разбора multipart, не читая request.body, чтобы
    авторизованный пользователь не занял temp/диск/worker гигабайтным телом до
    валидации в form/view. Если Content-Length отсутствует или нечисловой
    (chunked) — пропускает дальше (fallback на form-лимиты).
    """

    BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in self.BODY_METHODS:
            try:
                size = int(request.META.get("CONTENT_LENGTH"))
            except (TypeError, ValueError):
                size = None
            if size is not None and size > settings.MAX_REQUEST_BODY_SIZE_BYTES:
                return HttpResponse(
                    "Размер запроса превышает допустимый лимит.",
                    status=413,
                    content_type="text/plain; charset=utf-8",
                )
        return self.get_response(request)


class MaintenanceModeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            state = get_system_state_readonly()
        except (OperationalError, ProgrammingError):
            return self.get_response(request)

        # V18-MED-1: read-only — если синглтон ещё не создан, режим считается
        # NORMAL; middleware не вставляет строку, чтобы не конфликтовать с
        # заливкой mysql-дампа в окне DROP/CREATE→INSERT.
        if state is None:
            return self.get_response(request)

        if self._is_allowed_without_checks(request):
            return self.get_response(request)

        user_can_view = can_view_system_status(request.user)

        if state.mode == SystemState.MODE_ADMIN_ONLY and not user_can_view:
            return self._maintenance_response(request, state)

        if state.mode == SystemState.MODE_RESTORE_RUNNING:
            if self._is_restore_admin_path(request) and user_can_view:
                return self.get_response(request)
            return self._maintenance_response(request, state)

        return self.get_response(request)

    def _is_allowed_without_checks(self, request):
        path = request.path
        if path == reverse("login"):
            return not request.user.is_authenticated or can_view_system_status(request.user)
        if path in {reverse("logout"), reverse("core:healthz"), reverse("core:readyz")}:
            return True
        static_url = getattr(settings, "STATIC_URL", "/static/")
        media_url = getattr(settings, "MEDIA_URL", "/uploads/")
        return path.startswith(static_url) or path.startswith(media_url)

    def _is_restore_admin_path(self, request):
        allowed = {
            ("GET", reverse("core:system_status")),
            ("POST", reverse("core:recover_restore")),
        }
        return (request.method, request.path) in allowed

    def _maintenance_response(self, request, state):
        return render(
            request,
            "core/maintenance.html",
            {"system_state": state, "user_can_view": can_view_system_status(request.user)},
            status=503,
        )
