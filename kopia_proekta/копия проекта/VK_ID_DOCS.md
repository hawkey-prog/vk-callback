# VK ID — ключевая документация и заметки

## Приложение
- **Client ID:** 54634256
- **Платформа:** Web
- **Уровень конфиденциальности:** Публичное (не конфиденциальное)
- **Базовый домен:** hawkey-prog.github.io
- **Redirect URL:** https://hawkey-prog.github.io/vk-callback/index.html
- **Состояние:** Включено и видно всем
- **Поддержка:** devsupport@corp.vk.com

## Тип приложения: Публичное vs Конфиденциальное

### Публичное (текущее)
- Обмен кода на токены на **фронтенде**
- Лимит: **15 000 запросов/сутки на один IP**
- Не требует `service_token` при обмене
- Не требует указания IP-адреса сервера

### Конфиденциальное (можно переключить)
- Обмен кода на токены через **бэкенд**
- Лимит: **120 000 запросов/сутки на приложение** (с ограниченного IP)
- **Обязателен** `service_token` в запросах обмена кода и refresh
- Нужно указать IP-адрес бэкенда в настройках
- VK проверяет IP-адрес при обмене кода → если не совпадает, запрос отклоняется
- Можно привязать Access token к IP пользователя (параметр `ip`)

⚠️ **Текущая архитектура уже делает обмен на бэкенде (app.py), но приложение публичное.** Это нормально работает, но лимит ниже (15к/сутки). Для повышения лимитов и безопасности стоит переключить на конфиденциальное.

## Scope (права доступа)

### Базовые (доступны сразу)
- `vkid.personal_info` — фамилия, имя, пол, фото профиля, дата рождения (по умолчанию)
- `email` — почта (нужно включить в настройках + в данных регистрации)

### Расширенные (после подтверждения бизнес-профиля)
- `phone` — номер телефона (можно включать/выключать самостоятельно)
- **Другие (друзья, видеозаписи, фотографии, groups и т.д.)** — доступны **в исключительных случаях**, нужно писать на **devsupport@corp.vk.com**

### Важно
- Запрошенный scope **не может быть больше**, чем разрешённый в настройках приложения
- Если scope не указан — берётся `vkid.personal_info` по умолчанию
- `scope` передаётся в запросе авторизации (параметр `scope`), значения разделены пробелами

### ⚠️ Проблема проекта
В `index.html` запрашивается `scope=groups`, но в настройках приложения это право **не включено** (оно расширенное, требует обращения в поддержку). Поэтому токен возвращается только с `vkid.personal_info` (что и видно в `tokens.json`: `"scope": "vkid.personal_info"`).

**Решение:** Написать на devsupport@corp.vk.com с просьбой добавить право `groups` для приложения 54634256. Письмо уже отправлено, ответа нет 2 дня.

## Авторизация без SDK (текущий способ)

### Шаг 1: Запрос кода
```
GET https://id.vk.ru/authorize
  ?response_type=code
  &client_id=54634256
  &redirect_uri=https://hawkey-prog.github.io/vk-callback/index.html
  &state=<random 43+ chars>
  &code_challenge=<base64url(SHA256(code_verifier))>
  &code_challenge_method=S256
  &scope=groups
```

**Важно:** `code_verifier` — 43-128 символов [a-z, A-Z, 0-9, _, -]. `state` — минимум 32 символа.

### Шаг 2: Редирект
```
GET https://<redirect_uri>?code=<authorization_code>&device_id=<device_id>&state=<state>
```
- Код живёт **10 минут**
- `device_id` нужно сохранить и передавать в последующих запросах

### Шаг 3: Обмен кода на токены
```
POST https://id.vk.ru/oauth2/auth
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<authorization_code>
&code_verifier=<code_verifier>
&redirect_uri=<redirect_uri>
&client_id=<client_id>
&device_id=<device_id>
&state=<state>
```

Для **конфиденциального** приложения дополнительно: `service_token=<сервисный ключ>`

**Ответ:**
```json
{
  "refresh_token": "...",
  "access_token": "...",
  "id_token": "...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user_id": 123456789,
  "state": "...",
  "scope": "vkid.personal_info"
}
```

- Access token живёт **1 час**
- Refresh token обновляет пару Access + Refresh

### Шаг 4: Обновление токена
```
POST https://id.vk.ru/oauth2/auth
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&refresh_token=<refresh_token>
&client_id=<client_id>
&device_id=<device_id>
&state=<state>
```

⚠️ После обмена старая пара токенов **инвалидируется**.

## Документация по URL
- [Создание и настройка приложения](https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/create-application)
- [Как работает авторизация на Web](https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/start-integration/how-auth-works/auth-flow-web)
- [Справочник методов API VK ID](https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/api-description)
- [Работа с доступами](https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/work-with-user-info/scopes)
- [Верификация бизнес-профиля](https://id.vk.ru/about/business/go/docs/ru/vkid/latest/vk-id/connection/verification)

## Замечания по коду проекта

1. **TOKEN_URL в .env** указан как `https://oauth.vk.com/access_token`, но в app.py default — `https://id.vk.ru/oauth2/auth`. По документации VK ID правильный endpoint — `https://id.vk.ru/oauth2/auth`. Нужно исправить .env.

2. **Content-Type:** Документация требует `application/x-www-form-urlencoded` для запросов обмена токена. В app.py `requests.post(..., data=params)` — это правильно, requests отправляет form-urlencoded при `data=`.

3. **scope=groups не работает** без разрешения от поддержки. Текущий токен имеет только `vkid.personal_info`.

4. **Конфиденциальное приложение:** Если переключить, нужно добавить `service_token` в запросы и указать IP сервера (89.108.78.99) в настройках.

5. **redirect_uri страница** не должна содержать скриптов (по документации). Но index.html содержит JS — это нарушает требование. Однако VK сейчас это не блокирует.

6. **Лимиты:** Публичное приложение — 15 000 запросов/сутки на IP. Конфиденциальное — 120 000/сутки на приложение.

## Письмо в поддержку
- **Отправлено:** ~17 июля 2026
- **Кому:** devsupport@corp.vk.com
- **Текст:** Просьба добавить право `groups` в список разрешённых scope для приложения 54634256 (методы groups.removeUser и groups.banUser, автоматизация модерации сообщества)
- **Статус:** Нет ответа 2 дня
- **Действие:** Подождать ещё. VK поддержка обычно отвечает 3-5 рабочих дней. Можно отправить повторное письмо через 5 дней.