#!/usr/bin/env bash
# Обновление VPN Panel. Запускать из папки панели:
#   cd ~/vpn-panel && bash update.sh
set -euo pipefail

# Раньше здесь был `cd "$(dirname "$0")"` — если скрипт сохраняли и
# запускали не из папки панели (например `bash /tmp/update.sh` для
# диагностики), он переносил выполнение в /tmp и тут же падал на
# "файл .env не найден". Полагаемся на то, что пользователь уже в нужной
# папке (так и написано в подсказке выше и во всех местах, где мы даём
# эту команду), и просто проверяем это явно.

c()   { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok()  { c '0;32' "  ✓ $1"; }
inf() { c '0;36' "$1"; }
err() { c '0;31' "  ✗ $1" >&2; }
die() { err "$1"; exit 1; }

# Без этого сбой любой команды под set -e просто молча обрывал скрипт —
# снаружи выглядело так, будто обновление "прошло", хотя на деле не
# доехало до пересборки. Теперь видно ровно на какой строке и команде.
trap 'err "Обновление прервано на строке $LINENO: $BASH_COMMAND"' ERR

[ "$(id -u)" -eq 0 ] || die "Запустите от root:  sudo bash update.sh"
[ -f .env ] || die "Файл .env не найден — панель не установлена в этой папке"

inf "
╭──────────────────────────────────────╮
│         VPN Panel · обновление       │
╰──────────────────────────────────────╯"

# ── 1. Бэкап .env ─────────────────────────────────────────────────────
BACKUP_DIR="./backups"
BACKUP_FILE="$BACKUP_DIR/env_$(date +%Y%m%d_%H%M%S).bak"
mkdir -p "$BACKUP_DIR"
cp .env "$BACKUP_FILE"
ok "Бэкап .env → $BACKUP_FILE"

# ── 2. Бэкап базы данных ──────────────────────────────────────────────
get() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
DEPLOY_MODE=$(get DEPLOY_MODE)
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.${DEPLOY_MODE}.yml"

DB_BACKUP="$BACKUP_DIR/db_$(date +%Y%m%d_%H%M%S).sql"
inf "  Создаю дамп базы данных…"
if $COMPOSE exec -T postgres pg_dump -U postgres vpn_panel < /dev/null > "$DB_BACKUP" 2>/dev/null; then
    ok "Бэкап БД → $DB_BACKUP"
else
    c '1;33' "  ⚠ Не удалось создать дамп БД (контейнер не запущен?), продолжаю без него"
fi

# ── 3. Получаем обновления ────────────────────────────────────────────
inf "  Получаю обновления из репозитория…"
git fetch origin main

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    ok "Уже установлена последняя версия"
    exit 0
fi

inf "  Изменения:"
git log --oneline "$LOCAL..$REMOTE"
echo

git pull origin main
ok "Код обновлён"

# ── 3.5 Публичный адрес сервера (для ссылок на фото рассылок) ─────────
# Установки до этого обновления не сохраняли его в .env — бэкенд считал
# публичным адресом "localhost", и фото в рассылках не скачивались
# серверами Telegram. Дополняем .env, если поля ещё нет.
if ! grep -qE "^PANEL_PUBLIC_URL=" .env; then
    if [ "$DEPLOY_MODE" = "domain" ]; then
        PUBLIC_URL="https://$(get PANEL_DOMAIN)"
    else
        SERVER_IP=$(curl -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')
        PUBLIC_URL="http://${SERVER_IP}:$(get PANEL_PORT)"
    fi
    echo "PANEL_PUBLIC_URL=$PUBLIC_URL" >> .env
    ok "Добавлен публичный адрес в .env: $PUBLIC_URL"
fi

# ── 4. Пересобираем и перезапускаем ──────────────────────────────────
inf "  Обновляю образы и перезапускаю контейнеры…"
# У panel-api/bot/panel-web нет тега image — только build. `compose pull`
# на некоторых версиях Docker падает на таких сервисах с ошибкой, и под
# set -e скрипт молча обрывается ЗДЕСЬ, до пересборки — обновление внешне
# "проходит", а по факту ничего не меняется. Поэтому pull — только для
# готовых образов (postgres/redis/caddy) и не должен останавливать скрипт.
$COMPOSE pull postgres redis caddy 2>/dev/null || true
$COMPOSE build panel-api bot panel-web
$COMPOSE up -d --remove-orphans

# panel-web — одноразовый контейнер (restart: no): собирает SPA и
# завершается. Docker Compose иногда решает, что раз он уже "Exited (0)",
# трогать его не нужно, даже после свежей сборки — тогда в общем томе
# остаётся старая сборка фронтенда, и в браузере не видно новых полей
# панели. Прогоняем явно, чтобы гарантированно обновить статику.
inf "  Пересобираю фронтенд панели…"
$COMPOSE run --rm panel-web
ok "Контейнеры перезапущены"

# ── 4.5 Применяем миграции базы данных ────────────────────────────────
inf "  Жду готовности panel-api перед миграциями…"
for i in $(seq 1 30); do
    if $COMPOSE exec -T panel-api true < /dev/null 2>/dev/null; then
        break
    fi
    sleep 2
done
if $COMPOSE exec -T panel-api alembic upgrade head < /dev/null; then
    ok "Миграции применены"
else
    c '1;33' "  ⚠ Не удалось применить миграции автоматически — выполните вручную:"
    echo "      docker compose exec panel-api alembic upgrade head"
fi

# ── 5. Готово ─────────────────────────────────────────────────────────
c '0;32' "
╭──────────────────────────────────────╮
│        Обновление завершено          │
╰──────────────────────────────────────╯"
echo "  Логи:  docker compose logs -f"
echo "  Бэкапы хранятся в: $BACKUP_DIR"
echo
c '1;33' "  ⚠ Если в браузере не видно новых полей — обновите страницу"
c '1;33' "    жёстко (Ctrl+Shift+R / Cmd+Shift+R), браузер мог закэшировать старую версию."
echo
