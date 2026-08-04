# Образ для Варианта Б (headless-браузер). Chromium и системные библиотеки
# уже внутри официального образа Playwright — доставлять ничего не нужно.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FETCH_MODE=auto \
    STATE_PATH=/data/state.json

WORKDIR /app

COPY requirements.txt requirements-browser.txt ./
RUN pip install --no-cache-dir -r requirements-browser.txt

COPY . .

# Каталог для state.json. На Render сюда можно примонтировать Disk,
# чтобы состояние переживало передеплой.
RUN mkdir -p /data

CMD ["python", "monitor.py"]
