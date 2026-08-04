"""Загрузка HTML страницы очереди.

Два бэкенда:

* DirectFetcher  — обычный HTTP-клиент с браузерными заголовками (Вариант А).
  Если установлен curl_cffi, используем его: он подделывает TLS-отпечаток
  Chrome, что заметно снижает шанс словить блокировку Cloudflare. Иначе httpx.

* BrowserFetcher — Playwright/Chromium с одним долгоживущим контекстом
  (Вариант Б). Страница открывается один раз, дальше только reload.

Важно (проверено, см. README): Cloudflare на этом сайте отдаёт датацентровым
IP короткий `403 Blocked for security reasons` БЕЗ заголовка `cf-mitigated`.
Это кастомное WAF-правило по IP/ASN, а не JS-челлендж, поэтому браузер его
сам по себе не обходит — нужен «чистый» IP (см. SCRAPE_PROXY).
"""

from __future__ import annotations

import logging

from config import Config

log = logging.getLogger(__name__)

CHROMIUM_MAJOR = "150"


class FetchError(Exception):
    """Сетевая ошибка или неожиданный ответ — считается «ошибкой подряд»."""


class BlockedError(FetchError):
    """Cloudflare/WAF отдал блокировку. Требует вмешательства человека."""


class CaptchaError(BlockedError):
    """Показана капча или JS-челлендж — автоматически не решаем."""


def browser_headers(cfg: Config) -> dict[str, str]:
    """Заголовки один в один как у настоящего Chrome (сняты из HAR)."""
    return {
        "accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "accept-language": "uk-UA,uk;q=0.9,en;q=0.8,pl;q=0.7,ru;q=0.6",
        "cache-control": "max-age=0",
        "priority": "u=0, i",
        "sec-ch-ua": (
            f'"Not;A=Brand";v="8", "Chromium";v="{CHROMIUM_MAJOR}", '
            f'"Google Chrome";v="{CHROMIUM_MAJOR}"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": cfg.user_agent,
    }


def _classify(status: int, body: str) -> None:
    """Бросить осмысленное исключение, если ответ — не нормальная страница."""
    lowered = body[:4000].lower()

    if "blocked for security reasons" in lowered:
        raise BlockedError(
            f"HTTP {status}: Cloudflare заблокировал IP "
            f"(правило WAF «Blocked for security reasons»)"
        )

    if any(
        marker in lowered
        for marker in (
            "just a moment",
            "cf-challenge",
            "cdn-cgi/challenge-platform",
            "checking your browser",
            "enable javascript and cookies to continue",
        )
    ):
        raise CaptchaError(f"HTTP {status}: Cloudflare показывает челлендж/капчу")

    if status == 403:
        raise BlockedError(f"HTTP 403 от сайта (тело: {body[:200]!r})")

    if status != 200:
        raise FetchError(f"HTTP {status}")


class DirectFetcher:
    """Вариант А: прямой HTTP-запрос."""

    name = "direct"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._impl = None
        self._client = None
        self._setup()

    def _setup(self) -> None:
        try:
            from curl_cffi import requests as curl_requests  # noqa: F401

            self._impl = "curl_cffi"
        except ImportError:
            self._impl = "httpx"
        log.info("direct-бэкенд: %s", self._impl)

    def fetch(self) -> str:
        headers = browser_headers(self.cfg)
        proxy = self.cfg.scrape_proxy or None

        if self._impl == "curl_cffi":
            from curl_cffi import requests as curl_requests

            try:
                resp = curl_requests.get(
                    self.cfg.url,
                    headers=headers,
                    impersonate="chrome",
                    timeout=self.cfg.request_timeout,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                )
            except Exception as exc:  # сетевой сбой, TLS, таймаут
                raise FetchError(f"curl_cffi: {exc}") from exc
            _classify(resp.status_code, resp.text)
            return resp.text

        import httpx

        try:
            with httpx.Client(
                http2=True,
                timeout=self.cfg.request_timeout,
                follow_redirects=True,
                proxy=proxy,
            ) as client:
                resp = client.get(self.cfg.url, headers=headers)
        except Exception as exc:
            raise FetchError(f"httpx: {exc}") from exc

        _classify(resp.status_code, resp.text)
        return resp.text

    def close(self) -> None:
        pass


class BrowserFetcher:
    """Вариант Б: Playwright/Chromium, один долгоживущий контекст."""

    name = "browser"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_page(self):
        if self._page is not None:
            return self._page

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()

        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        launch_kwargs: dict = {"headless": True, "args": launch_args}
        if self.cfg.scrape_proxy:
            launch_kwargs["proxy"] = {"server": self.cfg.scrape_proxy}

        self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            user_agent=self.cfg.user_agent,
            locale="uk-UA",
            timezone_id="Europe/Warsaw",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"accept-language": "uk-UA,uk;q=0.9,en;q=0.8"},
        )

        # Снимаем самый заметный признак автоматизации.
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        try:
            from playwright_stealth import stealth_sync

            stealth_sync(self._context)
            log.info("playwright-stealth подключён")
        except ImportError:
            log.info("playwright-stealth не установлен, работаем без него")

        self._page = self._context.new_page()
        return self._page

    def fetch(self) -> str:
        page = self._ensure_page()
        timeout_ms = self.cfg.request_timeout * 1000

        try:
            if page.url == "about:blank":
                resp = page.goto(
                    self.cfg.url, wait_until="domcontentloaded", timeout=timeout_ms
                )
            else:
                # Контекст уже прогрет — просто обновляем данные.
                resp = page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            self.close()  # контекст мог развалиться, пересоздадим на следующем круге
            raise FetchError(f"playwright: {exc}") from exc

        status = resp.status if resp is not None else 0
        html = page.content()
        _classify(status, html)
        return html

    def close(self) -> None:
        for obj, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._pw, "stop"),
        ):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception:
                pass
        self._pw = self._browser = self._context = self._page = None


def build_fetcher(cfg: Config):
    """Создать бэкенд по FETCH_MODE. Режим auto стартует с direct."""
    if cfg.fetch_mode == "browser":
        return BrowserFetcher(cfg)
    return DirectFetcher(cfg)
