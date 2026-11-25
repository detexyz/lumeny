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
SLOT_RU = {"cpu": "процессор", "gpu": "видеокарта", "ram": "память", "mon": "монитор", "cam": "вебкамера", "mic": "микрофон",
           "kb": "клавиатура", "ms": "мышь", "cap": "карта захвата", "ring": "свет"}
REQ_SLOTS = ("cpu", "gpu", "ram", "mon")
PARTS = {}
for slot, rows in {
    "cpu": [("Intel Pentium G", 2500, 14), ("Core i3-12100", 6000, 24), ("Ryzen 5 5600", 11000, 34), ("Core i5-13600K", 22000, 46), ("Ryzen 7 7800X3D", 45000, 60), ("Core i9-14900K", 90000, 76), ("Threadripper 7980X", 220000, 92)],
    "gpu": [("GeForce GT 1030", 3000, 12), ("GTX 1650", 8000, 22), ("RTX 3060", 20000, 36), ("RTX 4070", 55000, 52), ("RTX 4080", 110000, 68), ("RTX 4090", 220000, 84), ("RTX 5090 Ti", 600000, 100)],
    "ram": [("8 ГБ DDR4", 1500, 12), ("16 ГБ DDR4", 4000, 28), ("32 ГБ DDR5", 9000, 46), ("64 ГБ DDR5", 20000, 64), ("128 ГБ DDR5", 60000, 82)],
    "mon": [("Монитор 19″ 60 Гц", 1200, 1), ("24″ 144 Гц", 7000, 2), ("27″ 2K 165 Гц", 18000, 3), ("32″ 4K", 45000, 4), ("Ультравайд 34″", 85000, 5), ("Тройной сетап 27″", 200000, 6)],
    "cam": [("Вебкамера 720p", 800, 1), ("Вебкамера Full HD", 5000, 2), ("Вебкамера 4K", 15000, 3), ("Кинокамера", 120000, 4)],
    "mic": [("Микрофон из наушников", 600, 1), ("USB-микрофон", 4000, 2), ("Студийный микрофон", 14000, 3), ("Бродкастер на пантографе", 70000, 4)],
    "kb": [("Клавиатура мембранная", 600, 1), ("Механическая клавиатура", 4000, 2), ("RGB-механика", 12000, 3), ("Кастомная клавиатура", 40000, 4)],
    "ms": [("Офисная мышь", 400, 1), ("Игровая мышь", 3000, 2), ("Беспроводная про-мышь", 9000, 3), ("Мышь-легенда", 25000, 4)],
    "cap": [("Карта захвата", 6000, 1), ("Карта захвата 4K", 25000, 2), ("Студийный рекордер", 90000, 3)],
    "ring": [("Кольцевая лампа", 2500, 1), ("Свет Softbox", 12000, 2), ("Студийный свет", 50000, 3)]}.items():
    for n, (name, price, power) in enumerate(rows, 1):
        PARTS[f"{slot}{n}"] = dict(id=f"{slot}{n}", name=name, price=price, slot=slot, power=power, lvl=n)
# мебель и декор: id, название, цена, красота, тип (f - пол, w - стена), размеры (по полу: вдоль «лица» и вдоль стены; на стене: ширина и высота)
FURN = {i: dict(id=i, name=n, price=p, pts=w, kind=k, a=a, b=b) for i, n, p, w, k, a, b in [
    ("desk1", "Простой стол", 3000, 4, "f", .8, 1.6), ("desk2", "Игровой стол", 14000, 8, "f", .9, 2.2), ("desk3", "Угловой L-стол", 60000, 14, "f", 2.4, 2.6), ("desk4", "Стол стримера", 250000, 22, "f", 1.0, 3.0),
    ("chair1", "Офисное кресло", 4000, 4, "f", .7, .7), ("chair2", "Геймерское кресло", 18000, 8, "f", .8, .8), ("chair3", "Про-кресло", 80000, 14, "f", .9, .9), ("chair4", "Трон стримера", 400000, 24, "f", 1.0, 1.0),
    ("rug1", "Коврик", 2500, 3, "f", 1.6, 1.6), ("rug2", "Большой ковёр", 12000, 6, "f", 2.8, 2.8), ("rug3", "Ковёр с лого", 45000, 10, "f", 3.2, 3.2),
    ("drawers", "Тумба", 6000, 4, "f", .7, .9), ("shelf", "Стеллаж-куб", 9000, 6, "f", .6, 1.6), ("vitrine", "Витрина с трофеями", 90000, 12, "f", .6, 1.8),
    ("sofa", "Диван", 20000, 8, "f", .9, 2.0), ("beanbag", "Кресло-мешок", 5000, 4, "f", .9, .9),
    ("plant", "Растение", 1500, 3, "f", .5, .5), ("palm", "Пальма", 6000, 5, "f", .8, .8), ("bin", "Урна", 700, 1, "f", .4, .4), ("floorlamp", "Торшер", 3500, 4, "f", .4, .4),
    ("fridge", "Мини-холодильник", 15000, 6, "f", .6, .6), ("speakers", "Студийные колонки", 8000, 4, "f", .4, .9), ("neontower", "Неоновая стойка", 30000, 9, "f", .4, .4),
    ("trophy", "Кубок", 300000, 16, "f", .5, .5), ("statue", "Золотая статуя", 5000000, 40, "f", .8, .8),
    ("poster", "Постер", 900, 3, "w", 1.0, 1.4), ("frames", "Рамы с подсветкой", 5000, 5, "w", 3.2, .9), ("foam", "Акустические панели", 20000, 8, "w", 2.8, 1.6),
    ("neon", "Неоновый логотип", 40000, 10, "w", 1.3, 1.3), ("walllamps", "Настенные светильники", 12000, 5, "w", .5, .8), ("clock", "Часы", 2000, 2, "w", .6, .6),
    ("tv", "Телевизор на стене", 120000, 12, "w", 2.0, 1.2), ("goldbtn", "Золотая кнопка", 2000000, 30, "w", .9, .9)]}
# куда ставится предмет сразу после покупки: пол (x, z, поворот 0-3) или стена (l - левая, b - правая; u - вдоль стены, v - высота)
DEF = {"desk1": (.45, 3.0, 0), "desk2": (.5, 3.2, 0), "desk3": (1.2, 1.3, 0), "desk4": (.55, 3.2, 0),
       "chair1": (2.2, 3.1, 2), "chair2": (2.2, 3.1, 2), "chair3": (2.3, 3.1, 2), "chair4": (2.4, 3.1, 2),
       "rug1": (3.8, 3.8, 0), "rug2": (4.0, 4.0, 0), "rug3": (4.0, 4.0, 0), "drawers": (.45, 5.3, 0), "shelf": (5.4, .35, 1), "vitrine": (7.0, .4, 1),
       "sofa": (3.0, .5, 1), "beanbag": (6.0, 6.2, 0), "plant": (.35, 7.5, 0), "palm": (7.4, 7.2, 0), "bin": (.3, 6.5, 0), "floorlamp": (7.5, 4.8, 0),
       "fridge": (.4, 6.4, 0), "speakers": (.35, 1.0, 0), "neontower": (6.2, .35, 1), "trophy": (7.5, 2.6, 0), "statue": (6.8, 5.4, 0),
       "poster": ("l", 6.6, 1.9), "frames": ("b", 3.2, 2.3), "foam": ("l", 3.2, 1.7), "neon": ("b", 6.0, 1.9), "walllamps": ("b", 1.2, 1.8),
       "clock": ("b", 7.5, 2.5), "tv": ("b", 4.6, 1.9), "goldbtn": ("l", 1.3, 2.4)}
CLOTH = {i: dict(id=i, name=n, price=p, slot=s, bonus=b) for i, n, p, s, b in [
    ("cap", "Кепка", 800, "hat", 1), ("beanie", "Шапка", 1200, "hat", 1), ("bucket", "Панама", 2000, "hat", 2), ("snap", "Снепбэк", 4000, "hat", 2),
    ("hp", "Наушники", 3500, "ears", 3), ("hp2", "Игровая гарнитура", 15000, "ears", 4), ("hp3", "Студийные наушники", 40000, "ears", 5),
    ("gl1", "Круглые очки", 1500, "glasses", 1), ("gl2", "Тёмные очки", 6000, "glasses", 2), ("gl3", "Геймерские очки", 12000, "glasses", 3),
    ("tee", "Футболка", 600, "top", 0), ("hood", "Худи", 2500, "top", 2), ("jersey", "Джерси", 4000, "top", 3), ("jacket", "Куртка", 7000, "top", 4),
    ("bomber", "Бомбер", 15000, "top", 4), ("blazer", "Пиджак", 40000, "top", 5), ("leather", "Кожаная куртка", 60000, "top", 6),
    ("shorts", "Шорты", 1000, "bottom", 0), ("jeans", "Джинсы", 1500, "bottom", 1), ("sweat", "Спортивки", 2000, "bottom", 1), ("cargo", "Карго", 5000, "bottom", 2), ("suitp", "Костюмные брюки", 20000, "bottom", 3),
    ("sneak", "Кроссовки", 3000, "shoes", 1), ("boots", "Ботинки", 8000, "shoes", 2), ("lux", "Брендовые кроссовки", 35000, "shoes", 3),
    ("chain", "Золотая цепочка", 20000, "acc", 2), ("watch", "Часы", 30000, "acc", 2), ("ring", "Перстень", 10000, "acc", 1)]}
# Скины только для админа. Чтобы добавить свой: допишите id и название сюда и вид в index.html (словарь SK).
SKINS = {"s_legend": "Легенда эфира", "s_tilt": "Король тильта", "s_gold": "Золотой донатер", "s_ghost": "Призрак чата", "s_robot": "Робот-стример",
         "s_cat": "Кот-геймер", "s_neon": "Неоновый хакер", "s_ninja": "Ниндзя эфира", "s_astro": "Космонавт", "s_king": "Король донатов", "s_alien": "Пришелец", "s_angel": "Ангел чата"}
GAMES = {i: dict(id=i, name=n, req=r, base=b) for i, n, r, b in [
    ("chat", "Просто общение", 5, 150000), ("dota2", "Dota 2", 22, 100000), ("valorant", "Valorant", 26, 70000),
    ("mc", "Minecraft", 18, 55000), ("cs2", "Counter-Strike 2", 32, 30000), ("gta", "GTA V", 45, 90000), ("cyber", "Cyberpunk 2077", 70, 12000)]}
DONORS = ["Лёха_228", "Мария", "DarkKnight", "Тимур", "Саша", "xX_Pro_Xx", "Катя", "Влад", "StreamFan", "Диана", "Никита", "Артём", "Зевс", "Лиза_play"]
MSGS = ["Лучший стример!", "Держи на пиво", "Привет с Урала", "Играй лучше)", "ГЛХФ", "Красивая комната!", "Первый раз тут, остаюсь",
        "Сыграй в другую игру", "Топ контент", "На новый комп", "Легенда", "Заказываю трек"]
SLOTS = ("hat", "ears", "glasses", "top", "bottom", "shoes", "acc")

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
new_state = lambda: {"money": START_MONEY, "parts": [], "furn": [], "clothes": [], "wear": {}, "skin": None, "fol": 0.0, "earned": 0, "log": [], "layout": {}}


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
    d["parts"] = [i for i in d["parts"] if i in PARTS]                       # чистим предметы, которых больше нет в каталоге
    d["furn"] = [i for i in d["furn"] if i in FURN]
    d["clothes"] = [i for i in d["clothes"] if i in CLOTH]
    d["wear"] = {s_: i for s_, i in d["wear"].items() if i in d["clothes"]}
    if d["skin"] not in SKINS:
        d["skin"] = None
    d["layout"] = {i: p for i, p in d["layout"].items() if i in d["furn"]}
    if not d.get("lv"):                                                     # один раз расставляем уже купленное
        d["lv"] = 1
        for i in d["furn"]:
            d["layout"].setdefault(i, default_place(i))
    players[uid] = d
    return d


def get_d(uid):
    if uid in players:
        return players[uid]
    r = ex("SELECT name FROM sim WHERE id=?", (uid,)).fetchone()
    return load(uid, r["name"]) if r else None


def save(uid):
    ex("UPDATE sim SET data=? WHERE id=?", (json.dumps(players[uid]), uid))


def default_place(i):
    v = DEF.get(i)
    if not v:
        return {"x": 4.0, "z": 4.0, "r": 0, "w": "f"}
    return {"w": "f", "x": v[0], "z": v[1], "r": v[2]} if FURN[i]["kind"] == "f" else {"w": v[0], "u": v[1], "v": v[2], "r": 0}


def best(d):
    b = {}
    for i in d["parts"]:
        p = PARTS[i]
        if p["slot"] not in b or p["power"] > b[p["slot"]]["power"]:
            b[p["slot"]] = p
    return b


def pc(d):
    b = best(d)
    miss = [s for s in REQ_SLOTS if s not in b]
    return (0 if miss else .35 * b["cpu"]["power"] + .45 * b["gpu"]["power"] + .2 * b["ram"]["power"]), miss, b


def qlv(b):
    return sum(b[s]["lvl"] for s in ("mon", "cam", "mic", "cap", "ring", "kb", "ms") if s in b)      # уровень сетапа, максимум 28


decor = lambda d: min(200, sum(FURN[i]["pts"] for i in d["furn"]))
charisma = lambda d: min(25, sum(CLOTH[i]["bonus"] for i in d["wear"].values() if i in CLOTH)) / 100
fps_for = lambda sc, req: int(min(400, 60 * (sc / req) ** 1.2)) if req else 400                       # FPS растёт вместе с мощностью ПК


def fol_cap(d):                        # потолок аудитории зависит от ПК, оборудования и комнаты: чем круче, тем больше
    sc, _, b = pc(d)
    tier = (sc / 90 + qlv(b) / 28 + decor(d) / 200) / 3
    return int(150 + 4000000 * tier ** 4)


def view(d):
    sc, miss, b = pc(d)
    return {"money": d["money"], "parts": d["parts"], "furn": d["furn"], "clothes": d["clothes"], "wear": d["wear"], "skin": d["skin"],
            "fol": int(d["fol"]), "earned": d["earned"], "score": round(sc, 1), "miss": miss, "decor": decor(d), "log": d["log"][-20:],
            "layout": d["layout"], "cap": fol_cap(d), "q": qlv(b)}


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
    ratio = sc / G["req"]
    fps = fps_for(sc, G["req"])
    lag = max(.15, min(1, ratio ** 1.2))
    smooth = 1 + min(.25, max(0, fps - 60) / 800)               # плавная картинка привлекает зрителей
    pop = max(.3, min(3, online(g) / 60000))                    # популярность игры сейчас
    look, cha = 1 + decor(d) / 80, 1 + charisma(d)
    qual = .7 + .035 * qlv(b)
    viewers = max(1, int((2 + d["fol"] * .05) * pop * lag * qual * (1 + decor(d) / 200) * cha * smooth * rng.uniform(.85, 1.15)))
    evs = []
    for _ in range(poisson(min(10, .2 + viewers / 500))):
        a = max(10, int(rng.uniform(10, 70) * (.6 + .4 * min(pop, 2.5)) * look * cha * lag * (1 + math.log10(viewers + 1) / 2) / 10) * 10)
        evs.append({"n": rng.choice(DONORS), "a": a, "m": rng.choice(MSGS), "g": G["name"]})
    gain = sum(e["a"] for e in evs)
    d["money"] += gain
    d["earned"] += gain
    cap = fol_cap(d)
    if cap > d["fol"]:
        d["fol"] += (cap - d["fol"]) * .0012 * min(1.5, pop) * lag
    d["log"] = (d["log"] + evs)[-40:]
    return {"viewers": viewers, "evs": evs, "online": online(g), "perf": int(lag * 100), "fps": fps}


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
           "skins": [{"id": k, "name": v} for k, v in SKINS.items()] if admin else [], "games": list(GAMES.values()), "req": list(REQ_SLOTS)}
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
                    if kind == "furn":
                        d["layout"][i] = default_place(i)
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
            elif t == "place" and m.get("id") in d["furn"]:
                i, F = m["id"], FURN[m["id"]]
                try:
                    if F["kind"] == "f":
                        p = {"w": "f", "x": min(7.8, max(.2, float(m["x"]))), "z": min(7.8, max(.2, float(m["z"]))), "r": int(m.get("r", 0)) % 4}
                    else:
                        p = {"w": "l" if m.get("w") == "l" else "b", "u": min(8, max(0, float(m["u"]))), "v": min(3, max(.3, float(m["v"]))), "r": 0}
                    d["layout"][i] = p
                    save(uid)
                    await push(uid)
                except (KeyError, ValueError, TypeError):
                    await err("Некорректное положение")
            elif t == "unplace" and m.get("id") in d["layout"]:
                d["layout"].pop(m["id"], None)
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
                    await send({"t": "tick", **res, "cap": fol_cap(d), "money": d["money"], "fol": int(d["fol"]), "earned": d["earned"]})
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
