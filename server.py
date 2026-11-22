import asyncio, base64, hashlib, hmac, json, math, os, random, re, sqlite3, time
from urllib.parse import parse_qsl
from aiohttp import web, ClientSession

TOKEN = os.environ.get("BOT_TOKEN", "")
BOT = os.environ.get("BOT_USERNAME", "")
BASE = os.environ.get("PUBLIC_URL", "").rstrip("/")
APP_NAME = os.environ.get("APP_SHORT_NAME", "")
ADMIN = os.environ.get("ADMIN_ID", "")          # ваш Telegram ID: админ-команды доступны только ему
DEV = os.environ.get("DEV") == "1"
DB = os.environ.get("DB_PATH", "db.sqlite")
BET_TIME, SPIN_TIME, START_BAL, MIN_BAL, EARN_MAX = 15, 6.5, 100, 10, 38
BOT_NAMES = ["Лиса", "Комета", "Нокс", "Мира", "Тень", "Орион", "Зира", "Бублик", "Вега", "Рекс"]
rng = random.SystemRandom()
DATABASE_URL = os.environ.get("DATABASE_URL", "")   # бесплатная база (Neon/Supabase); без неё используется SQLite-файл
SCHEMA = """CREATE TABLE IF NOT EXISTS u(id TEXT PRIMARY KEY, name TEXT, bal INT DEFAULT 100, day INT DEFAULT 0,
  streak INT DEFAULT 0, rounds INT DEFAULT 0, wins INT DEFAULT 0, best INT DEFAULT 0, started INT DEFAULT 0, remind INT DEFAULT 0, luck REAL DEFAULT 1,
  title TEXT DEFAULT '', owned TEXT DEFAULT '', ach TEXT DEFAULT '', wstreak INT DEFAULT 0, earned INT DEFAULT 0)"""
if DATABASE_URL:
    import psycopg2, psycopg2.extras
    pg = None

    def ex(sql, a=()):
        global pg
        sql = sql.replace("?", "%s").replace("MAX(best,%s)", "GREATEST(best,%s)")
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
ex(SCHEMA)
for col in ("luck REAL DEFAULT 1", "title TEXT DEFAULT ''", "owned TEXT DEFAULT ''", "ach TEXT DEFAULT ''",
            "wstreak INT DEFAULT 0", "earned INT DEFAULT 0"):      # для баз, созданных раньше
    try:
        ex("ALTER TABLE u ADD COLUMN " + col)
    except Exception:
        pass
ex("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v INT)")
ex("INSERT OR IGNORE INTO kv(k,v) VALUES('jp',0)")
JP = [int(ex("SELECT v FROM kv WHERE k='jp'").fetchone()["v"])]      # копилка джекпота


def jp_set(v):
    JP[0] = v
    ex("UPDATE kv SET v=? WHERE k='jp'", (v,))


EARN = {"catch": (15, 60, 38), "react": (6, 120, 30), "memory": (8, 240, 30), "hilo": (4, 240, 20)}  # (мин. сек, макс. сек, макс. люменов)
conns, rooms, earn, http = {}, {}, {}, None


def U(uid):
    r = ex("SELECT * FROM u WHERE id=?", (uid,)).fetchone()
    return dict(r) if r else None


def S(uid, **kw):
    ex("UPDATE u SET " + ",".join(f"{k}=?" for k in kw) + " WHERE id=?", (*kw.values(), uid))


def add_bal(uid, d):
    ex("UPDATE u SET bal=bal+? WHERE id=?", (d, uid))


day = lambda: int(time.time() // 86400)
bonus = lambda st: 50 + min(st, 7) * 10


def me(uid):
    u, d = U(uid), day()
    nxt = u["streak"] + 1 if u["day"] == d - 1 else 1
    return {"t": "me", "balance": u["bal"], "id": uid, "bot": BOT, "rounds": u["rounds"], "wins": u["wins"],
            "best": u["best"], "streak": u["streak"], "can_claim": u["day"] < d, "bonus": bonus(nxt)}


async def push(uid):
    m = json.dumps(me(uid))
    for ws in list(conns.get(uid, ())):
        try:
            await ws.send_str(m)
        except Exception:
            pass


SHOP = {"lucky": ("Везунчик", 300), "sniper": ("Снайпер", 600), "ice": ("Ледяной король", 1500), "wolf": ("Снежный волк", 3000)}
ACH = {  # id: (название, условие, награда, титул)
    "first": ("Первая победа", "Выиграйте раунд", 50, "Новичок"),
    "streak3": ("Три подряд", "Выиграйте 3 раунда подряд", 150, "Серийный"),
    "allin": ("Ва-банк", "Поставьте всё и выиграйте", 100, "Отчаянный"),
    "rich": ("Богач", "Накопите 1000 люменов", 200, "Богач"),
    "rounds50": ("Ветеран", "Сыграйте 50 раундов", 150, "Ветеран"),
    "bigpot": ("Крупный куш", "Выиграйте банк от 500", 150, "Хищник"),
    "farmer": ("Трудяга", "Заработайте 500 люменов в мини-играх", 100, "Трудяга"),
    "memory": ("Идеальная память", "Пройдите «Память» без ошибок", 100, "Памятливый"),
    "jackpot": ("Джекпот", "Сорвите копилку", 300, "Счастливчик"),
}
TITLES = {k: v[0] for k, v in SHOP.items()}
TITLES.update({"a_" + k: v[3] for k, v in ACH.items()})
tname = lambda k: TITLES.get(k or "", "")
lst = lambda x: set(filter(None, (x or "").split(",")))


def ach_ok(k, u, c):
    return bool({"first": u["wins"] >= 1, "streak3": u["wstreak"] >= 3, "allin": c.get("allin"), "rich": u["bal"] >= 1000,
                 "rounds50": u["rounds"] >= 50, "bigpot": c.get("pot", 0) >= 500, "farmer": u["earned"] >= 500,
                 "memory": c.get("memory"), "jackpot": c.get("jackpot")}[k])


async def send_to(uid, m):
    data = json.dumps(m)
    for ws in list(conns.get(uid, ())):
        try:
            await ws.send_str(data)
        except Exception:
            pass


async def check_ach(uid, **ctx):
    u = U(uid)
    have, owned, new = lst(u["ach"]), lst(u["owned"]), []
    for k, (name, desc, rew, title) in ACH.items():
        if k not in have and ach_ok(k, u, ctx):
            have.add(k)
            owned.add("a_" + k)
            new.append((name, rew))
    if new:
        S(uid, ach=",".join(have), owned=",".join(owned))
        add_bal(uid, sum(r for _, r in new))
        for name, rew in new:
            await send_to(uid, {"t": "toast", "text": f"Достижение: {name} (+{rew})"})
        await push(uid)


def shop_msg(uid):
    u = U(uid)
    owned, have = lst(u["owned"]), lst(u["ach"])
    items = [{"id": k, "name": n, "price": p, "owned": k in owned} for k, (n, p) in SHOP.items()]
    items += [{"id": "a_" + k, "name": v[3], "price": 0, "owned": True} for k, v in ACH.items() if "a_" + k in owned]
    return {"t": "shop", "items": items, "title": u["title"] or "",
            "ach": [{"id": k, "name": v[0], "desc": v[1], "reward": v[2], "done": k in have} for k, v in ACH.items()]}


def verify(init):
    d = dict(parse_qsl(init))
    h = d.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h)
    if not ok or time.time() - int(d.get("auth_date", 0)) > 86400:
        return None
    return json.loads(d["user"])


def lk(u):
    """Множитель удачи игрока (боты всегда 1). Меняется админ-командой /luck и виден всем на колесе."""
    if u.startswith("bot:"):
        return 1.0
    r = U(u)
    return float(r.get("luck") or 1.0) if r else 1.0


class Room:
    def __init__(s):
        s.clients, s.bets, s.phase, s.left, s.task = {}, {}, "idle", 0, None
        s.bots_on, s.bot_task, s.hist = True, None, []
        s.info, s.allin = {}, set()

    async def bc(s, m):
        data = json.dumps(m)
        for ws in list(s.clients):
            try:
                await ws.send_str(data)
            except Exception:
                pass

    def state(s):
        nm = lambda u: s.info[u]["name"] if u in s.info else u[4:]
        return {"t": "state", "phase": s.phase, "left": s.left, "bots": s.bots_on, "history": s.hist, "online": len(set(s.clients.values())), "jackpot": JP[0],
                "players": [{"id": u, "name": nm(u), "bet": b, "title": s.info.get(u, {}).get("title", "")} for u, b in s.bets.items()]}

    def add_bot(s, name):
        humans = [b for u, b in s.bets.items() if not u.startswith("bot:")]
        s.bets["bot:" + name] = max(10, int((max(humans) if humans else 50) * rng.uniform(.5, 1.5)))

    async def fill_bots(s):
        await asyncio.sleep(2)
        if s.phase == "idle" and s.bots_on and len(s.bets) < 2 and not s.task:
            names = rng.sample(BOT_NAMES, 3)
            for n in names[:2]:
                s.add_bot(n)
            s.task = asyncio.create_task(s.run())
            asyncio.create_task(s.late_bot(names[2]))
        s.bot_task = None

    async def late_bot(s, name):
        await asyncio.sleep(rng.uniform(3, 9))
        if s.phase == "bet":
            s.add_bot(name)
            await s.bc(s.state())

    async def run(s):
        s.phase, s.left = "bet", BET_TIME
        while s.left > 0:
            await s.bc(s.state())
            await asyncio.sleep(1)
            s.left -= 1
        s.phase = "spin"
        ids, pot = list(s.bets), sum(s.bets.values())
        wt = {u: s.bets[u] * lk(u) for u in ids}
        W = sum(wt.values())
        r, acc, win = rng.uniform(0, W), 0, ids[-1]
        for u in ids:
            if r < acc + wt[u]:
                win = u
                break
            acc += wt[u]
        before = 0                                   # угол считаем по видимым ставкам: колесо выглядит обычным
        for u in ids:
            if u == win:
                break
            before += s.bets[u]
        target = 2 * math.pi * (before + s.bets[win] * rng.uniform(.1, .9)) / pot
        await s.bc({"t": "spin", "winner": win, "pot": pot, "target": target})
        await asyncio.sleep(SPIN_TIME)
        humans = [u for u in ids if not u.startswith("bot:")]
        fee = max(1, int(pot * .05))                 # 5% банка уходит в копилку
        jp_hit = 0
        if win in humans and JP[0] > 0 and rng.random() < .04:
            jp_hit = JP[0]
            jp_set(0)
        jp_set(JP[0] + fee)
        payout = pot - fee + jp_hit
        for u in humans:
            ex("UPDATE u SET rounds=rounds+1, wstreak=CASE WHEN id=? THEN wstreak+1 ELSE 0 END WHERE id=?", (win, u))
        if win in humans:
            ex("UPDATE u SET bal=bal+?, wins=wins+1, best=MAX(best,?) WHERE id=?", (payout, payout, win))
        for u in humans:
            ex("UPDATE u SET bal=? WHERE id=? AND bal<?", (MIN_BAL, u, MIN_BAL))
        wname = U(win)["name"] if win in humans else win[4:]
        s.hist = (s.hist + [{"name": wname, "pot": payout}])[-8:]
        await s.bc({"t": "result", "winner": win, "name": wname, "pot": payout, "jp": jp_hit})
        for u in humans:
            await push(u)
        for u in humans:
            await check_ach(u, allin=(u == win and u in s.allin), pot=payout if u == win else 0, jackpot=(u == win and jp_hit > 0))
        s.allin = set()
        s.bets = {}
        await asyncio.sleep(4)
        s.phase, s.task = "idle", None
        await s.bc(s.state())


async def ws_handler(req):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    q = req.query
    u = {"id": q.get("id", "1"), "first_name": q.get("name", "Dev")} if DEV else verify(q.get("initData", ""))
    if not u:
        await ws.close()
        return ws
    uid, name = str(u["id"]), u.get("first_name", "Игрок")[:20]
    ex("INSERT OR IGNORE INTO u(id,name,bal) VALUES(?,?,?)", (uid, name, START_BAL))
    S(uid, name=name)
    room = rooms.setdefault(re.sub(r"\W", "", q.get("room", "main"))[:32] or "main", Room())
    room.clients[ws] = uid
    conns.setdefault(uid, set()).add(ws)
    err = lambda t: ws.send_str(json.dumps({"t": "err", "msg": t}))
    await push(uid)
    await room.bc(room.state())
    try:
        async for msg in ws:
            try:
                m = json.loads(msg.data)
            except Exception:
                continue
            t = m.get("t")
            if t == "bet":
                try:
                    a = int(m.get("amount", 0))
                except Exception:
                    a = 0
                if room.phase not in ("idle", "bet"):
                    await err("Раунд уже идёт")
                elif a < 1 or a > U(uid)["bal"]:
                    await err("Недостаточно люменов")
                else:
                    add_bal(uid, -a)
                    u2 = U(uid)
                    room.info[uid] = {"name": u2["name"], "title": tname(u2["title"])}
                    if u2["bal"] == 0:
                        room.allin.add(uid)
                    room.bets[uid] = room.bets.get(uid, 0) + a
                    await push(uid)
                    if room.phase == "idle" and len(room.bets) >= 2 and not room.task:
                        room.task = asyncio.create_task(room.run())
                    elif room.phase == "idle" and room.bots_on and not room.bot_task:
                        room.bot_task = asyncio.create_task(room.fill_bots())
                        await room.bc(room.state())
                    else:
                        await room.bc(room.state())
            elif t == "bots":
                room.bots_on = bool(m.get("on"))
                await room.bc(room.state())
            elif t == "daily":
                rec, d = U(uid), day()
                if rec["day"] >= d:
                    await err("Бонус уже получен")
                else:
                    st = rec["streak"] + 1 if rec["day"] == d - 1 else 1
                    S(uid, day=d, streak=st)
                    add_bal(uid, bonus(st))
                    await push(uid)
            elif t == "top":
                rows = ex("SELECT id,name,bal,title FROM u ORDER BY bal DESC LIMIT 10").fetchall()
                await ws.send_str(json.dumps({"t": "top", "rows": [{**dict(r), "title": tname(r["title"])} for r in rows]}))
            elif t == "earn_start":
                earn[uid] = (time.time(), m.get("game") if m.get("game") in EARN else "catch")
            elif t == "earn_end":
                st, g = earn.pop(uid, (0, "catch"))
                lo, hi, cap = EARN[g]
                dt = time.time() - st
                try:
                    sc = max(0, min(int(m.get("score", 0)), cap))
                except Exception:
                    sc = 0
                why = ("сессия потерялась, сыграйте ещё раз" if not st else "время вышло" if dt > hi
                       else "слишком быстро" if sc and dt < lo else None)
                if why is None:
                    add_bal(uid, sc)
                    ex("UPDATE u SET earned=earned+? WHERE id=?", (sc, uid))
                    await ws.send_str(json.dumps({"t": "earned", "ok": True, "n": sc}))
                else:
                    print("EARN REJECTED", uid, g, round(dt), flush=True)
                    await ws.send_str(json.dumps({"t": "earned", "ok": False, "why": why}))
                await push(uid)
                if why is None:
                    await check_ach(uid, memory=(g == "memory" and sc >= 30))
            elif t == "shop":
                await ws.send_str(json.dumps(shop_msg(uid)))
            elif t in ("buy", "equip"):
                k, u2 = m.get("id") or "", U(uid)
                owned = lst(u2["owned"])
                if t == "buy" and k not in SHOP:
                    await err("Такого товара нет")
                elif t == "buy" and k in owned:
                    await err("Уже куплено")
                elif t == "buy" and u2["bal"] < SHOP[k][1]:
                    await err("Недостаточно люменов")
                elif t == "equip" and k and k not in owned:
                    await err("Этот титул вам недоступен")
                else:
                    if t == "buy":
                        add_bal(uid, -SHOP[k][1])
                        S(uid, owned=",".join(owned | {k}))
                    S(uid, title=k)
                    if uid in room.info:
                        room.info[uid]["title"] = tname(k)
                    await push(uid)
                    await ws.send_str(json.dumps(shop_msg(uid)))
    finally:
        room.clients.pop(ws, None)
        conns.get(uid, set()).discard(ws)
        asyncio.create_task(room.bc(room.state()))
    return ws


# ---------- бот: /start, уведомления, админ-команды ----------
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


async def open_msg(chat, room="", text="Рулетка на лумены."):
    url = BASE + ("/?room=" + room if room else "/")
    link = f"https://t.me/{BOT + '/' + APP_NAME if APP_NAME else BOT}" + ("?startapp=" + room if room else "")
    body = {"chat_id": chat, "parse_mode": "HTML", "text": f'{text}\n\n<a href="{link}">Открыть игру</a>'}
    if BASE.startswith("https://"):
        body["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть игру", "web_app": {"url": url}}]]}
    r = await tg("sendMessage", **body)
    if not r.get("ok"):      # запасной вариант: простое сообщение без кнопки и ссылки
        r = await tg("sendMessage", chat_id=chat, text=f"{text}\nИгра пока не настроена. Проверьте PUBLIC_URL и BOT_USERNAME.")
    return r


async def flash(m, secs):
    """Админ-фото: скачиваем у Telegram и показываем на экранах всех подключённых игроков."""
    photos = m.get("photo") or (m.get("reply_to_message") or {}).get("photo") or []
    pick = next((p for p in reversed(photos) if p.get("file_size", 0) <= 1_500_000), None)
    if not pick:
        return 0
    path = (await tg("getFile", file_id=pick["file_id"])).get("result", {}).get("file_path")
    if not path:
        return 0
    async with http.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}") as r:
        data = await r.read()
    msg = json.dumps({"t": "flash", "ms": int(secs * 1000), "img": "data:image/jpeg;base64," + base64.b64encode(data).decode()})
    n = 0
    for wss in list(conns.values()):
        for ws in list(wss):
            try:
                await ws.send_str(msg)
                n += 1
            except Exception:
                pass
    return n


async def admin_cmd(chat, text):
    p = text.split(maxsplit=2)
    c = p[0].split("@")[0]
    reply = lambda t: tg("sendMessage", chat_id=chat, text=t)
    if c == "/give" and len(p) == 3 and p[2].lstrip("-").isdigit() and U(p[1]):
        add_bal(p[1], int(p[2]))
        await push(p[1])
        await reply(f"Готово: {U(p[1])['name']} теперь ✦ {U(p[1])['bal']}")
    elif c == "/reset" and len(p) >= 2:
        if p[1] == "all":
            if len(p) == 3 and p[2].lower() == "да":
                ex("UPDATE u SET bal=0")
                for uid in list(conns):
                    await push(uid)
                await reply("Балансы всех игроков обнулены")
            else:
                await reply("Это обнулит баланс ВСЕХ игроков. Подтвердите: /reset all да")
        elif U(p[1]):
            try:
                v = max(0, int(p[2])) if len(p) == 3 else 0
            except ValueError:
                return await reply("Сумма должна быть целым числом")
            S(p[1], bal=v)
            await push(p[1])
            await reply(f"{U(p[1])['name']}: баланс теперь ✦ {v}")
        else:
            await reply("Игрок с таким ID не найден")
    elif c == "/luck":
        if len(p) == 3:
            try:
                k = round(max(.1, min(float(p[2].replace(",", ".")), 5)), 2)
            except ValueError:
                return await reply("Множитель — число от 0.1 до 5 (1 = обычные шансы)")
            if not U(p[1]):
                return await reply("Игрок с таким ID не найден")
            S(p[1], luck=k)
            for rm in rooms.values():
                if p[1] in rm.clients.values():
                    asyncio.create_task(rm.bc(rm.state()))
            await reply(f"{U(p[1])['name']}: множитель удачи ×{k} (игроки этого не видят)")
        else:
            rows = ex("SELECT id,name,luck FROM u WHERE luck<>1").fetchall()
            await reply("\n".join(f"{r['id']} {r['name']} ×{r['luck']}" for r in rows) or "Особых множителей нет")
    elif c == "/players":
        rows = ex("SELECT id,name,bal FROM u ORDER BY rounds DESC LIMIT 30").fetchall()
        await reply("\n".join(f"{r['id']} {r['name']} ✦{r['bal']}" for r in rows) or "Игроков пока нет")
    elif c == "/stats":
        n, s_, st = dict(ex("SELECT COUNT(*) AS a,COALESCE(SUM(bal),0) AS b,COALESCE(SUM(started),0) AS c FROM u").fetchone()).values()
        await reply(f"Игроков: {n}\nНажали Start: {st}\nВсего люменов: {s_}\nКомнат: {len(rooms)}\nОнлайн: {len(conns) and sum(len(v) for v in conns.values())}")
    elif c == "/broadcast" and len(text.split(maxsplit=1)) == 2:
        body, k = text.split(maxsplit=1)[1], 0
        for r in ex("SELECT id FROM u WHERE started=1 AND remind<999999").fetchall():
            k += bool((await tg("sendMessage", chat_id=r["id"], text=body)).get("ok"))
            await asyncio.sleep(.05)
        await reply(f"Отправлено: {k}")
    else:
        await reply("/stats\n/players\n/give ID число\n/reset ID (или /reset ID число)\n/reset all да\n/luck ID множитель (0.1–5)\n/luck — список\n/flash — фото с подписью /flash [секунды]\n/broadcast текст")


async def handle_update(up):
    m = up.get("message") or {}
    text, uid = m.get("text") or m.get("caption") or "", str(m.get("from", {}).get("id", ""))
    if not text.startswith("/") or not uid:
        return
    cmd = text.split()[0].split("@")[0]
    if cmd == "/flash":
        if ADMIN and uid == ADMIN:
            parts = text.split()
            try:
                secs = max(.5, min(float(parts[1].replace(",", ".")), 10)) if len(parts) > 1 else 2.0
            except ValueError:
                secs = 2.0
            n = await flash(m, secs)
            await tg("sendMessage", chat_id=m["chat"]["id"],
                     text=f"Показано на экранах: {n}" if n else "Отправьте фото с подписью /flash (или ответьте /flash на фото)")
        return
    if cmd == "/start":
        ex("INSERT OR IGNORE INTO u(id,name,bal) VALUES(?,?,?)", (uid, m["from"].get("first_name", "Игрок")[:20], START_BAL))
        S(uid, started=1, remind=0)
        await open_msg(m["chat"]["id"], re.sub(r"\W", "", text.partition(" ")[2])[:32])
    elif cmd == "/stop":
        if U(uid):
            S(uid, remind=999999)
        await tg("sendMessage", chat_id=m["chat"]["id"], text="Уведомления выключены. Вернуть: /start")
    elif ADMIN and uid == ADMIN:
        await admin_cmd(m["chat"]["id"], text)


async def bot_loop():
    off = 0
    await tg("deleteWebhook")
    print("BOT STARTED:", (await tg("getMe")).get("result", {}).get("username"), flush=True)
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


async def remind_loop():
    while True:
        await asyncio.sleep(1800)
        d = day()
        if not 7 <= time.gmtime().tm_hour <= 19:     # время UTC, примерно дневные часы
            continue
        for r in ex("SELECT id FROM u WHERE started=1 AND day<? AND remind<?", (d, d)).fetchall():
            await open_msg(r["id"], text="Ежедневный бонус люменов ждёт вас.")
            S(r["id"], remind=d)
            await asyncio.sleep(.05)


async def index(req):
    return web.FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))


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


async def health(req):
    return web.Response(text="ok")


async def on_start(app):
    global http
    http = ClientSession()
    if not TOKEN:
        return
    print("BOT:", (await tg("getMe")).get("result", {}).get("username"), flush=True)
    if BASE.startswith("https://"):      # вебхук: Telegram сам будит сервер при сообщении
        print("WEBHOOK:", await tg("setWebhook", url=BASE + "/tg", secret_token=SECRET, allowed_updates=["message"]), flush=True)
        app["tasks"] = [asyncio.create_task(remind_loop())]
    else:                                 # без PUBLIC_URL: запасной вариант, опрос
        app["tasks"] = [asyncio.create_task(bot_loop()), asyncio.create_task(remind_loop())]


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
