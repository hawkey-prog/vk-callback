import os
import json
import secrets
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

CLIENT_ID = os.getenv("VK_CLIENT_ID")
CLIENT_SECRET = os.getenv("VK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("VK_REDIRECT_URI")
TOKEN_URL = os.getenv("VK_TOKEN_URL", "https://id.vk.ru/oauth2/auth")
API_VERSION = os.getenv("VK_API_VERSION", "5.199")
SERVICE_TOKEN = os.getenv("VK_SERVICE_TOKEN", "")

TOKENS_FILE = os.getenv("TOKENS_FILE", "/opt/vk-bot/tokens.json")


def load_tokens():
    try:
        with open(TOKENS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_tokens(data):
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def generate_state(length=43):
    """Генерация случайной строки для state (не менее 32 символов)."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(chars) for _ in range(length))


def call_vk_api(method, params, refreshed=False):
    tokens = load_tokens()
    access_token = tokens.get("access_token")

    if not access_token:
        return {"error": "no access_token"}, 500

    params["access_token"] = access_token
    params["v"] = API_VERSION

    try:
        response = requests.post(
            f"https://api.vk.com/method/{method}",
            data=params,
            timeout=10
        )
    except Exception as e:
        return {"error": "request failed", "details": str(e)}, 502

    result = response.json()

    # error_code 5 — токен протух
    if "error" in result and result["error"].get("error_code") == 5:
        if refreshed:
            return {"error": "token refresh failed", "vk_error": result}, 401

        refresh_result = do_refresh_token()
        if "error" in refresh_result:
            return {"error": "failed to refresh token", "details": refresh_result}, 401

        return call_vk_api(method, params, refreshed=True)

    return result, 200


@app.route("/")
def index():
    return "VK Bot Server is running"


@app.route("/vk/exchange-code", methods=["POST"])
def exchange_code():
    data = request.get_json() or {}

    code = data.get("code")
    state = data.get("state")
    code_verifier = data.get("code_verifier")
    device_id = data.get("device_id")

    if not code:
        return {"error": "code is required"}, 400
    if not code_verifier:
        return {"error": "code_verifier is required"}, 400

    with open("/tmp/vk_debug.log", "a") as _log:
        _log.write(f"=== EXCHANGE REQUEST ===\n")
        _log.write(f"TOKEN_URL: {TOKEN_URL}\n")
        _log.write(f"CLIENT_ID: {CLIENT_ID}\n")
        _log.write(f"REDIRECT_URI: {REDIRECT_URI}\n")
        _log.write(f"CODE: {code[:30]}...\n")
        _log.write(f"STATE: {state}\n")
        _log.write(f"DEVICE_ID: {device_id}\n")
        _log.write(f"========================\n")

    # Формируем параметры для обмена кода на токен
    token_params = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "device_id": device_id,
        "state": state,
    }

    # Для конфиденциального приложения — service_token обязателен
    if SERVICE_TOKEN:
        token_params["service_token"] = SERVICE_TOKEN

    try:
        response = requests.post(
            TOKEN_URL,
            data=token_params,
            timeout=30
        )
    except Exception as e:
        with open("/tmp/vk_debug.log", "a") as _log:
            _log.write(f"REQUEST EXCEPTION: {e}\n")
        return {"error": "request failed", "details": str(e)}, 502

    with open("/tmp/vk_debug.log", "a") as _log:
        _log.write(f"VK STATUS: {response.status_code}\n")
        _log.write(f"VK RAW: {response.text[:1000]}\n")

    try:
        result = response.json()
    except Exception as e:
        with open("/tmp/vk_debug.log", "a") as _log:
            _log.write(f"JSON PARSE ERROR: {e}\n")
        return {"error": "invalid response", "raw": response.text[:500]}, 502

    with open("/tmp/vk_debug.log", "a") as _log:
        _log.write(f"VK RESPONSE: {result}\n")

    if "error" in result:
        return {"error": result}, 400

    save_tokens(result)

    return {
        "status": "ok",
        "user_id": result.get("user_id"),
        "access_token_prefix": result.get("access_token", "")[:20],
        "has_refresh_token": bool(result.get("refresh_token")),
        "scope": result.get("scope", ""),
    }


@app.route("/vk/refresh", methods=["POST"])
def do_refresh_token():
    tokens = load_tokens()
    refresh_token = tokens.get("refresh_token")
    device_id = tokens.get("device_id", "")

    if not refresh_token:
        return {"error": "no refresh_token"}, 400

    state = generate_state()

    refresh_params = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "device_id": device_id,
        "state": state,
    }

    # Для конфиденциального приложения — service_token обязателен
    if SERVICE_TOKEN:
        refresh_params["service_token"] = SERVICE_TOKEN

    response = requests.post(
        TOKEN_URL,
        data=refresh_params,
        timeout=30
    )
    result = response.json()

    if "error" in result:
        return {"error": result}, 400

    save_tokens(result)
    return {"status": "ok", "user_id": result.get("user_id")}


@app.route("/vk/remove-user", methods=["POST"])
def remove_user():
    data = request.get_json() or {}
    user_id = data.get("user_id")
    group_id = data.get("group_id")

    if not user_id:
        return {"error": "user_id is required"}, 400

    if not group_id:
        return {"error": "group_id is required"}, 400

    result, status = call_vk_api("groups.removeUser", {
        "group_id": group_id,
        "user_id": user_id,
    })

    return jsonify(result), status


@app.route("/vk/ban-user", methods=["POST"])
def ban_user():
    data = request.get_json() or {}
    user_id = data.get("user_id")
    group_id = data.get("group_id")
    comment = data.get("comment", "")

    if not user_id:
        return {"error": "user_id is required"}, 400

    if not group_id:
        return {"error": "group_id is required"}, 400

    result, status = call_vk_api("groups.banUser", {
        "group_id": group_id,
        "user_id": user_id,
        "comment": comment,
        "comment_visible": 0,
    })

    return jsonify(result), status


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)