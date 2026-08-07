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

COPY requirements.txt requirements-browser.txt ./
RUN pip install --no-cache-dir -r requirements-browser.txt

COPY . .

# Каталог для state.json. На Render сюда можно примонтировать Disk,
# чтобы состояние переживало передеплой.
RUN mkdir -p /data

# Запуск идёт через entrypoint.sh: он поднимает виртуальный дисплей для
# headful-браузера. Xvfb уже входит в официальный образ Playwright.
RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
