#!/bin/sh
# Точка входа Docker-образа.
#
# Cloudflare часто не пропускает headless-браузер, поэтому при
# BROWSER_HEADLESS=false поднимаем виртуальный дисплей и запускаем настоящий
# Chromium. Xvfb стартуем сами, а не через xvfb-run: так python остаётся
# главным процессом, и SIGTERM от Render доходит до него напрямую — иначе
# монитор не успевает завершиться штатно.
set -e

if [ "${BROWSER_HEADLESS}" = "false" ]; then
	if command -v Xvfb >/dev/null 2>&1; then
		Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/dev/null 2>&1 &
		export DISPLAY=:99

		# Ждём, пока дисплей поднимется, иначе Chromium стартует раньше него.
		i=0
		while [ ! -e /tmp/.X11-unix/X99 ] && [ $i -lt 25 ]; do
			sleep 0.2
			i=$((i + 1))
		done

		if [ -e /tmp/.X11-unix/X99 ]; then
			echo "entrypoint: виртуальный дисплей :99 запущен"
		else
			echo "entrypoint: Xvfb не поднялся, переключаюсь на headless"
			export BROWSER_HEADLESS=true
		fi
	else
		echo "entrypoint: Xvfb не найден в образе, переключаюсь на headless"
		export BROWSER_HEADLESS=true
	fi
fi

# exec — чтобы python заменил собой шелл и получал сигналы напрямую.
exec python monitor.py "$@"
