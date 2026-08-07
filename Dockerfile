# Образ для Варианта Б (headless-браузер). Chromium и системные библиотеки
# уже внутри официального образа Playwright — доставлять ничего не нужно.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FETCH_MODE=browser \
    STATE_PATH=/data/state.json

# Cloudflare часто не пропускает headless-браузер, поэтому запускаем настоящий
# Chromium под виртуальным дисплеем Xvfb (см. CMD в конце файла).
ENV BROWSER_HEADLESS=false

WORKDIR /app

COPY requirements.txt requirements-impersonate.txt requirements-browser.txt ./
RUN pip install --no-cache-dir -r requirements-browser.txt

# Браузеры в образе собраны под ту версию Playwright, что указана в его теге.
# Мы ставим Playwright новее (из-за greenlet — см. requirements-browser.txt),
# поэтому нужную ревизию Chromium докачиваем сами. Системные библиотеки для
# него в образе уже есть.
RUN playwright install chromium

COPY . .

# Каталог для state.json. На Render сюда можно примонтировать Disk,
# чтобы состояние переживало передеплой.
RUN mkdir -p /data

# Виртуальный дисплей для headful-браузера поднимает сам монитор (display.py)
# прямо перед запуском Chromium, поэтому обёртка вроде xvfb-run не нужна:
# python остаётся главным процессом и штатно обрабатывает SIGTERM от Render.
CMD ["python", "monitor.py"]
