"""Smoke-test app.py: очередь, авторизация, CORS, fallback — без реальных вызовов VK."""
import json, os, sys, tempfile, time

work = tempfile.mkdtemp()
os.environ["STATE_FILE"] = os.path.join(work, "state.json")
os.environ["ADMIN_SECRET"] = "adm"
os.environ["BOT_SECRET"] = "bot"
os.environ["VK_GROUP_ID"] = "236838246"
os.environ["VK_CLIENT_ID"] = "54634256"
os.environ["VK_REDIRECT_URI"] = "https://hawkey-prog.github.io/vk-callback/index.html"

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

print("\n6а. Просмотр очереди для ручной модерации")
# Чистим состояние и кладём два задания.
srv.save_state(dict(srv.EMPTY_STATE))
VK_MODE["server_ok"] = False
c.post("/vk/token", json={"access_token": "vk1.a.test", "expires": 0}, headers=ADM)
c.post("/vk/remove-user", json={"user_id": 1001}, headers=BOT)
c.post("/vk/ban-user", json={"user_id": 1002}, headers=BOT)

r = c.get("/vk/queue/list", headers=ADM)
lst = j(r)
check("список отдан", r.status_code == 200 and lst["total"] == 2, lst)
check("group_id отдан для ссылки", lst["group_id"] == "236838246", lst)
check("действия различимы",
      sorted(t["action"] for t in lst["tasks"]) == ["ban", "remove"], lst["tasks"])
check("просмотр не захватывает задания",
      all(t["status"] == "pending" for t in lst["tasks"]), lst["tasks"])
check("после просмотра обработчик всё ещё может их взять",
      len(j(c.get("/vk/queue", headers=ADM))["tasks"]) == 2)
check("список без секрета отбит", c.get("/vk/queue/list").status_code == 403)

# Отмечаем руками: одно сделано, одно пропущено.
ids = [t["id"] for t in lst["tasks"]]
c.post("/vk/queue/ack", json={"id": ids[0], "ok": True}, headers=ADM)
c.post("/vk/queue/ack", json={"id": ids[1], "ok": False, "error": "пропущено вручную"}, headers=ADM)
after = j(c.get("/vk/queue/list", headers=ADM))
check("очередь опустела", after["total"] == 0, after)
hist = j(c.get("/vk/status", headers=ADM))["history"]
check("оба попали в историю", len(hist) >= 2, hist)
check("статусы различаются",
      sorted(h["status"] for h in hist[:2]) == ["done", "failed"], hist[:2])

print("\n6б. VK ID: обмен кода, scope и обновление токена")
# Подменяем VK ID так же, как VK API: сеть в тестах не трогаем.
ID_CALLS = []
ID_MODE = {"scope": "groups vkid.personal_info", "fail": None}

def fake_vk_id_post(params):
    ID_CALLS.append(dict(params))
    if ID_MODE["fail"]:
        return None, ID_MODE["fail"]
    return {
        "access_token": "vk2.a." + params["grant_type"],
        "refresh_token": "rt_" + str(len(ID_CALLS)),
        "expires_in": 3600,
        "user_id": 146275235,
        "scope": ID_MODE["scope"],
    }, None

srv.vk_id_post = fake_vk_id_post
srv.save_state(dict(srv.EMPTY_STATE))
VK_MODE["server_ok"] = True
ID_CALLS.clear()

r = c.post("/vk/exchange-code", json={"code": "abc", "code_verifier": "ver", "device_id": "d1"})
body = j(r)
check("код обменян", r.status_code == 200 and body["status"] == "ok", body)
check("groups распознан в scope", body["has_groups"] is True, body)
check("refresh-токен сохранён", body["has_refresh"] is True, body)
check("обмен идёт как authorization_code",
      ID_CALLS[0]["grant_type"] == "authorization_code", ID_CALLS[0])
check("redirect_uri передан", "hawkey-prog" in ID_CALLS[0]["redirect_uri"], ID_CALLS[0])
check("code_verifier передан", ID_CALLS[0]["code_verifier"] == "ver", ID_CALLS[0])
check("обмен кода открыт без секрета — его делает браузер", r.status_code != 403)
check("без code_verifier отказ",
      c.post("/vk/exchange-code", json={"code": "abc"}).status_code == 400)

st = j(c.get("/vk/status", headers=ADM))
check("статус показывает scope", st["scope"] == "groups vkid.personal_info", st)
check("статус подтверждает groups", st["has_groups"] is True, st)

# device_id приходит только в адресе возврата; без него обновление токена
# через час уйдёт с пустым значением и провалится.
check("device_id из ответа VK сохранён", srv.load_state()["device_id"] == "d1",
      srv.load_state()["device_id"])
ID_CALLS.clear()
_st = srv.load_state(); _st["expires"] = int(time.time()) - 10; srv.save_state(_st)
c.post("/vk/remove-user", json={"user_id": 1}, headers=BOT)
_refresh = [x for x in ID_CALLS if x["grant_type"] == "refresh_token"]
check("device_id уходит при обновлении", _refresh and _refresh[0]["device_id"] == "d1", _refresh)

# Если VK выдал только личные данные — это обязано быть видно, а не «ок».
ID_MODE["scope"] = "vkid.personal_info"
r = c.post("/vk/exchange-code", json={"code": "abc2", "code_verifier": "ver"})
check("отсутствие groups видно сразу", j(r)["has_groups"] is False, j(r))
ID_MODE["scope"] = "groups vkid.personal_info"

print("\n6в. Протухший токен обновляется сам")
srv.save_state(dict(srv.EMPTY_STATE))
c.post("/vk/exchange-code", json={"code": "abc", "code_verifier": "ver"})
state = srv.load_state()
state["expires"] = int(time.time()) - 10        # как будто час прошёл
srv.save_state(state)
ID_CALLS.clear()
r = c.post("/vk/remove-user", json={"user_id": 277162801}, headers=BOT)
check("задание выполнено, а не отложено", r.status_code == 200 and j(r)["via"] == "server", j(r))
check("сервер сходил за обновлением",
      any(x["grant_type"] == "refresh_token" for x in ID_CALLS), ID_CALLS)
check("новый токен сохранён", srv.load_state()["access_token"].endswith("refresh_token"))

print("\n6г. VK отозвал токен на ходу (код 5)")
srv.save_state(dict(srv.EMPTY_STATE))
c.post("/vk/exchange-code", json={"code": "abc", "code_verifier": "ver"})
ID_CALLS.clear()
CALLS.clear()
attempts = {"n": 0}
def once_expired(state, method, params):
    if method == "groups.get":
        return {"response": {"count": 1, "items": [1]}}, None
    attempts["n"] += 1
    if attempts["n"] == 1:
        return {"error": {"error_code": 5, "error_msg": "token expired"}}, 5
    return {"response": 1}, None
srv.vk_call = once_expired
r = c.post("/vk/remove-user", json={"user_id": 5}, headers=BOT)
check("после обновления повтор удался", r.status_code == 200 and j(r)["status"] == "ok", j(r))
check("обновление действительно запрашивалось",
      any(x["grant_type"] == "refresh_token" for x in ID_CALLS), ID_CALLS)
srv.vk_call = fake_vk_call

print("\n6д. Обновление не удалось — задание не теряется")
srv.save_state(dict(srv.EMPTY_STATE))
c.post("/vk/exchange-code", json={"code": "abc", "code_verifier": "ver"})
state = srv.load_state(); state["expires"] = int(time.time()) - 10; srv.save_state(state)
ID_MODE["fail"] = "invalid_grant"
r = c.post("/vk/remove-user", json={"user_id": 7}, headers=BOT)
check("ушло в очередь, а не в ошибку", r.status_code == 202 and j(r)["status"] == "queued", j(r))
check("причина названа", "invalid_grant" in j(r)["reason"], j(r))
ID_MODE["fail"] = None

print("\n6е. Список сообществ и доступ админской страницы")
srv.save_state(dict(srv.EMPTY_STATE))
c.post("/vk/exchange-code", json={"code": "abc", "code_verifier": "ver"})
def groups_reply(state, method, params):
    if method == "groups.get":
        return {"response": {"count": 2, "items": [
            {"id": 239099649, "name": "Курс", "screen_name": "adult_course"},
            {"id": 237521740, "name": "Интуиция", "screen_name": "intuition_sixthsense"}]}}, None
    return {"response": 1}, None
srv.vk_call = groups_reply
r = c.get("/vk/groups", headers=ADM)
check("сообщества отданы", r.status_code == 200 and len(j(r)["groups"]) == 2, j(r))
check("имя и id на месте", j(r)["groups"][0]["name"] == "Курс", j(r)["groups"][0])
check("без секрета список закрыт", c.get("/vk/groups").status_code == 403)
srv.vk_call = fake_vk_call

check("админским секретом модерация тоже доступна",
      c.post("/vk/remove-user", json={"user_id": 1}, headers=ADM).status_code in (200, 202))
check("чужой секрет по-прежнему отбит",
      c.post("/vk/remove-user", json={"user_id": 1},
             headers={"X-Admin-Secret": "нет"}).status_code == 403)

print("\n7. Прочее")
# Протухший токен сам по себе не повод откладывать задание — сервер его
# обновит (см. 6в). В очередь уходим, только когда обновлять нечем.
_s = srv.load_state()
_s["expires"] = int(time.time()) - 10
_s["refresh_token"] = ""
srv.save_state(_s)
check("просроченный токен без refresh уводит в очередь",
      c.post("/vk/remove-user", json={"user_id": 5}, headers=BOT).status_code == 202)
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
