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

import importlib.util
import logging
import time

from config import Config

log = logging.getLogger(__name__)

CHROMIUM_MAJOR = "150"

# Признаки страницы-челленджа Cloudflare. Важно отличать её от жёсткой
# блокировки: челлендж настоящий браузер проходит сам за несколько секунд,
# а «Blocked for security reasons» не проходится ничем, кроме смены IP.
CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "cdn-cgi/challenge-platform",
    "checking your browser",
    "enable javascript and cookies to continue",
)

# Вердикты проверки прокси.
VERDICT_OK = "OK, САЙТ ПУСКАЕТ"
VERDICT_CHALLENGE = "ЧЕЛЛЕНДЖ/КАПЧА"
VERDICT_BLOCKED = "IP ЗАБЛОКИРОВАН"
VERDICT_ERROR = "ОШИБКА"
VERDICT_UNREACHABLE = "СЕТЬ НЕДОСТУПНА"


def looks_like_challenge(html: str) -> bool:
    lowered = html[:6000].lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


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

    if looks_like_challenge(body):
        raise CaptchaError(f"HTTP {status}: Cloudflare показывает челлендж/капчу")

    if status == 403:
        raise BlockedError(f"HTTP 403 от сайта (тело: {body[:200]!r})")

    if status != 200:
        raise FetchError(f"HTTP {status}")


class DirectFetcher:
    """Вариант А: прямой HTTP-запрос."""

    name = "direct"

    def __init__(self, cfg: Config, proxy_provider=None) -> None:
        self.cfg = cfg
        # Функция, возвращающая адрес прокси на момент запроса: пул может
        # переключиться между проверками.
        self.proxy_provider = proxy_provider or (lambda: cfg.scrape_proxy or None)
        self._impl = None
        self._client = None
        self._setup()

    def _setup(self) -> None:
        # curl_cffi подделывает TLS-отпечаток Chrome, поэтому предпочитаем его.
        self._impl = "curl_cffi" if importlib.util.find_spec("curl_cffi") else "httpx"
        log.info("direct-бэкенд: %s", self._impl)

    def fetch(self) -> str:
        headers = browser_headers(self.cfg)
        proxy = self.proxy_provider()

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

    def __init__(self, cfg: Config, proxy_provider=None) -> None:
        self.cfg = cfg
        self.proxy_provider = proxy_provider or (lambda: cfg.scrape_proxy or None)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._launched_proxy = None

    def _ensure_page(self):
        # Прокси задаётся при запуске браузера, поэтому смена прокси требует
        # перезапуска контекста.
        if self._page is not None and self.proxy_provider() != self._launched_proxy:
            log.info("Прокси сменился — перезапускаю браузер")
            self.close()

        if self._page is not None:
            return self._page

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()

        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1440,900",
            # Признаки автоматизации, по которым Cloudflare отличает робота.
            "--disable-features=IsolateOrigins,site-per-process",
        ]
        launch_kwargs: dict = {
            "headless": self.cfg.browser_headless,
            "args": launch_args,
        }
        log.info(
            "Запускаю Chromium (%s)",
            "headless" if self.cfg.browser_headless else "headful под Xvfb",
        )
        self._launched_proxy = self.proxy_provider()
        if self._launched_proxy:
            from proxies import split_auth

            # Chromium не читает логин и пароль из URL прокси — только из
            # отдельных полей, иначе подключение к авторизованному прокси падает.
            server, username, password = split_auth(self._launched_proxy)
            proxy_config: dict = {"server": server}
            if username:
                proxy_config["username"] = username
            if password:
                proxy_config["password"] = password
            launch_kwargs["proxy"] = proxy_config

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

        self._apply_stealth(self._context)

        self._page = self._context.new_page()
        return self._page

    @staticmethod
    def _apply_stealth(context) -> None:
        """Спрятать признаки автоматизации.

        API у playwright-stealth менялся: в 1.x это функция stealth_sync,
        в 2.x — метод Stealth().apply_stealth_sync. Поддерживаем оба, чтобы
        обновление зависимости не отключало маскировку молча.
        """
        try:
            import playwright_stealth
        except ImportError:
            log.info("playwright-stealth не установлен, работаем без него")
            return

        try:
            if hasattr(playwright_stealth, "stealth_sync"):
                playwright_stealth.stealth_sync(context)
            elif hasattr(playwright_stealth, "Stealth"):
                playwright_stealth.Stealth().apply_stealth_sync(context)
            else:
                log.warning("playwright-stealth есть, но знакомого API в нём нет")
                return
            log.info("playwright-stealth подключён")
        except Exception as exc:
            log.warning("playwright-stealth не применился: %s", str(exc)[:200])

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

        # Cloudflare отдал челлендж — его как раз и должен пройти браузер.
        # Страница сама выполнит проверку и перезагрузится на настоящий контент,
        # надо только дождаться. Полученный cf_clearance живёт в контексте,
        # поэтому следующие проверки идут уже без задержки.
        if looks_like_challenge(html):
            html = self._wait_for_challenge(page)
            # Челлендж пройден — исходный 403 больше не описывает результат.
            status = 200 if not looks_like_challenge(html) else status

        _classify(status, html)
        return html

    def _wait_for_challenge(self, page) -> str:
        """Дождаться, пока Cloudflare пропустит. Возвращает итоговый HTML."""
        deadline = time.monotonic() + self.cfg.challenge_wait_seconds
        started = time.monotonic()
        log.info("Cloudflare показал челлендж, жду прохождения...")

        clicked = False
        while time.monotonic() < deadline:
            page.wait_for_timeout(1500)
            try:
                html = page.content()
            except Exception:
                continue  # страница в этот момент могла перезагружаться

            if not looks_like_challenge(html):
                log.info("Челлендж пройден за %.0f сек", time.monotonic() - started)
                return html

            # Часть челленджей проходит сама, но «managed challenge» ждёт клика
            # по чекбоксу Turnstile внутри iframe. Пробуем один раз.
            if not clicked and time.monotonic() - started > 6:
                clicked = self._try_click_turnstile(page)

        log.warning("Челлендж не пройден за %d сек", self.cfg.challenge_wait_seconds)
        self._log_challenge_details(page)
        return page.content()

    def _try_click_turnstile(self, page) -> bool:
        """Кликнуть по чекбоксу Turnstile, если челлендж ждёт взаимодействия."""
        try:
            frame = page.frame_locator('iframe[src*="challenges.cloudflare.com"]')
            checkbox = frame.locator('input[type="checkbox"]')
            if checkbox.count() == 0:
                return True  # чекбокса нет — челлендж должен пройти сам
            checkbox.click(timeout=5000)
            log.info("Кликнул по чекбоксу Turnstile")
        except Exception as exc:
            log.debug("Кликнуть по Turnstile не вышло: %s", str(exc)[:150])
        return True

    def _log_challenge_details(self, page) -> None:
        """Записать, как выглядит непройденный челлендж — иначе его не починить."""
        try:
            log.warning("  заголовок страницы: %r", page.title()[:120])
        except Exception:
            pass
        try:
            text = page.inner_text("body", timeout=5000)
            log.warning("  текст: %r", " ".join(text.split())[:300])
        except Exception:
            pass
        try:
            frames = page.locator('iframe[src*="challenges.cloudflare.com"]').count()
            log.warning("  iframe'ов Turnstile на странице: %d", frames)
        except Exception:
            pass

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


def probe(cfg: Config, proxy: str | None = None, timeout: int | None = None) -> dict:
    """Проверить, пускает ли сайт с текущего IP (или через SCRAPE_PROXY).

    Возвращает словарь с кодом ответа, вердиктом и заголовком cf-ray — по его
    суффиксу видно, через какой дата-центр Cloudflare пришёл запрос.
    Нужно, чтобы проверять VPS и прокси ДО того, как за них платить.
    """
    import httpx

    from proxies import mask

    if proxy is None:
        proxy = cfg.scrape_proxy or None
    result: dict = {"proxy": mask(proxy), "proxy_url": proxy}

    try:
        with httpx.Client(
            http2=True,
            timeout=timeout or cfg.request_timeout,
            follow_redirects=True,
            proxy=proxy,
        ) as client:
            resp = client.get(cfg.url, headers=browser_headers(cfg))
    except Exception as exc:
        result.update(status=None, verdict=VERDICT_UNREACHABLE, detail=str(exc))
        return result

    result["status"] = resp.status_code
    result["cf_ray"] = resp.headers.get("cf-ray", "—")
    result["size"] = len(resp.text)

    try:
        _classify(resp.status_code, resp.text)
    except CaptchaError as exc:
        result.update(verdict=VERDICT_CHALLENGE, detail=str(exc))
    except BlockedError as exc:
        result.update(verdict=VERDICT_BLOCKED, detail=str(exc))
    except FetchError as exc:
        result.update(verdict=VERDICT_ERROR, detail=str(exc))
    else:
        from parser import parse

        status = parse(resp.text)
        result.update(verdict=VERDICT_OK, detail=f"состояние очереди: {status}")

    return result


def check_proxies(cfg: Config, proxies: list[str], workers: int = 20) -> list[tuple[str, str]]:
    """Проверить список прокси параллельно, вернуть пары (адрес, вердикт).

    Быстрая HTTP-проверка нужна не только чтобы найти пропускающие прокси, но и
    чтобы отделить безнадёжные (жёсткий бан) от годных для браузера (челлендж).
    Порядок исходного списка сохраняется, чтобы результат был воспроизводим.
    """
    import concurrent.futures

    from proxies import mask

    if not proxies:
        return []

    def check(proxy: str) -> tuple[str, str]:
        result = probe(cfg, proxy=proxy, timeout=cfg.proxy_check_timeout)
        return proxy, result["verdict"]

    results: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for proxy, verdict in pool.map(check, proxies):
            log.info("  %-45s %s", mask(proxy), verdict)
            results.append((proxy, verdict))

    return results


def usable_proxies(results: list[tuple[str, str]], browser_mode: bool) -> list[str]:
    """Отобрать прокси, которые есть смысл использовать в текущем режиме.

    Прямые запросы годятся только там, где сайт пускает сразу. Браузер, кроме
    того, умеет проходить челлендж — значит, такие адреса для него тоже годные,
    и в списке они идут после «чистых».
    """
    clean = [proxy for proxy, verdict in results if verdict == VERDICT_OK]
    if not browser_mode:
        return clean

    challenged = [proxy for proxy, verdict in results if verdict == VERDICT_CHALLENGE]
    return clean + challenged


def build_fetcher(cfg: Config, proxy_provider=None):
    """Создать бэкенд по FETCH_MODE. Режим auto стартует с direct."""
    if cfg.fetch_mode == "browser":
        return BrowserFetcher(cfg, proxy_provider)
    return DirectFetcher(cfg, proxy_provider)
