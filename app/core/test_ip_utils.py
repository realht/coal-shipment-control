from django.test import RequestFactory, override_settings

from core.ip_utils import get_client_ip


def _request(remote_addr, xff=None):
    factory = RequestFactory()
    request = factory.get("/")
    request.META["REMOTE_ADDR"] = remote_addr
    if xff is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = xff
    return request


@override_settings(TRUSTED_PROXIES=[])
def test_no_trusted_proxies_returns_remote_addr():
    request = _request("203.0.113.5", xff="1.2.3.4")
    assert get_client_ip(request) == "203.0.113.5"


@override_settings(TRUSTED_PROXIES=["172.18.0.1"])
def test_trusted_proxy_returns_rightmost_untrusted_not_spoofed():
    # клиент подделывает левый элемент, прокси дописывает реальный IP справа
    request = _request("172.18.0.1", xff="9.9.9.9, 203.0.113.5")
    assert get_client_ip(request) == "203.0.113.5"


@override_settings(TRUSTED_PROXIES=["172.18.0.1"])
def test_invalid_xff_entries_are_skipped():
    request = _request("172.18.0.1", xff="203.0.113.5, not-an-ip")
    assert get_client_ip(request) == "203.0.113.5"


@override_settings(TRUSTED_PROXIES=["172.18.0.1", "172.18.0.2"])
def test_all_xff_entries_trusted_falls_back_to_remote_addr():
    request = _request("172.18.0.1", xff="172.18.0.2")
    assert get_client_ip(request) == "172.18.0.1"


@override_settings(TRUSTED_PROXIES=["172.18.0.1"])
def test_untrusted_remote_addr_ignores_xff():
    # прямой доступ к 8000 в обход прокси — XFF не доверяем
    request = _request("203.0.113.99", xff="1.2.3.4, 5.6.7.8")
    assert get_client_ip(request) == "203.0.113.99"
