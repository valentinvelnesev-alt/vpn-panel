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
│           VPN Panel · установка      │
╰──────────────────────────────────────╯"

# ── 1. Зависимости ────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Запустите от root:  sudo bash install.sh"

if ! command -v docker >/dev/null; then
	inf "Docker не найден, устанавливаю…"
	curl -fsSL https://get.docker.com | sh || die "не удалось установить Docker"
fi
docker compose version >/dev/null 2>&1 || die "нужен Docker Compose v2 (обновите Docker)"
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

# ── 3. Режим развёртывания ────────────────────────────────────────────
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
else
	read -rp "  Порт панели [4250]: " port
	port=${port:-4250}
	[[ "$port" =~ ^[0-9]+$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || die "некорректный порт"

	set_ DEPLOY_MODE ip
	set_ PANEL_PORT "$port"
	server_ip=$(curl -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
	PANEL_URL="http://${server_ip}:${port}"
fi
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
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.$(get DEPLOY_MODE).yml"
set_ COMPOSE_FILE "docker-compose.yml:docker-compose.$(get DEPLOY_MODE).yml"

inf "
  Собираю и запускаю (первый раз это несколько минут)…"
$COMPOSE up -d --build

inf "  Жду готовности панели…"
for i in $(seq 1 60); do
	if $COMPOSE exec -T panel-api python -c \
		"import urllib.request;urllib.request.urlopen('http://localhost:8000/api/v1/health')" 2>/dev/null; then
		ready=1; break
	fi
	sleep 3
done
[ "${ready:-0}" = 1 ] || die "панель не поднялась. Логи:  $COMPOSE logs panel-api"
ok "Панель работает"

# ── 6. Первый администратор ───────────────────────────────────────────
if $COMPOSE exec -T panel-api python -m app.cli admin-exists 2>/dev/null; then
	ok "Администратор уже создан"
else
	c '1;37' "
  Создайте администратора панели."
	read -rp  "  Логин: " login
	read -rsp "  Пароль (минимум 12 символов): " pass; echo
	read -rsp "  Повторите пароль: " pass2; echo
	[ "$pass" = "$pass2" ] || die "пароли не совпадают"
	[ ${#pass} -ge 12 ]    || die "пароль слишком короткий"
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
echo "  Панель:  $PANEL_URL"
echo "  Логи:    docker compose logs -f"
echo "  Стоп:    docker compose down"

if [ "$(get DEPLOY_MODE)" = "ip" ]; then
	c '1;33' "
  ⚠ Соединение без шифрования (HTTP). Закройте порт для чужих:
      ufw allow from <ваш-ip> to any port $(get PANEL_PORT) proto tcp
    Появится домен — перезапустите install.sh и выберите режим 1."
fi
echo
echo "  Дальше: войдите в панель, укажите адрес и токен Remnawave,"
echo "  затем на вкладке «Бот» вставьте токен от @BotFather."
echo
