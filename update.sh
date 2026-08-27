#!/usr/bin/env bash
# Обновление VPN Panel
#   bash update.sh
set -euo pipefail

cd "$(dirname "$0")"

c()   { printf '\033[%sm%s\033[0m\n' "$1" "$2"; }
ok()  { c '0;32' "  ✓ $1"; }
inf() { c '0;36' "$1"; }
err() { c '0;31' "  ✗ $1" >&2; }
die() { err "$1"; exit 1; }

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
if $COMPOSE exec -T postgres pg_dump -U postgres vpn_panel > "$DB_BACKUP" 2>/dev/null; then
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

# ── 4. Пересобираем и перезапускаем ──────────────────────────────────
inf "  Обновляю образы и перезапускаю контейнеры…"
$COMPOSE pull
$COMPOSE up -d --build
ok "Контейнеры перезапущены"

# ── 5. Готово ─────────────────────────────────────────────────────────
c '0;32' "
╭──────────────────────────────────────╮
│        Обновление завершено          │
╰──────────────────────────────────────╯"
echo "  Логи:  docker compose logs -f"
echo "  Бэкапы хранятся в: $BACKUP_DIR"
echo
