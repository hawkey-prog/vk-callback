# Описание проекта — VK Callback Server

## Назначение

Сервер для авторизации пользователей через **VK ID (OAuth2)** и управления группами VK (удаление и бан участников). Состоит из двух частей:

1. **Backend (app.py)** — Flask-сервер на порту 5000, обрабатывает OAuth-код, сохраняет токены, делает вызовы к VK API.
2. **Frontend (index.html)** — статическая страница на GitHub Pages (`https://hawkey-prog.github.io/vk-callback/index.html`), инициирует авторизацию VK ID по PKCE и отправляет код на backend.
3. **Cloudflare Worker (index.js)** — прокси для обхода CORS, пересылает POST-запросы на origin-сервер.

## Архитектура

```
Пользователь → index.html (GitHub Pages)
    ↓ VK ID authorize (PKCE: code_challenge S256, scope=groups)
VK возвращает ?code=***&device_id=*** → index.html
    ↓ POST /vk/exchange-code (code + code_verifier + device_id)
Cloudflare Worker (index.js) → проксирование на origin
    ↓
Backend (app.py, Flask, 127.0.0.1:5000)
    ↓ POST https://id.vk.ru/oauth2/auth (exchange code → tokens)
VK возвращает access_token + refresh_token
    ↓ Сохранение в /opt/vk-bot/tokens.json
```

На продакшене backend доступен через `https://89.108.78.99/` (Nginx reverse proxy → 127.0.0.1:5000).
Cloudflare Worker URL: `https://89-108-78-99.sslip.io/vk/exchange-code`.

## Файлы

| Файл | Назначение |
|---|---|
| `app.py` | Flask-приложение, все эндпоинты |
| `index.html` | Frontend для OAuth-логина (деплой на GitHub Pages) |
| `index.js` | Cloudflare Worker — CORS-прокси на origin |
| `.env` | Переменные окружения (VK_CLIENT_ID, VK_CLIENT_SECRET, VK_TOKEN_URL, VK_API_VERSION, VK_SERVICE_TOKEN, TOKENS_FILE) |
| `tokens.json` | Сохранённые VK-токены (access_token, refresh_token, user_id, device_id, scope) |
| `wrangler.toml` | Конфигурация Cloudflare Worker |
| `VK_ID_DOCS.md` | Документация VK ID и заметки |
| `README.md` | Краткое описание: "vk mini app" |

## Эндпоинты backend (app.py)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/` | Health check — возвращает "VK Bot Server is running" |
| POST | `/vk/exchange-code` | Обмен OAuth-кода на токены (PKCE), сохранение в tokens.json |
| POST | `/vk/refresh` | Обновление access_token через refresh_token |
| POST | `/vk/remove-user` | Удаление пользователя из группы (VK API: `groups.removeUser`) |
| POST | `/vk/ban-user` | Бан пользователя в группе (VK API: `groups.banUser`) |

## Конфигурация (.env)

- `VK_CLIENT_ID` = 54634256
- `VK_CLIENT_SECRET` = 4O0Gx1OopSAq7v7QjURb (⚠️ секрет!)
- `VK_REDIRECT_URI` = https://hawkey-prog.github.io/vk-callback/index.html
- `VK_TOKEN_URL` = https://id.vk.ru/oauth2/auth (актуальный endpoint VK ID)
- `VK_API_VERSION` = 5.199
- `VK_SERVICE_TOKEN` = (пусто — для публичного приложения; заполнить при переходе на конфиденциальное)
- `TOKENS_FILE` = /opt/vk-bot/tokens.json

## Безопасность

- ⚠️ **`.env` и `tokens.json` содержат секреты** (client_secret, access_token, refresh_token) — не коммитить в публичные репозитории!
- Используется PKCE (S256) для защиты OAuth-флоу.
- `state`-параметр проверяется на фронте для защиты от CSRF.
- Backend слушает только `127.0.0.1:5000` (доступ извне через Nginx reverse proxy).
- Подготовлена поддержка `service_token` для перехода на конфиденциальное приложение.

## Технологии

- Python 3, Flask
- requests (для вызовов VK API)
- python-dotenv
- Vanilla JS frontend (crypto.subtle для PKCE)
- Cloudflare Workers (wrangler)

## Деплой

- Backend: сервер `89.108.78.99`, Flask на `127.0.0.1:5000`, Nginx reverse proxy
- Frontend: GitHub Pages репозитория `hawkey-prog/vk-callback`
- Cloudflare Worker: `vk-bot-exchange` (wrangler deploy)
- Токены хранятся в `/opt/vk-bot/tokens.json` (на сервере)

## Известные особенности

- `TOKENS_FILE` настраивается через .env (по умолчанию `/opt/vk-bot/tokens.json` — Linux-путь)
- Логирование отладки пишется в `/tmp/vk_debug.log` (на сервере)
- При ошибке `error_code=5` (протухший токен) автоматически вызывается refresh и повтор запроса
- `scope=groups` запрашивается при авторизации, но право ещё не одобрено поддержкой VK (токен возвращается только с `vkid.personal_info`)
- Поддержка `service_token` добавлена в код — активируется заполнением `VK_SERVICE_TOKEN` в .env

## Статус

Проект — рабочий прототип VK OAuth callback-сервера с управлением группами.
Ожидается ответ от VK поддержки (devsupport@corp.vk.com) по добавлению scope `groups`.