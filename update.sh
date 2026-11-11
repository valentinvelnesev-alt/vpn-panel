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

# Ниже скрипт делает `git reset --hard`, который переписывает и сам файл
# update.sh — если выполнять его прямо с диска, это самомодифицирующийся
# скрипт во время исполнения (тот же класс бага, что раньше ловили на
# `curl | bash`: bash дочитывает файл по мере выполнения, и переписанное
# содержимое может исказить оставшиеся команды). Поэтому один раз копируем
# себя во временный файл и перезапускаемся уже из него — код на диске
# после этого можно спокойно перезаписывать.
#
# При запуске через `curl … | bash` скрипта на диске нет вовсе: $0 — это
# просто «bash», и `cp "$0"` падал с «cannot stat 'bash'» — официальная
# команда обновления не работала. В этом случае скачиваем свою копию.
# Плюс stdin перезапущенного скрипта — /dev/null: иначе docker compose run
# читал бы из пайпа остаток скрипта.
UPDATE_URL="${VPN_PANEL_UPDATE_URL:-https://raw.githubusercontent.com/valentinvelnesev-alt/vpn-panel/main/update.sh}"
if [ -z "${VPN_PANEL_UPDATE_REEXEC:-}" ]; then
    TMP_SELF="$(mktemp /tmp/vpn-panel-update.XXXXXX.sh)"
    if [ -f "$0" ]; then
        cp "$0" "$TMP_SELF"
    elif ! curl -fsSL "$UPDATE_URL" -o "$TMP_SELF"; then
        rm -f "$TMP_SELF"
        printf '\033[0;31m  ✗ Не удалось скачать update.sh — проверьте доступ к GitHub\033[0m\n' >&2
        exit 1
    fi
    chmod +x "$TMP_SELF"
    export VPN_PANEL_UPDATE_REEXEC=1
    exec bash "$TMP_SELF" "$@" < /dev/null
fi
trap 'rm -f "$0"' EXIT

# Без этого сбой любой команды под set -e просто молча обрывал скрипт —
# снаружи выглядело так, будто обновление "прошло", хотя на деле не
# доехало до пересборки. Теперь видно ровно на какой строке и команде.
trap 'err "Обновление прервано на строке $LINENO: $BASH_COMMAND"' ERR

[ "$(id -u)" -eq 0 ] || die "Запустите от root:  sudo bash update.sh"
[ -f .env ] || die "Файл .env не найден — панель не установлена в этой папке"

# Compose использует одно имя проекта для всех копий репозитория. Если на
# сервере есть вложенная или другая копия, запуск из неверной папки может
# пересоздать существующие контейнеры с чужими ключами и настройками.
EXISTING_POSTGRES=$(docker ps -aq --filter label=com.docker.compose.project=vpn-panel --filter label=com.docker.compose.service=postgres | head -1)
if [ -n "$EXISTING_POSTGRES" ]; then
    CONTAINER_DIR=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$EXISTING_POSTGRES")
    if [ -n "$CONTAINER_DIR" ] && [ "$CONTAINER_DIR" != "<no value>" ] && [ "$(cd "$CONTAINER_DIR" 2>/dev/null && pwd -P || true)" != "$(pwd -P)" ]; then
        die "Контейнеры панели запущены из другой папки: $CONTAINER_DIR. Обновляйте панель из неё."
    fi
fi

inf "
╭──────────────────────────────────────╮
│          Обновление панели           │
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
case "$DEPLOY_MODE" in domain|ip) ;; *) die "Неизвестный DEPLOY_MODE в .env: $DEPLOY_MODE" ;; esac
PROXY_MODE=$(get PROXY_MODE)
OVERLAY=$DEPLOY_MODE
case "$PROXY_MODE" in
    nginx) OVERLAY=nginx ;;
    caddy) ;;
    "")
        # Старые установки могли сохранить только выбор файлов Compose.
        case "$(get COMPOSE_FILE)" in *docker-compose.nginx.yml*) OVERLAY=nginx ;; esac
        ;;
    *) die "Неизвестный PROXY_MODE в .env: $PROXY_MODE" ;;
esac
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.${OVERLAY}.yml"

# Другие команды docker compose без -f читают COMPOSE_FILE из .env. Держим
# его в согласии с выбранным прокси, чтобы они не заняли порты Nginx.
EXPECTED_COMPOSE_FILE="docker-compose.yml:docker-compose.${OVERLAY}.yml"
if [ "$(get COMPOSE_FILE)" != "$EXPECTED_COMPOSE_FILE" ]; then
    TMP_ENV=$(mktemp .env.update.XXXXXX)
    chmod 600 "$TMP_ENV"
    sed '/^COMPOSE_FILE=/d' .env > "$TMP_ENV"
    printf 'COMPOSE_FILE=%s\n' "$EXPECTED_COMPOSE_FILE" >> "$TMP_ENV"
    mv "$TMP_ENV" .env
    ok "Режим Docker Compose приведён в соответствие с настройками прокси"
fi

DB_BACKUP="$BACKUP_DIR/db_$(date +%Y%m%d_%H%M%S).sql"
DB_USER=$(get POSTGRES_USER); DB_USER=${DB_USER:-vpnpanel}
DB_NAME=$(get POSTGRES_DB); DB_NAME=${DB_NAME:-vpnpanel}
inf "  Создаю дамп базы данных…"
if $COMPOSE exec -T postgres pg_dump -U "$DB_USER" "$DB_NAME" < /dev/null > "$DB_BACKUP" 2>/dev/null; then
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
    ok "Код уже последней версии — проверяю образы и запуск"
else
# --no-pager: в интерактивном терминале git открывает less и ждёт
# клавишу — снаружи это выглядит как зависание, а мобильный SSH-клиент
# потом рвёт соединение. Список ограничен: после переписывания истории
# старый коммит сервера не входит в новую, и LOCAL..REMOTE — вся история.
inf "  Изменения:"
git --no-pager log --oneline -n 15 "$LOCAL..$REMOTE" 2>/dev/null || true
echo

# reset --hard, а не pull: локальные правки самого update.sh (например,
# другие права доступа или ручное редактирование во время диагностики
# проблемы) иначе обрывают merge с ошибкой "would be overwritten" и молча
# останавливают обновление. .env и данные вне git (см. .gitignore), терять
# нечего — можно безопасно перезаписать код версией из репозитория.
git reset --hard origin/main
ok "Код обновлён"
fi

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
# По умолчанию тянем готовые образы из реестра — быстро и без сборки на
# сервере. BUILD_FROM_SOURCE=1 пересобирает локально (форк со своим кодом).
if [ "${BUILD_FROM_SOURCE:-0}" = 1 ]; then
    $COMPOSE -f docker-compose.build.yml build panel-api bot panel-web
else
    $COMPOSE pull
fi
$COMPOSE up -d --remove-orphans
# Caddy монтирует конфиг из репозитория отдельным файлом; git reset
# подменяет файл новым, а контейнер продолжает видеть старый, пока его не
# пересоздать. Без этого изменения Caddyfile не применялись бы.
$COMPOSE up -d --force-recreate --no-deps caddy

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

# ── 4.6 Секретный путь панели ─────────────────────────────────────────
if [ -z "$(get PANEL_SECRET_PATH)" ]; then
    c '1;33' "
  ⚠ Панель открывается по адресу сервера без секретного пути — любой
    сканер видит страницу входа. Включить (ссылка изменится!):
      sed -i '/^PANEL_SECRET_PATH=/d' .env && echo \"PANEL_SECRET_PATH=\$(openssl rand -hex 6)\" >> .env
      $COMPOSE up -d --force-recreate panel-api caddy
      grep PANEL_SECRET_PATH .env   # панель: <адрес>/<этот путь>"
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
