"""
Сервер модерации VK-сообщества.

Принимает задания от бота (BotHelp) и исполняет их одним из двух способов:

  1. напрямую — если VK принимает токен, присланный мини-приложением, с сервера;
  2. через очередь — мини-приложение, открытое во вкладке VK, забирает задания
     и вызывает методы у себя, где токен точно рабочий.

Способ выбирается автоматически при сохранении токена и перепроверяется при
каждой ошибке доступа, так что переключение не требует вмешательства.
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


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Secret"
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
            return {"error": "no token: откройте мини-приложение и отправьте токен"}, 409

        # Токена нет или он протух — сразу в очередь, мини-приложение разберёт.
        if not token_alive(state):
            task = enqueue(state, action, user_id, group_id, "token expired")
            save_state(state)
            return {"status": "queued", "task_id": task["id"], "reason": "token expired"}, 202

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
    if not check_secret(BOT_SECRET, "X-Bot-Secret"):
        return deny()
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    body, status = moderate("remove", user_id, data.get("group_id"))
    return jsonify(body), status


@app.route("/vk/ban-user", methods=["POST"])
def ban_user():
    if not check_secret(BOT_SECRET, "X-Bot-Secret"):
        return deny()
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    body, status = moderate("ban", user_id, data.get("group_id"))
    return jsonify(body), status


# --- Эндпоинты для мини-приложения -------------------------------------------

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
            "server_side_ok": state["server_side_ok"],
            "group_id": state["group_id"] or DEFAULT_GROUP_ID,
            "queue_pending": sum(1 for t in state["queue"] if t["status"] == "pending"),
            "queue_in_progress": sum(1 for t in state["queue"] if t["status"] == "in_progress"),
            "history": state["history"][:20],
        })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
