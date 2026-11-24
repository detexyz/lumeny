import asyncio, hashlib, hmac, json, math, os, random, re, sqlite3, time
from urllib.parse import parse_qsl
from aiohttp import web, ClientSession

# Переменные окружения те же, что и раньше: BOT_TOKEN, BOT_USERNAME, PUBLIC_URL, APP_SHORT_NAME, ADMIN_ID, DATABASE_URL
TOKEN = os.environ.get("BOT_TOKEN", "")
BOT = os.environ.get("BOT_USERNAME", "")
BASE = os.environ.get("PUBLIC_URL", "").rstrip("/")
APP_NAME = os.environ.get("APP_SHORT_NAME", "")
ADMIN = os.environ.get("ADMIN_ID", "")
DEV = os.environ.get("DEV") == "1"
DB = os.environ.get("DB_PATH", "db.sqlite")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
START_MONEY = 10000
rng = random.SystemRandom()

# ---------- база (SQLite или бесплатный Postgres) ----------
if DATABASE_URL:
    import psycopg2, psycopg2.extras
    pg = None

    def ex(sql, a=()):
        global pg
        sql = sql.replace("?", "%s")
        if sql.startswith("INSERT OR IGNORE"):
            sql = sql.replace("INSERT OR IGNORE", "INSERT") + " ON CONFLICT DO NOTHING"
        for attempt in (1, 2):
            try:
                if pg is None or pg.closed:
                    pg = psycopg2.connect(DATABASE_URL)
                    pg.autocommit = True
                cur = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(sql, a)
                return cur
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                pg = None
                if attempt == 2:
                    raise
else:
    lite = sqlite3.connect(DB, check_same_thread=False)
    lite.row_factory = sqlite3.Row

    def ex(sql, a=()):
        c = lite.execute(sql, a)
        lite.commit()
        return c
ex("CREATE TABLE IF NOT EXISTS sim(id TEXT PRIMARY KEY, name TEXT, data TEXT, started INT DEFAULT 0)")

# ---------- каталог ----------
SLOT_RU = {"cpu": "процессор", "gpu": "видеокарта", "ram": "память", "cam": "вебкамера", "mic": "микрофон"}
PARTS = {i: dict(id=i, name=n, price=p, slot=s, power=w) for i, n, p, s, w in [
    ("cpu1", "Intel Pentium G", 2500, "cpu", 14), ("cpu2", "Ryzen 5 5600", 9000, "cpu", 32),
    ("cpu3", "Ryzen 7 7800X3D", 22000, "cpu", 58), ("cpu4", "Core i9-14900K", 60000, "cpu", 90),
    ("gpu1", "GeForce GT 1030", 3000, "gpu", 12), ("gpu2", "RTX 3060", 20000, "gpu", 36),
    ("gpu3", "RTX 4070", 55000, "gpu", 66), ("gpu4", "RTX 4090", 160000, "gpu", 100),
    ("ram1", "8 ГБ ОЗУ", 1500, "ram", 12), ("ram2", "16 ГБ ОЗУ", 4000, "ram", 28),
    ("ram3", "32 ГБ ОЗУ", 9000, "ram", 48), ("ram4", "64 ГБ ОЗУ", 20000, "ram", 70),
    ("cam1", "Вебкамера 720p", 1000, "cam", 1), ("cam2", "Вебкамера Full HD", 5000, "cam", 2), ("cam3", "Вебкамера 4K", 15000, "cam", 3),
    ("mic1", "Микрофон из наушников", 800, "mic", 1), ("mic2", "USB-микрофон", 4000, "mic", 2), ("mic3", "Студийный микрофон", 14000, "mic", 3)]}
FURN = {i: dict(id=i, name=n, price=p, pts=w) for i, n, p, w in [
    ("desk", "Игровой стол", 2500, 5), ("chair", "Геймерское кресло", 3500, 8), ("lamp", "Настольная лампа", 1200, 4),
    ("poster", "Постер", 900, 5), ("plant", "Растение", 700, 3), ("rug", "Ковёр", 2500, 6), ("rgb", "RGB-подсветка", 4500, 10),
    ("shelf", "Полка с коллекцией", 3500, 7), ("sofa", "Диван", 9000, 12), ("neon", "Неоновая вывеска", 7000, 14), ("curtains", "Шторы", 2000, 4)]}
CLOTH = {i: dict(id=i, name=n, price=p, slot=s, bonus=b) for i, n, p, s, b in [
    ("cap", "Кепка", 800, "hat", 1), ("beanie", "Шапка", 1200, "hat", 1), ("hp", "Игровые наушники", 3500, "ears", 3),
    ("gl1", "Круглые очки", 1500, "glasses", 1), ("gl2", "Тёмные очки", 6000, "glasses", 2),
    ("tee", "Футболка", 600, "top", 0), ("hood", "Худи", 2500, "top", 2), ("jersey", "Джерси", 4000, "top", 3), ("jacket", "Куртка", 7000, "top", 4)]}
# Скины только для админа. Чтобы добавить свой, допишите id и название здесь и вид в index.html (словарь SK).
SKINS = {"s_legend": "Легенда эфира", "s_tilt": "Король тильта", "s_gold": "Золотой донатер", "s_ghost": "Призрак чата"}
GAMES = {i: dict(id=i, name=n, req=r, base=b) for i, n, r, b in [
    ("chat", "Просто общение", 5, 150000), ("dota2", "Dota 2", 22, 100000), ("valorant", "Valorant", 26, 70000),
    ("mc", "Minecraft", 18, 55000), ("cs2", "Counter-Strike 2", 32, 30000), ("gta", "GTA V", 45, 90000), ("cyber", "Cyberpunk 2077", 70, 12000)]}
DONORS = ["Лёха_228", "Мария", "DarkKnight", "Тимур", "Саша", "xX_Pro_Xx", "Катя", "Влад", "StreamFan", "Диана", "Никита", "Артём", "Зевс", "Лиза_play"]
MSGS = ["Лучший стример!", "Держи на пиво", "Привет с Урала", "Играй лучше)", "ГЛХФ", "Красивая комната!", "Первый раз тут, остаюсь",
        "Сыграй в другую игру", "Топ контент", "На новый комп", "Легенда", "Заказываю трек"]
SLOTS = ("hat", "ears", "glasses", "top")

# ---------- онлайн игр на «Твиче» (общий для всех, меняется со временем) ----------
PH = {g: (rng.uniform(0, 6.28), rng.uniform(1200, 2600)) for g in GAMES}
spikes, over = {}, {}


def online(g):
    now = time.time()
    if g in over and over[g][0] > now:
        return over[g][1]
    ph, per = PH[g]
    v = GAMES[g]["base"] * (1 + .4 * math.sin(now / per + ph) + .08 * math.sin(now / 37 + ph * 3))
    if g in spikes and spikes[g][0] > now:
        v *= spikes[g][1]
    return int(v)


games_view = lambda: [{"id": g, "online": online(g)} for g in GAMES]

# ---------- игроки ----------
players, streams, socks = {}, {}, {}
new_state = lambda: {"money": START_MONEY, "parts": [], "furn": [], "clothes": [], "wear": {}, "skin": None, "fol": 0.0, "earned": 0, "log": []}


def load(uid, name):
    r = ex("SELECT data FROM sim WHERE id=?", (uid,)).fetchone()
    if r:
        d = json.loads(r["data"])
        ex("UPDATE sim SET name=? WHERE id=?", (name, uid))
    else:
        d = new_state()
        ex("INSERT INTO sim(id,name,data) VALUES(?,?,?)", (uid, name, json.dumps(d)))
    for k, v in new_state().items():
        d.setdefault(k, v)
    players[uid] = d
    return d


def get_d(uid):
    if uid in players:
        return players[uid]
    r = ex("SELECT name FROM sim WHERE id=?", (uid,)).fetchone()
    return load(uid, r["name"]) if r else None


def save(uid):
    ex("UPDATE sim SET data=? WHERE id=?", (json.dumps(players[uid]), uid))


def best(d):
    b = {}
    for i in d["parts"]:
        p = PARTS[i]
        if p["slot"] not in b or p["power"] > b[p["slot"]]["power"]:
            b[p["slot"]] = p
    return b


def pc(d):
    b = best(d)
    miss = [s for s in ("cpu", "gpu", "ram") if s not in b]
    return (0 if miss else .35 * b["cpu"]["power"] + .45 * b["gpu"]["power"] + .2 * b["ram"]["power"]), miss, b


decor = lambda d: min(100, sum(FURN[i]["pts"] for i in d["furn"]))
charisma = lambda d: min(15, sum(CLOTH[i]["bonus"] for i in d["wear"].values() if i in CLOTH)) / 100


def view(d):
    sc, miss, b = pc(d)
    return {"money": d["money"], "parts": d["parts"], "furn": d["furn"], "clothes": d["clothes"], "wear": d["wear"], "skin": d["skin"],
            "fol": int(d["fol"]), "earned": d["earned"], "score": round(sc, 1), "miss": miss, "decor": decor(d), "log": d["log"][-20:]}


async def send_to(uid, m):
    data = json.dumps(m)
    for ws, u in list(socks.items()):
        if u == uid:
            try:
                await ws.send_str(data)
            except Exception:
                pass


async def push(uid):
    await send_to(uid, {"t": "me", "me": view(players[uid])})


def poisson(l):
    L, k, p = math.exp(-l), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def do_tick(d, g):
    G = GAMES[g]
    sc, _, b = pc(d)
    lag = max(.15, min(1, sc / G["req"]) ** 1.5)
    pop = max(.3, min(3, online(g) / 60000))                 # популярность игры сейчас
    look, cha = 1 + decor(d) / 80, 1 + charisma(d)           # красота комнаты и образ
    qual = .7 + .1 * (b.get("cam", {}).get("power", 0) + b.get("mic", {}).get("power", 0))
    viewers = max(1, int((3 + d["fol"] * .08) * pop * lag * qual * look * cha * rng.uniform(.85, 1.15)))
    evs = []
    for _ in range(poisson(min(2.5, .25 + viewers / 140))):
        a = max(10, int(rng.uniform(20, 160) * (.6 + .4 * min(pop, 2.5)) * look * cha * lag / 10) * 10)
        evs.append({"n": rng.choice(DONORS), "a": a, "m": rng.choice(MSGS), "g": G["name"]})
    gain = sum(e["a"] for e in evs)
    d["money"] += gain
    d["earned"] += gain
    d["fol"] += viewers * .02 * lag
    d["log"] = (d["log"] + evs)[-40:]
    return {"viewers": viewers, "evs": evs, "online": online(g), "perf": int(lag * 100)}


def verify(init):
    d = dict(parse_qsl(init))
    h = d.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h)
    if not ok or time.time() - int(d.get("auth_date", 0)) > 86400:
        return None
    return json.loads(d["user"])


async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    q = req.query
    u = {"id": q.get("id", "1"), "first_name": q.get("name", "Dev")} if DEV else verify(q.get("initData", ""))
    if not u:
        await ws.close()
        return ws
    uid, name = str(u["id"]), u.get("first_name", "Игрок")[:20]
    d, admin = load(uid, name), bool(ADMIN) and str(u["id"]) == ADMIN
    socks[ws] = uid
    send = lambda m: ws.send_str(json.dumps(m))
    err = lambda t: send({"t": "err", "msg": t})
    cat = {"parts": list(PARTS.values()), "furn": list(FURN.values()), "clothes": list(CLOTH.values()),
           "skins": [{"id": k, "name": v} for k, v in SKINS.items()] if admin else [], "games": list(GAMES.values())}
    await send({"t": "init", "cat": cat, "admin": admin, "games": games_view(), "me": view(d)})
    try:
        async for msg in ws:
            try:
                m = json.loads(msg.data)
            except Exception:
                continue
            t = m.get("t")
            if t == "buy":
                kind, i = m.get("kind"), m.get("id")
                tbl = {"parts": PARTS, "furn": FURN, "clothes": CLOTH}.get(kind)
                if not tbl or i not in tbl:
                    await err("Такого товара нет")
                elif i in d[kind]:
                    await err("Уже куплено")
                elif d["money"] < tbl[i]["price"]:
                    await err("Не хватает денег")
                else:
                    d["money"] -= tbl[i]["price"]
                    d[kind].append(i)
                    save(uid)
                    await push(uid)
            elif t == "wear":
                i, slot = m.get("id"), m.get("slot")
                if i is None and slot in SLOTS:
                    d["wear"].pop(slot, None)
                elif i in CLOTH and i in d["clothes"]:
                    d["wear"][CLOTH[i]["slot"]] = i
                save(uid)
                await push(uid)
            elif t == "skin" and admin:
                d["skin"] = m.get("id") if m.get("id") in SKINS else None
                save(uid)
                await push(uid)
            elif t == "stream_start":
                g, (sc, miss, _) = m.get("game"), pc(d)
                if g not in GAMES:
                    await err("Такой игры нет")
                elif miss:
                    await err("ПК не собран. Не хватает: " + ", ".join(SLOT_RU[s] for s in miss))
                else:
                    streams[uid] = {"g": g, "last": time.time(), "n": 0}
                    await send({"t": "stream", "on": True, "game": g})
            elif t == "stream_tick":
                s = streams.get(uid)
                if s and time.time() - s["last"] >= 4:
                    s["last"], s["n"] = time.time(), s["n"] + 1
                    res = do_tick(d, s["g"])
                    if s["n"] % 6 == 0:
                        save(uid)
                    await send({"t": "tick", **res, "money": d["money"], "fol": int(d["fol"]), "earned": d["earned"]})
            elif t == "stream_stop":
                streams.pop(uid, None)
                save(uid)
                await send({"t": "stream", "on": False})
                await push(uid)
    finally:
        socks.pop(ws, None)
        if uid not in socks.values():
            streams.pop(uid, None)
        save(uid)
    return ws


# ---------- бот ----------
async def tg(method, **kw):
    try:
        async with http.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=kw) as r:
            data = await r.json()
            if not data.get("ok"):
                print("TELEGRAM ERROR", method, data, flush=True)
            return data
    except Exception as e:
        print("TELEGRAM REQUEST FAILED", method, repr(e), flush=True)
        return {}


async def open_msg(chat):
    link = f"https://t.me/{BOT + '/' + APP_NAME if APP_NAME else BOT}"
    body = {"chat_id": chat, "parse_mode": "HTML", "text": f'Симулятор стримера: собери ПК, обставь комнату и зарабатывай на донатах.\n\n<a href="{link}">Открыть игру</a>'}
    if BASE.startswith("https://"):
        body["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть игру", "web_app": {"url": BASE + "/"}}]]}
    r = await tg("sendMessage", **body)
    if not r.get("ok"):
        await tg("sendMessage", chat_id=chat, text="Игра пока не настроена. Проверьте PUBLIC_URL и BOT_USERNAME.")


async def admin_cmd(chat, text):
    p = text.split(maxsplit=2)
    c = p[0].split("@")[0]
    reply = lambda t: tg("sendMessage", chat_id=chat, text=t)
    if c == "/give" and len(p) == 3 and p[2].lstrip("-").isdigit() and get_d(p[1]):
        d = players[p[1]]
        d["money"] += int(p[2])
        save(p[1])
        await push(p[1])
        await reply(f"Готово: баланс {d['money']} ₽")
    elif c == "/reset" and len(p) >= 2 and get_d(p[1]):
        players[p[1]] = new_state()
        save(p[1])
        await push(p[1])
        await reply("Прогресс игрока сброшен")
    elif c == "/online" and len(p) == 3 and p[1] in GAMES and p[2].isdigit():
        over[p[1]] = (time.time() + 600, int(p[2]))
        await reply(f"{GAMES[p[1]]['name']}: онлайн {p[2]} на 10 минут")
    elif c == "/stats":
        n, tot = [list(dict(ex("SELECT COUNT(*) AS a, COUNT(CASE WHEN started=1 THEN 1 END) AS b FROM sim").fetchone()).values())][0]
        await reply(f"Игроков: {n}\nНажали Start: {tot}\nСейчас онлайн: {len(set(socks.values()))}\nСтримят: {len(streams)}")
    elif c == "/players":
        rows = ex("SELECT id,name FROM sim LIMIT 40").fetchall()
        await reply("\n".join(f"{r['id']} {r['name']}" for r in rows) or "Игроков пока нет")
    elif c == "/broadcast" and len(text.split(maxsplit=1)) == 2:
        body, k = text.split(maxsplit=1)[1], 0
        for r in ex("SELECT id FROM sim WHERE started=1").fetchall():
            k += bool((await tg("sendMessage", chat_id=r["id"], text=body)).get("ok"))
            await asyncio.sleep(.05)
        await reply(f"Отправлено: {k}")
    else:
        await reply("/give ID сумма\n/reset ID (сброс прогресса)\n/online игра число (dota2, cs2, valorant, mc, gta, cyber, chat)\n/stats\n/players\n/broadcast текст")


async def handle_update(up):
    m = up.get("message") or {}
    text, uid = m.get("text") or "", str(m.get("from", {}).get("id", ""))
    if not text.startswith("/") or not uid:
        return
    cmd = text.split()[0].split("@")[0]
    if cmd == "/start":
        ex("INSERT OR IGNORE INTO sim(id,name,data) VALUES(?,?,?)", (uid, m["from"].get("first_name", "Игрок")[:20], json.dumps(new_state())))
        ex("UPDATE sim SET started=1 WHERE id=?", (uid,))
        await open_msg(m["chat"]["id"])
    elif ADMIN and uid == ADMIN:
        await admin_cmd(m["chat"]["id"], text)


async def bot_loop():
    off = 0
    await tg("deleteWebhook")
    while True:
        r = await tg("getUpdates", offset=off, timeout=30)
        if not r.get("ok"):
            await asyncio.sleep(3)
            continue
        for up in r.get("result", []):
            off = up["update_id"] + 1
            try:
                await handle_update(up)
            except Exception as e:
                print("HANDLER ERROR", repr(e), flush=True)


SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()[:32]


async def tg_hook(req):
    if req.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET:
        return web.Response(status=403)
    try:
        up = await req.json()
    except Exception:
        return web.Response(text="bad")

    async def go():
        try:
            await handle_update(up)
        except Exception as e:
            print("HANDLER ERROR", repr(e), flush=True)
    asyncio.create_task(go())
    return web.Response(text="ok")


async def ticker():                    # раз в 10 секунд рассылаем онлайн игр; иногда случается всплеск или спад интереса
    while True:
        await asyncio.sleep(10)
        if rng.random() < .08:
            spikes[rng.choice(list(GAMES))] = (time.time() + 150, rng.choice([.5, 1.7, 2.1]))
        data = json.dumps({"t": "games", "games": games_view()})
        for ws in list(socks):
            try:
                await ws.send_str(data)
            except Exception:
                pass


async def index(req):
    return web.FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))


async def health(req):
    return web.Response(text="ok")


async def on_start(app):
    global http
    http = ClientSession()
    app["tasks"] = [asyncio.create_task(ticker())]
    if not TOKEN:
        return
    print("BOT:", (await tg("getMe")).get("result", {}).get("username"), flush=True)
    if BASE.startswith("https://"):
        print("WEBHOOK:", await tg("setWebhook", url=BASE + "/tg", secret_token=SECRET, allowed_updates=["message"]), flush=True)
    else:
        app["tasks"].append(asyncio.create_task(bot_loop()))


async def on_stop(app):
    await http.close()


app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/ws", ws_handler)
app.router.add_post("/tg", tg_hook)
app.router.add_get("/health", health)
app.on_startup.append(on_start)
app.on_cleanup.append(on_stop)
if __name__ == "__main__":
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
