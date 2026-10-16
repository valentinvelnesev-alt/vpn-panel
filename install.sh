#!/usr/bin/env bash
# Установка VPN Panel на чистый Ubuntu-сервер.
#   bash install.sh
set -euo pipefail

cd "$(dirname "$0")"
ENV_FILE=.env

c()  { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok()  { c '0;32' "  ✓ $1"; }
inf() { c '0;36' "$1"; }
err() { c '0;31' "  ✗ $1" >&2; }
die() { err "$1"; exit 1; }

inf "
╭──────────────────────────────────────╮
│             Установка панели         │
╰──────────────────────────────────────╯"

# ── 1. Зависимости ────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Запустите от root:  sudo bash install.sh"

# Условие охватывает и прерванную ранее установку: если docker поставился, а
# плагин compose — нет (например, скрипт остановили в середине apt), get.docker.com
# идемпотентно доустанавливает недостающее при повторном запуске install.sh.
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
	# Без этого apt на свежем сервере задаёт вопросы (например, о кодировке
	# консоли) и установка встаёт, пока человек не ответит.
	export DEBIAN_FRONTEND=noninteractive
	inf "Ставлю Docker (доустанавливаю, если установка прерывалась)…"
	# Прерванный apt оставляет полу-настроенные пакеты — доводим их до конца,
	# иначе get.docker.com упрётся в тот же незавершённый dpkg.
	command -v dpkg >/dev/null 2>&1 && { dpkg --configure -a >/dev/null 2>&1 || true; }
	curl -fsSL https://get.docker.com | sh || die "не удалось установить Docker. Доведите вручную:  apt-get update && dpkg --configure -a && apt-get -f install -y  — затем запустите install.sh снова"
fi
docker compose version >/dev/null 2>&1 || die "Docker есть, но Compose v2 не работает. Выполните:  dpkg --configure -a && apt-get -f install -y && apt-get install -y docker-compose-plugin  — затем запустите install.sh снова"
ok "Docker готов"

# ── 2. Существующая установка ─────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
	c '1;33' "
  Файл .env уже существует — панель, похоже, установлена."
	read -rp "  Перенастроить? Секреты и данные сохранятся [y/N]: " a
	[[ "${a,,}" == y ]] || { inf "Отменено."; exit 0; }
	RECONFIGURE=1
else
	RECONFIGURE=0
	cp .env.example "$ENV_FILE"
fi

# Читает значение ключа из .env
get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
# Записывает ключ в .env (заменяет строку или добавляет)
set_() {
	if grep -qE "^$1=" "$ENV_FILE"; then
		sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"
	else
		printf '%s=%s\n' "$1" "$2" >>"$ENV_FILE"
	fi
}

# ── 3. VPN-панель ─────────────────────────────────────────────────────
# Бот выдаёт ключи в одной панели. Выбор фиксируется при установке: ключи
# клиентов в базе привязаны к ней, поэтому сменить панель потом — это
# переезд, а не переключатель в настройках.
cur_panel=$(get PANEL_TYPE)
c '1;37' "
  С какой VPN-панелью будет работать бот?"
echo "    1) Remnawave"
echo "    2) 3x-ui (версия 3.0 или новее)"
case "$cur_panel" in 3xui) def_panel=2 ;; *) def_panel=1 ;; esac
read -rp "  Выбор [1/2, Enter — $def_panel]: " panel_choice
panel_choice=${panel_choice:-$def_panel}
case "$panel_choice" in
	1) new_panel=remnawave; PANEL_NAME=Remnawave ;;
	2) new_panel=3xui; PANEL_NAME=3x-ui ;;
	*) die "выберите 1 или 2" ;;
esac
if [ -n "$cur_panel" ] && [ "$cur_panel" != "$new_panel" ]; then
	c '1;33' "  ⚠ Панель меняется ($cur_panel → $new_panel). Уже выданные ключи останутся
    в старой панели — бот перестанет их видеть. Адрес и токен новой панели
    нужно будет указать заново в настройках."
	read -rp "  Продолжить? [y/N]: " a
	[[ "${a,,}" == y ]] || { inf "Отменено."; exit 0; }
fi
set_ PANEL_TYPE "$new_panel"
ok "Панель: $PANEL_NAME"

# ── 3a. Режим развёртывания ────────────────────────────────────────────
c '1;37' "
  Как будете открывать панель?"
echo "    1) По домену, с HTTPS  — нужен домен, направленный A-записью на этот сервер"
echo "    2) По IP, без домена   — http://<IP>:4250, без шифрования"
read -rp "  Выбор [1/2]: " mode

if [ "$mode" = "1" ]; then
	read -rp "  Домен (например panel.example.com): " domain
	[ -n "$domain" ] || die "домен обязателен"
	read -rp "  E-mail для Let's Encrypt: " email
	[ -n "$email" ] || die "e-mail обязателен"

	# Проверяем, что домен указывает сюда — иначе сертификат не выдадут.
	server_ip=$(curl -fsS --max-time 10 https://api.ipify.org || echo '')
	domain_ip=$(getent ahostsv4 "$domain" 2>/dev/null | awk 'NR==1{print $1}' || echo '')
	if [ -n "$server_ip" ] && [ -n "$domain_ip" ] && [ "$server_ip" != "$domain_ip" ]; then
		c '1;33' "  ⚠ $domain указывает на $domain_ip, а сервер — $server_ip."
		c '1;33' "    Пока A-запись не обновится, сертификат выдан не будет."
		read -rp "  Продолжить? [y/N]: " a
		[[ "${a,,}" == y ]] || exit 1
	fi

	set_ DEPLOY_MODE domain
	set_ PANEL_DOMAIN "$domain"
	set_ ACME_EMAIL "$email"
	PANEL_URL="https://$domain"

	# Чем обслуживать HTTPS. Caddy занимает порты 80 и 443 целиком, поэтому
	# на сервере, где уже работает Nginx с другими сайтами, ему там не место:
	# в варианте 2 Caddy слушает только 127.0.0.1, а Nginx принимает домен.
	busy=""
	for p in 80 443; do
		ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$p$" && busy="$busy $p"
	done
	if [ "$(get PROXY_MODE)" = nginx ]; then def_proxy=2; else def_proxy=1; fi
	if [ -n "$busy" ] && [ "$(get PROXY_MODE)" != caddy ] && [ "$RECONFIGURE" = 0 ]; then
		def_proxy=2
		c '1;33' "
  ⚠ На сервере заняты порты:$busy — там уже работает веб-сервер (например, Nginx).
    Вариант 1 с ними конфликтует, поэтому по умолчанию выбран вариант 2."
	fi
	c '1;37' "
  Чем обслуживать HTTPS?"
	echo "    1) Caddy — рекомендуем. Сам получает сертификат, ничего настраивать не нужно."
	echo "                Требует свободные порты 80 и 443."
	echo "    2) Nginx — если на сервере уже стоит Nginx (с другими сайтами)."
	echo "                Другие сайты не затрагиваются: добавится один отдельный файл."
	read -rp "  Выбор [1/2, Enter — $def_proxy]: " proxy_choice
	proxy_choice=${proxy_choice:-$def_proxy}
	case "$proxy_choice" in
		1) PROXY_MODE=caddy ;;
		2) PROXY_MODE=nginx ;;
		*) die "выберите 1 или 2" ;;
	esac
	if [ "$PROXY_MODE" = nginx ]; then
		command -v nginx >/dev/null 2>&1 || die "Nginx не найден на сервере. Установите его (apt install -y nginx) или выберите Caddy."
		# Панель открывается с корня домена (/assets, /api…), поэтому делить
		# домен с другим сайтом нельзя: Nginx отдаст запрос тому, кто был первым,
		# и вместо панели будет 404. Нужен отдельный поддомен.
		dom_re=${domain//./\\.}
		if [ ! -f /etc/nginx/sites-available/vpn-panel.conf ] && [ ! -f /etc/nginx/conf.d/vpn-panel.conf ] \
			&& nginx -T 2>/dev/null | grep -qE "^[[:space:]]*server_name[^;]*[[:space:]]${dom_re}[[:space:];]"; then
			die "В Nginx уже есть сайт на домене $domain. Панель нельзя поставить на тот же домен.
    Создайте поддомен (например panel.$domain) — A-запись на IP этого сервера —
    и запустите установщик снова, указав его."
		fi
		PANEL_PORT=$(get PANEL_PORT); PANEL_PORT=${PANEL_PORT:-4250}
		if [ "$RECONFIGURE" = 0 ] && ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PANEL_PORT$"; then
			die "Порт $PANEL_PORT занят. Освободите его или впишите другой в .env (PANEL_PORT=…) и запустите установщик снова."
		fi
		set_ PANEL_PORT "$PANEL_PORT"
	fi
	set_ PROXY_MODE "$PROXY_MODE"
else
	PROXY_MODE=caddy
	set_ PROXY_MODE caddy
	read -rp "  Порт панели [4250]: " port
	port=${port:-4250}
	[[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || die "некорректный порт"

	set_ DEPLOY_MODE ip
	set_ PANEL_PORT "$port"
	server_ip=$(curl -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
	PANEL_URL="http://${server_ip}:${port}"
fi
# Публичный адрес сервера — по нему собираются ссылки на фото рассылок,
# которые потом скачивают серверы Telegram. Без этого поля бэкенд решил бы,
# что публичный адрес — "localhost", и фото не скачивались бы никогда.
set_ PANEL_PUBLIC_URL "$PANEL_URL"

# Секретный путь: без него по адресу сервера открывается страница входа,
# и сканеры сразу видят, что тут панель. С ним — пустой 404 для всех, кто
# не знает ссылку. При перенастройке сохраняем уже выданный путь.
if [ -z "$(get PANEL_SECRET_PATH)" ]; then
	set_ PANEL_SECRET_PATH "$(openssl rand -hex 6)"
fi
PANEL_LOGIN_URL="${PANEL_URL}/$(get PANEL_SECRET_PATH)"
ok "Режим настроен"

# ── 4. Секреты (только при первой установке) ──────────────────────────
if [ "$RECONFIGURE" = 0 ] || [ -z "$(get SECRET_KEY)" ]; then
	set_ POSTGRES_PASSWORD "$(openssl rand -hex 24)"
	set_ SECRET_KEY        "$(openssl rand -hex 32)"
	set_ ENCRYPTION_KEY    "$(openssl rand -base64 32 | tr '+/' '-_')"
	ok "Секреты сгенерированы"
fi
chmod 600 "$ENV_FILE"

# ── 5. Запуск ─────────────────────────────────────────────────────────
# За Nginx порты 80/443 не нужны: Caddy слушает только 127.0.0.1 (оверлей nginx).
OVERLAY=$(get DEPLOY_MODE)
[ "$(get PROXY_MODE)" = nginx ] && OVERLAY=nginx
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.$OVERLAY.yml"
set_ COMPOSE_FILE "docker-compose.yml:docker-compose.$OVERLAY.yml"

# По умолчанию тянем готовые образы из реестра — это секунды вместо минут
# сборки из исходников. BUILD_FROM_SOURCE=1 собирает локально (форк/разработка).
if [ "${BUILD_FROM_SOURCE:-0}" = 1 ]; then
	inf "
  Собираю образы из исходников…"
	$COMPOSE -f docker-compose.build.yml build
	$COMPOSE up -d
else
	inf "
  Скачиваю образы и запускаю…"
	$COMPOSE pull
	$COMPOSE up -d
	fi

inf "  Жду готовности панели…"
for i in $(seq 1 60); do
	if $COMPOSE exec -T panel-api python -c \
		"import urllib.request;urllib.request.urlopen('http://localhost:8000/api/v1/health')" < /dev/null 2>/dev/null; then
		ready=1; break
	fi
	sleep 3
done
[ "${ready:-0}" = 1 ] || die "панель не поднялась. Логи:  $COMPOSE logs panel-api"
ok "Панель работает"

# ── 5a. Nginx ─────────────────────────────────────────────────────────
# Один отдельный файл для домена панели; остальные сайты не трогаем.
if [ "$(get PROXY_MODE)" = nginx ]; then
	domain=$(get PANEL_DOMAIN); email=$(get ACME_EMAIL); port=$(get PANEL_PORT)
	if [ -d /etc/nginx/sites-available ] && [ -d /etc/nginx/sites-enabled ]; then
		NGINX_CONF=/etc/nginx/sites-available/vpn-panel.conf
		NGINX_LINK=/etc/nginx/sites-enabled/vpn-panel.conf
	else
		NGINX_CONF=/etc/nginx/conf.d/vpn-panel.conf
		NGINX_LINK=""
	fi
	if [ -f "$NGINX_CONF" ]; then
		# Не перезаписываем: certbot уже мог дописать в файл HTTPS.
		ok "Конфиг Nginx уже есть ($NGINX_CONF) — оставляю как есть"
	else
		cat >"$NGINX_CONF" <<NGINX
# VPN Panel — создано install.sh. Затрагивает только домен $domain.
map \$http_upgrade \$vpnpanel_connection {
	default upgrade;
	''      close;
}

server {
	listen 80;
	listen [::]:80;
	server_name $domain;

	# Бэкапы для переноса из других ботов бывают большими.
	client_max_body_size 200m;

	location / {
		proxy_pass http://127.0.0.1:$port;
		proxy_http_version 1.1;
		proxy_set_header Host \$host;
		proxy_set_header X-Real-IP \$remote_addr;
		proxy_set_header X-Forwarded-For \$remote_addr;
		proxy_set_header X-Forwarded-Proto \$scheme;
		proxy_set_header Upgrade \$http_upgrade;
		proxy_set_header Connection \$vpnpanel_connection;
		proxy_read_timeout 300s;
		proxy_send_timeout 300s;
	}
}
NGINX
		[ -n "$NGINX_LINK" ] && ln -sf "$NGINX_CONF" "$NGINX_LINK"
		ok "Конфиг Nginx создан: $NGINX_CONF"
	fi
	nginx -t >/dev/null 2>&1 || { nginx -t; die "Nginx отклонил конфиг. Другие сайты не затронуты, но проверьте вывод выше"; }
	systemctl reload nginx 2>/dev/null || nginx -s reload || die "не удалось перезагрузить Nginx"
	ok "Nginx перезагружен"

	# HTTPS: certbot дописывает 443 только в блок нашего домена.
	if [ ! -d "/etc/letsencrypt/live/$domain" ]; then
		inf "  Получаю сертификат Let's Encrypt для $domain…"
		command -v certbot >/dev/null 2>&1 || {
			export DEBIAN_FRONTEND=noninteractive
			apt-get install -y certbot python3-certbot-nginx >/dev/null 2>&1 || true
		}
		if certbot --nginx -d "$domain" -m "$email" --agree-tos --no-eff-email --redirect -n; then
			ok "HTTPS включён"
		else
			c '1;33' "  ⚠ Сертификат не выдан (проверьте, что A-запись $domain указывает на этот сервер).
    Без HTTPS вход в панель не заработает. Когда запись обновится, выполните:
      certbot --nginx -d $domain -m $email --agree-tos --redirect"
		fi
	else
		ok "Сертификат для $domain уже есть"
	fi
fi

# ── 6. Первый администратор ───────────────────────────────────────────
if $COMPOSE exec -T panel-api python -m app.cli admin-exists < /dev/null 2>/dev/null; then
	ok "Администратор уже создан"
else
	c '1;37' "
  Создайте администратора панели."
	read -rp "  Логин: " login
	while true; do
		read -rsp "  Пароль (минимум 12 символов): " pass; echo
		if [ ${#pass} -lt 12 ]; then
			err "пароль слишком короткий, попробуйте снова"
			continue
		fi
		read -rsp "  Повторите пароль: " pass2; echo
		if [ "$pass" != "$pass2" ]; then
			err "пароли не совпадают, попробуйте снова"
			continue
		fi
		break
	done
	ADMIN_LOGIN="$login" ADMIN_PASSWORD="$pass" \
		$COMPOSE exec -T -e ADMIN_LOGIN -e ADMIN_PASSWORD panel-api \
		python -m app.cli create-admin || die "не удалось создать администратора"
	ok "Администратор создан"
fi

# ── 7. Готово ─────────────────────────────────────────────────────────
c '0;32' "
╭──────────────────────────────────────╮
│              Готово                  │
╰──────────────────────────────────────╯"
echo "  Панель:  $PANEL_LOGIN_URL"
c '1;33' "  ⚠ Сохраните эту ссылку: без секретного пути в конце сервер отвечает 404.
    Посмотреть её позже:  grep PANEL_SECRET_PATH .env"
echo "  Логи:    docker compose logs -f"
echo "  Стоп:    docker compose down"

if [ "$(get DEPLOY_MODE)" = "ip" ]; then
	c '1;33' "
  ⚠ Соединение без шифрования (HTTP). Закройте порт для чужих:
      ufw allow from <ваш-ip> to any port $(get PANEL_PORT) proto tcp
    Появится домен — перезапустите install.sh и выберите режим 1."
fi
echo
echo "  Дальше: войдите в панель, укажите адрес и токен $PANEL_NAME,"
echo "  затем на вкладке «Бот» вставьте токен от @BotFather."
echo
