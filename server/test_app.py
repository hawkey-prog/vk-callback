"""Smoke-test app.py: очередь, авторизация, CORS, fallback — без реальных вызовов VK."""
import json, os, sys, tempfile

work = tempfile.mkdtemp()
os.environ["STATE_FILE"] = os.path.join(work, "state.json")
os.environ["ADMIN_SECRET"] = "adm"
os.environ["BOT_SECRET"] = "bot"
os.environ["VK_GROUP_ID"] = "236838246"

sys.path.insert(0, r"D:\OpenClawData\workspace-coder\vk-callback\server")
import app as srv

# VK не дёргаем: подменяем транспорт.
CALLS = []
VK_MODE = {"server_ok": False, "fail_code": None}

def fake_vk_call(state, method, params):
    CALLS.append((method, dict(params)))
    if method == "groups.get":
        if VK_MODE["server_ok"]:
            return {"response": {"count": 1, "items": [236838246]}}, None
        return {"error": {"error_code": 15, "error_msg": "no access"}}, 15
    if VK_MODE["fail_code"]:
        c = VK_MODE["fail_code"]
        return {"error": {"error_code": c, "error_msg": "boom"}}, c
    return {"response": 1}, None

srv.vk_call = fake_vk_call
c = srv.app.test_client()

fails = []
def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + str(extra)) if not cond else ""))
    if not cond: fails.append(name)

def j(r): return json.loads(r.data)

print("\n1. Защита эндпоинтов")
check("бот без секрета отбит", c.post("/vk/remove-user", json={"user_id": 1}).status_code == 403)
check("админ без секрета отбит", c.get("/vk/queue").status_code == 403)
check("чужой секрет отбит",
      c.post("/vk/remove-user", json={"user_id": 1}, headers={"X-Bot-Secret": "x"}).status_code == 403)

BOT = {"X-Bot-Secret": "bot"}
ADM = {"X-Admin-Secret": "adm"}

print("\n2. Работа без токена")
r = c.post("/vk/remove-user", json={"user_id": 277162801}, headers=BOT)
check("без токена — 409", r.status_code == 409, j(r))

print("\n3. Токен принят, но VK не пускает сервер -> клиентский режим")
VK_MODE["server_ok"] = False
r = c.post("/vk/token", json={"access_token": "vk1.a.test", "expires": 0}, headers=ADM)
check("токен сохранён", r.status_code == 200 and j(r)["server_side_ok"] is False, j(r))

r = c.post("/vk/remove-user", json={"user_id": 277162801}, headers=BOT)
body = j(r)
check("задание ушло в очередь", r.status_code == 202 and body["status"] == "queued", body)
task_id = body.get("task_id")

r = c.get("/vk/queue?limit=10", headers=ADM)
tasks = j(r)["tasks"]
check("мини-приложение забрало задание", len(tasks) == 1 and tasks[0]["id"] == task_id, tasks)
check("group_id подставлен по умолчанию", tasks[0]["group_id"] == "236838246", tasks[0])
check("действие remove", tasks[0]["action"] == "remove", tasks[0])

check("повторный опрос не выдаёт то же дважды", j(c.get("/vk/queue", headers=ADM))["tasks"] == [])

r = c.post("/vk/queue/ack", json={"id": task_id, "ok": True}, headers=ADM)
check("подтверждение принято", r.status_code == 200, j(r))
st = j(c.get("/vk/status", headers=ADM))
check("очередь пуста", st["queue_pending"] == 0 and st["queue_in_progress"] == 0, st)
check("попало в историю", st["history"] and st["history"][0]["status"] == "done", st["history"][:1])

print("\n4. Брошенное задание возвращается в очередь")
c.post("/vk/remove-user", json={"user_id": 111}, headers=BOT)
c.get("/vk/queue", headers=ADM)
state = srv.load_state()
state["queue"][0]["leased_at"] -= (srv.LEASE_TIMEOUT + 10)
srv.save_state(state)
check("выдано снова", len(j(c.get("/vk/queue", headers=ADM))["tasks"]) == 1)
c.post("/vk/queue/ack", json={"id": srv.load_state()["history"][0]["id"] if False else
                              srv.load_state()["queue"][0]["id"], "ok": True}, headers=ADM)

print("\n5. VK пускает сервер -> прямой режим")
VK_MODE["server_ok"] = True
r = c.post("/vk/token", json={"access_token": "vk1.a.test", "expires": 0}, headers=ADM)
check("режим переключился", j(r)["server_side_ok"] is True, j(r))
CALLS.clear()
r = c.post("/vk/remove-user", json={"user_id": 277162801}, headers=BOT)
check("выполнено сразу", r.status_code == 200 and j(r)["via"] == "server", j(r))
check("вызван groups.removeUser", CALLS[0][0] == "groups.removeUser", CALLS)

CALLS.clear()
r = c.post("/vk/ban-user", json={"user_id": 277162801}, headers=BOT)
check("бан вызывает groups.ban с owner_id",
      CALLS[0][0] == "groups.ban" and "owner_id" in CALLS[0][1], CALLS)

print("\n6. VK отобрал доступ на ходу -> авто-откат в очередь")
VK_MODE["fail_code"] = 27
r = c.post("/vk/remove-user", json={"user_id": 999}, headers=BOT)
check("ушло в очередь", r.status_code == 202 and j(r)["status"] == "queued", j(r))
check("режим переключён на клиентский",
      j(c.get("/vk/status", headers=ADM))["server_side_ok"] is False)
VK_MODE["fail_code"] = None

print("\n7. Прочее")
check("протухший токен уводит в очередь",
      (c.post("/vk/token", json={"access_token": "t", "expires": 1}, headers=ADM),
       c.post("/vk/remove-user", json={"user_id": 5}, headers=BOT))[1].status_code == 202)
check("user_id обязателен",
      c.post("/vk/remove-user", json={}, headers=BOT).status_code == 400)
r = c.open("/vk/queue", method="OPTIONS")
check("preflight отвечает", r.status_code in (200, 204), r.status_code)
check("preflight на POST-эндпоинте отвечает",
      c.open("/vk/token", method="OPTIONS").status_code in (200, 204))
check("CORS-заголовок на месте",
      "hawkey-prog.github.io" in r.headers.get("Access-Control-Allow-Origin", ""), dict(r.headers))
check("X-Admin-Secret разрешён в CORS",
      "X-Admin-Secret" in r.headers.get("Access-Control-Allow-Headers", ""))

print("\n" + ("ВСЁ ЗЕЛЁНОЕ" if not fails else "ПРОВАЛЕНО: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
