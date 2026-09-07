# Редиректор подписки для Happ и INCY

Telegram не открывает ссылки `happ://` и `incy://` напрямую, поэтому бот
даёт обычный `https`-адрес, который перебрасывает в приложение.

Ставится **на сервер домена подписок** (тот, что отдаёт `https://sub.<домен>/…`),
рядом со страницей подписки Remnawave. К серверу панели отношения не имеет.

## Что делает бот

Формирует цель и кодирует её в параметр `url`:

```
happ://add/https://sub.luxinet.ru/<ключ>
incy://import/https://sub.luxinet.ru/<ключ>
```
```
https://sub.luxinet.ru/miniapp/redirect.html?url=<urlencoded-target>
```

Домен подставляется из самой ссылки подписки, отдельной настройки не требует.

## Установка

1. Положите `redirect.html` в папку `redirect` рядом с `docker-compose.yml`.
   **Проверьте первую строку скрипта**: переменная `domain` должна совпадать
   с вашим доменом подписок, вместе с завершающим слэшем.
2. В `docker-compose.yml` смонтируйте папку в контейнер Caddy:

```yaml
    volumes:
      - ./redirect:/srv/redirect:ro
```

3. В `Caddyfile` добавьте маршрут **до** `reverse_proxy`, иначе запрос уйдёт
   на страницу подписки и вернётся 502:

```caddy
handle_path /miniapp/* {
    root * /srv/redirect
    rewrite * /redirect.html
    file_server
}

reverse_proxy * http://subscription-page:3010
```

4. Проверьте и примените:

```bash
cp docker-compose.yml docker-compose.yml.bak
cp Caddyfile Caddyfile.bak
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
docker compose up -d caddy
```

5. Убедитесь, что страница отдаётся:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  'https://sub.luxinet.ru/miniapp/redirect.html?url=test'
```

Ожидается `200`, а в теле — «Некорректная ссылка для подключения»: параметр
`test` не проходит проверку домена, и это правильно.

6. В панели, раздел «Настройки», включите
   «Открывать приложение по кнопке „Подключиться“».

## Безопасность

Страница пускает ровно два префикса — `happ://add/<домен>` и
`incy://import/<домен>`. Всё остальное показывает ошибку и никуда не ведёт,
поэтому подставить чужой адрес через параметр `url` нельзя.
