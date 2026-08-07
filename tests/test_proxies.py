"""Тесты разбора списка прокси и ротации.

Запуск: python -m pytest tests/ -v (или python tests/test_proxies.py)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxies import ProxyPool, load_proxies, mask, parse_proxy, split_auth  # noqa: E402


def test_split_auth_separates_credentials_for_playwright():
    """Chromium игнорирует логин и пароль в URL — их надо отдавать отдельно."""
    server, user, password = split_auth("http://user:pass@1.2.3.4:8000")
    assert server == "http://1.2.3.4:8000"
    assert (user, password) == ("user", "pass")
    assert "user" not in server and "pass" not in server


def test_split_auth_decodes_escaped_credentials():
    """Пароль, закодированный при разборе строки, должен вернуться исходным."""
    proxy = parse_proxy("1.2.3.4:8000:user:p@ss")
    server, user, password = split_auth(proxy)
    assert server == "http://1.2.3.4:8000"
    assert (user, password) == ("user", "p@ss")


def test_split_auth_without_credentials():
    server, user, password = split_auth("http://1.2.3.4:8000")
    assert server == "http://1.2.3.4:8000"
    assert user is None and password is None


def test_provider_format_host_port_user_pass():
    """Основной формат выгрузок: host:port:user:pass."""
    assert parse_proxy("1.2.3.4:8000:user:pass") == "http://user:pass@1.2.3.4:8000"


def test_host_port_without_auth():
    assert parse_proxy("1.2.3.4:8000") == "http://1.2.3.4:8000"


def test_full_url_is_kept_as_is():
    for url in ("http://u:p@1.2.3.4:8000", "socks5://u:p@1.2.3.4:1080"):
        assert parse_proxy(url) == url


def test_special_characters_in_credentials_are_encoded():
    """Пароль с @ и : не должен ломать разбор URL."""
    result = parse_proxy("1.2.3.4:8000:us@r:p:ss")
    assert result is None or "@1.2.3.4:8000" in result

    result = parse_proxy("1.2.3.4:8000:user:p@ss")
    assert result == "http://user:p%40ss@1.2.3.4:8000"
    from urllib.parse import urlsplit

    assert urlsplit(result).hostname == "1.2.3.4"
    assert urlsplit(result).port == 8000


def test_comments_and_blanks_are_skipped():
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None
    assert parse_proxy("# комментарий") is None


def test_mask_hides_password():
    masked = mask("http://user:secret@1.2.3.4:8000")
    assert "secret" not in masked
    assert "user" in masked and "1.2.3.4:8000" in masked
    assert mask(None) == "нет (прямое подключение)"


def test_load_from_inline_and_dedupes():
    proxies = load_proxies("1.2.3.4:8000\n1.2.3.4:8000, 5.6.7.8:9000")
    assert proxies == ["http://1.2.3.4:8000", "http://5.6.7.8:9000"]


def test_empty_pool_means_direct_connection():
    pool = ProxyPool([])
    assert pool.enabled is False
    assert pool.current() is None
    assert pool.rotate() is None


def test_rotation_walks_the_list():
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    assert pool.current() == "http://a:1"
    assert pool.rotate() == "http://b:2"
    assert pool.rotate() == "http://c:3"


def test_rotation_recovers_when_all_are_dead():
    """Перебрав весь список, пул начинает круг заново, а не встаёт намертво."""
    pool = ProxyPool(["http://a:1", "http://b:2"])
    for _ in range(6):
        assert pool.rotate() is not None
    assert pool.current() in {"http://a:1", "http://b:2"}


def test_mark_good_revives_current():
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.rotate()  # пометит a мёртвым
    assert "http://a:1" in pool.dead
    pool.index = 0
    pool.mark_good()
    assert "http://a:1" not in pool.dead


if __name__ == "__main__":
    import logging

    logging.disable(logging.CRITICAL)

    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  OK   {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
    print("\nПровалено тестов:", failures)
    sys.exit(1 if failures else 0)
