"""
Сервер модерации VK-сообщества.

Токен берётся через VK ID (OAuth 2.1 + PKCE): страница авторизации отдаёт сюда
код, сервер меняет его на пару access + refresh и дальше обновляет сам. Права
`groups` выданы приложению индивидуально по обращению в devsupport, поэтому на
шаге авторизации их нужно явно запросить — иначе VK вернёт только
`vkid.personal_info`.

Задания от бота (BotHelp) исполняются одним из двух способов:

  1. напрямую с сервера — основной режим;
  2. через очередь, если VK почему-то не принимает токен с сервера: тогда
     задания разбирает человек или мини-приложение (`app.html`).

Режим определяется автоматически и перепроверяется при каждой ошибке доступа.
"""

import json
import os
import threading
import time
import uuid

import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_VERSION = os.getenv("VK_API_VERSION", "5.199")
STATE_FILE = os.getenv("STATE_FILE", "/opt/vk-bot/state.json")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")
DEFAULT_GROUP_ID = os.getenv("VK_GROUP_ID", "")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "https://hawkey-prog.github.io")

# VK ID: обмен кода на токен и его обновление.
CLIENT_ID = os.getenv("VK_CLIENT_ID", "")
REDIRECT_URI = os.getenv("VK_REDIRECT_URI", "")
TOKEN_URL = os.getenv("VK_TOKEN_URL", "https://id.vk.ru/oauth2/auth")
# Нужен только конфиденциальному приложению; у публичного остаётся пустым.
SERVICE_TOKEN = os.getenv("VK_SERVICE_TOKEN", "")

# Задание, взятое мини-приложением, но не подтверждённое за это время,
# считается брошенным (закрыли вкладку) и возвращается в очередь.
LEASE_TIMEOUT = 300

# Коды VK, означающие «этим токеном отсюда нельзя»: истёк, нет прав,
# group auth, неподходящий тип токена. Все они — повод уйти в очередь,
# а не сообщать боту об ошибке.
TOKEN_ERROR_CODES = {5, 15, 27, 1051}

_lock = threading.Lock()

EMPTY_STATE = {
    "access_token": "",
    "refresh_token": "",
    "device_id": "",
    "scope": "",
    "user_id": "",
    "expires": 0,
    "group_id": "",
    "server_side_ok": False,
    "token_updated": 0,
    "queue": [],
    "history": [],
}


# --- Хранилище ---------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return dict(EMPTY_STATE)
    state = dict(EMPTY_STATE)
    state.update(data)
    return state


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# --- Доступ ------------------------------------------------------------------

def check_secret(expected, header):
    if not expected:
        return True
    return request.headers.get(header, "") == expected


def deny():
    return jsonify({"error": "forbidden"}), 403


def may_moderate():
    """Модерация доступна и боту, и админской странице.

    Админский секрет строго старше ботовского, поэтому отдельный заголовок
    для страницы проверки заводить незачем.
    """
    return (check_secret(BOT_SECRET, "X-Bot-Secret")
            or check_secret(ADMIN_SECRET, "X-Admin-Secret"))


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Admin-Secret, X-Bot-Secret")
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/vk/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "", 204


# --- VK ----------------------------------------------------------------------

def vk_call(state, method, params):
    """Возвращает (ответ_vk, код_ошибки_или_None)."""
    payload = dict(params)
    payload["access_token"] = state["access_token"]
    payload["v"] = API_VERSION
    try:
        response = requests.post(
            "https://api.vk.com/method/" + method, data=payload, timeout=15
        )
        result = response.json()
    except Exception as exc:
        return {"error": {"error_msg": str(exc)}}, -1

    if "error" in result:
        return result, result["error"].get("error_code")
    return result, None


def token_alive(state):
    if not state["access_token"]:
        return False
    # expires == 0 означает бессрочный ключ, такой проверять не нужно.
    return state["expires"] == 0 or state["expires"] > time.time() + 60


def probe_server_side(state):
    """Проверяет, пускает ли VK этот токен с сервера. Метод требует scope groups."""
    _, code = vk_call(state, "groups.get", {"filter": "admin", "count": 1})
    return code is None


# --- VK ID: токены -----------------------------------------------------------

def store_tokens(state, result):
    """Раскладывает ответ VK ID по состоянию.

    Обновление инвалидирует прежнюю пару, поэтому и access, и refresh
    перезаписываются вместе — держать старый refresh бессмысленно и вредно.
    """
    state["access_token"] = result.get("access_token", "")
    state["refresh_token"] = result.get("refresh_token", "")
    state["scope"] = result.get("scope", "")
    state["token_updated"] = int(time.time())
    expires_in = int(result.get("expires_in") or 0)
    state["expires"] = int(time.time()) + expires_in if expires_in else 0
    if result.get("user_id"):
        state["user_id"] = str(result["user_id"])
    if result.get("device_id"):
        state["device_id"] = str(result["device_id"])


def vk_id_post(params):
    """Запрос к VK ID. Возвращает (результат, ошибка_строкой_или_None)."""
    payload = dict(params)
    if SERVICE_TOKEN:
        payload["service_token"] = SERVICE_TOKEN
    try:
        response = requests.post(TOKEN_URL, data=payload, timeout=30)
        result = response.json()
    except Exception as exc:
        return None, str(exc)
    if "error" in result:
        return None, result.get("error_description") or result.get("error")
    return result, None


def refresh_tokens(state):
    """Обновляет пару по refresh_token. Возвращает текст ошибки или None."""
    if not state.get("refresh_token"):
        return "no refresh_token"
    result, error = vk_id_post({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": state["refresh_token"],
        "device_id": state.get("device_id", ""),
        "state": uuid.uuid4().hex + uuid.uuid4().hex[:11],
    })
    if error:
        return error
    store_tokens(state, result)
    return None


# --- Очередь -----------------------------------------------------------------

def enqueue(state, action, user_id, group_id, reason):
    task = {
        "id": uuid.uuid4().hex[:12],
        "action": action,
        "user_id": str(user_id),
        "group_id": str(group_id),
        "status": "pending",
        "reason": reason,
        "created": int(time.time()),
        "leased_at": 0,
    }
    state["queue"].append(task)
    return task


def archive(state, task, ok, detail):
    task["status"] = "done" if ok else "failed"
    task["detail"] = detail
    task["finished"] = int(time.time())
    state["queue"] = [t for t in state["queue"] if t["id"] != task["id"]]
    state["history"].insert(0, task)
    del state["history"][200:]


def release_stale(state):
    now = time.time()
    for task in state["queue"]:
        if task["status"] == "in_progress" and now - task["leased_at"] > LEASE_TIMEOUT:
            task["status"] = "pending"
            task["leased_at"] = 0


# --- Общая логика модерации --------------------------------------------------

def moderate(action, user_id, group_id):
    """Выполняет действие сразу или ставит в очередь. Возвращает (тело, http-код)."""
    with _lock:
        state = load_state()

        if not group_id:
            group_id = state.get("group_id") or DEFAULT_GROUP_ID
        if not group_id:
            return {"error": "group_id is required"}, 400

        if not state["access_token"]:
            return {"error": "no token: пройдите авторизацию на странице входа"}, 409

        # Токен живёт час, поэтому к этому моменту он протух в большинстве
        # случаев — это норма, а не сбой: обновляем и работаем дальше.
        if not token_alive(state):
            error = refresh_tokens(state)
            if error:
                task = enqueue(state, action, user_id, group_id, "refresh failed: " + error)
                save_state(state)
                return {"status": "queued", "task_id": task["id"],
                        "reason": "не удалось обновить токен: " + error}, 202
            save_state(state)

        if not state["server_side_ok"]:
            task = enqueue(state, action, user_id, group_id, "server-side calls rejected")
            save_state(state)
            return {"status": "queued", "task_id": task["id"], "reason": "client-side mode"}, 202

        method = "groups.ban" if action == "ban" else "groups.removeUser"
        params = (
            {"group_id": group_id, "owner_id": user_id, "comment_visible": 0}
            if action == "ban"
            else {"group_id": group_id, "user_id": user_id}
        )
        result, code = vk_call(state, method, params)

        # Код 5 — «токен протух». Он может прийти и при живом по нашим часам
        # токене: VK отзывает ключ и по своим причинам. Одна попытка обновиться
        # и повтор, прежде чем считать это отказом.
        if code == 5:
            if refresh_tokens(state) is None:
                result, code = vk_call(state, method, params)

        if code is None:
            record = {
                "id": uuid.uuid4().hex[:12], "action": action, "user_id": str(user_id),
                "group_id": str(group_id), "created": int(time.time()), "via": "server",
            }
            archive(state, record, True, result.get("response"))
            save_state(state)
            return {"status": "ok", "via": "server", "response": result.get("response")}, 200

        if code in TOKEN_ERROR_CODES:
            # VK перестал принимать токен с сервера — больше не пробуем, уходим в очередь.
            state["server_side_ok"] = False
            task = enqueue(state, action, user_id, group_id,
                           "vk error %s" % code)
            save_state(state)
            return {"status": "queued", "task_id": task["id"],
                    "reason": result["error"].get("error_msg", "")}, 202

        save_state(state)
        return {"error": result["error"]}, 400


# --- Эндпоинты для бота ------------------------------------------------------

@app.route("/")
def index():
    return "VK moderator is running"


@app.route("/vk/remove-user", methods=["POST"])
def remove_user():
    if not may_moderate():
        return deny()
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    body, status = moderate("remove", user_id, data.get("group_id"))
    return jsonify(body), status


@app.route("/vk/ban-user", methods=["POST"])
def ban_user():
    if not may_moderate():
        return deny()
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    body, status = moderate("ban", user_id, data.get("group_id"))
    return jsonify(body), status


# --- Эндпоинты для мини-приложения -------------------------------------------

@app.route("/vk/exchange-code", methods=["POST"])
def exchange_code():
    """Меняет код авторизации VK ID на пару токенов.

    Открыт без секрета: страницу авторизации открывает человек в браузере,
    и заранее вписать туда секрет некуда. Подделать вызов нельзя — код
    одноразовый, живёт 10 минут и проверяется вместе с code_verifier и
    redirect_uri на стороне VK.
    """
    data = request.get_json(silent=True) or {}
    code = data.get("code")
    code_verifier = data.get("code_verifier")
    if not code or not code_verifier:
        return jsonify({"error": "code и code_verifier обязательны"}), 400
    if not CLIENT_ID or not REDIRECT_URI:
        return jsonify({"error": "на сервере не заданы VK_CLIENT_ID/VK_REDIRECT_URI"}), 500

    result, error = vk_id_post({
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "device_id": data.get("device_id", ""),
        "state": data.get("state", ""),
    })
    if error:
        return jsonify({"error": error}), 400

    with _lock:
        state = load_state()
        store_tokens(state, result)
        state["server_side_ok"] = probe_server_side(state)
        save_state(state)
        granted = state["scope"].split()
        return jsonify({
            "status": "ok",
            "user_id": state["user_id"],
            "scope": state["scope"],
            # Главный признак успеха: без groups модерация работать не будет.
            "has_groups": "groups" in granted,
            "has_refresh": bool(state["refresh_token"]),
            "expires_in": max(0, state["expires"] - int(time.time())) if state["expires"] else None,
            "server_side_ok": state["server_side_ok"],
            "queue": len(state["queue"]),
        })


@app.route("/vk/refresh", methods=["POST"])
def refresh_endpoint():
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    with _lock:
        state = load_state()
        error = refresh_tokens(state)
        if error:
            return jsonify({"error": error}), 400
        state["server_side_ok"] = probe_server_side(state)
        save_state(state)
        return jsonify({
            "status": "ok",
            "scope": state["scope"],
            "has_groups": "groups" in state["scope"].split(),
            "server_side_ok": state["server_side_ok"],
        })


@app.route("/vk/groups", methods=["GET"])
def admin_groups():
    """Сообщества, которыми управляет владелец токена."""
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    with _lock:
        state = load_state()
        if not state["access_token"]:
            return jsonify({"error": "нет токена"}), 409
        if not token_alive(state):
            refresh_tokens(state)
            save_state(state)
        result, code = vk_call(state, "groups.get",
                               {"filter": "admin", "extended": 1, "count": 100})
        if code is not None:
            return jsonify({"error": result["error"]}), 400
        items = result.get("response", {}).get("items", [])
        return jsonify({"groups": [
            {"id": g.get("id"), "name": g.get("name"), "screen_name": g.get("screen_name")}
            for g in items
        ]})


@app.route("/vk/token", methods=["POST"])
def set_token():
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    data = request.get_json(silent=True) or {}
    access_token = data.get("access_token")
    if not access_token:
        return jsonify({"error": "access_token is required"}), 400

    with _lock:
        state = load_state()
        state["access_token"] = access_token
        state["expires"] = int(data.get("expires") or 0)
        state["token_updated"] = int(time.time())
        if data.get("group_id"):
            state["group_id"] = str(data["group_id"])
        state["server_side_ok"] = probe_server_side(state)
        save_state(state)
        return jsonify({
            "status": "ok",
            "server_side_ok": state["server_side_ok"],
            "queue": len(state["queue"]),
        })


@app.route("/vk/queue", methods=["GET"])
def get_queue():
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    limit = min(int(request.args.get("limit", 20)), 100)

    with _lock:
        state = load_state()
        release_stale(state)
        taken = []
        for task in state["queue"]:
            if task["status"] != "pending":
                continue
            task["status"] = "in_progress"
            task["leased_at"] = int(time.time())
            taken.append(task)
            if len(taken) >= limit:
                break
        save_state(state)
        return jsonify({"tasks": taken})


@app.route("/vk/queue/list", methods=["GET"])
def list_queue():
    """Показать очередь, ничего не захватывая.

    Отличается от /vk/queue тем, что не помечает задания взятыми: список нужен
    человеку для глазами-и-руками, а не обработчику, и пометка «в работе»
    только мешала бы — задания молча возвращались бы в очередь по таймауту.
    """
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    limit = min(int(request.args.get("limit", 100)), 500)

    with _lock:
        state = load_state()
        release_stale(state)
        save_state(state)
        return jsonify({
            "tasks": state["queue"][:limit],
            "total": len(state["queue"]),
            "group_id": state["group_id"] or DEFAULT_GROUP_ID,
        })


@app.route("/vk/queue/ack", methods=["POST"])
def ack_task():
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    data = request.get_json(silent=True) or {}
    task_id = data.get("id")
    if not task_id:
        return jsonify({"error": "id is required"}), 400

    with _lock:
        state = load_state()
        for task in state["queue"]:
            if task["id"] == task_id:
                task["via"] = "client"
                archive(state, task, bool(data.get("ok")), data.get("error", ""))
                save_state(state)
                return jsonify({"status": "ok"})
        return jsonify({"error": "task not found"}), 404


@app.route("/vk/status", methods=["GET"])
def status():
    if not check_secret(ADMIN_SECRET, "X-Admin-Secret"):
        return deny()
    with _lock:
        state = load_state()
        return jsonify({
            "has_token": bool(state["access_token"]),
            "token_alive": token_alive(state),
            "expires_in": max(0, int(state["expires"] - time.time())) if state["expires"] else None,
            "scope": state["scope"],
            "has_groups": "groups" in state["scope"].split(),
            "has_refresh": bool(state["refresh_token"]),
            "user_id": state["user_id"],
            "server_side_ok": state["server_side_ok"],
            "group_id": state["group_id"] or DEFAULT_GROUP_ID,
            "queue_pending": sum(1 for t in state["queue"] if t["status"] == "pending"),
            "queue_in_progress": sum(1 for t in state["queue"] if t["status"] == "in_progress"),
            "history": state["history"][:20],
        })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
