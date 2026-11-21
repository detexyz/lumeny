import asyncio, hashlib, hmac, json, math, os, random, re, time
from urllib.parse import parse_qsl
from aiohttp import web, ClientSession

TOKEN = os.environ.get("BOT_TOKEN", "")
BOT = os.environ.get("BOT_USERNAME", "")
BASE = os.environ.get("PUBLIC_URL", "").rstrip("/")
APP_NAME = os.environ.get("APP_SHORT_NAME", "")   # короткое имя из BotFather /newapp (необязательно)
DEV = os.environ.get("DEV") == "1"          # DEV=1: вход без проверки Telegram (только для тестов)
DB = os.environ.get("DB_PATH", "db.json")
BET_TIME, SPIN_TIME, START_BAL, MIN_BAL, EARN_MAX = 15, 6.5, 100, 10, 38
rng = random.SystemRandom()
users = json.load(open(DB)) if os.path.exists(DB) else {}
conns, rooms = {}, {}
BOT_NAMES = ["Лиса", "Комета", "Нокс", "Мира", "Тень", "Орион", "Зира", "Бублик", "Вега", "Рекс"]


def save():
    with open(DB, "w") as f:
        json.dump(users, f)


def verify(init):
    d = dict(parse_qsl(init))
    h = d.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), h)
    if not ok or time.time() - int(d.get("auth_date", 0)) > 86400:
        return None
    return json.loads(d["user"])


async def push(uid):
    m = json.dumps({"t": "me", "balance": users[uid]["bal"], "id": uid, "bot": BOT})
    for ws in list(conns.get(uid, ())):
        try:
            await ws.send_str(m)
        except Exception:
            pass


class Room:
    def __init__(s):
        s.clients, s.bets, s.phase, s.left, s.task = {}, {}, "idle", 0, None
        s.bots_on, s.bot_task = True, None

    async def bc(s, m):
        data = json.dumps(m)
        for ws in list(s.clients):
            try:
                await ws.send_str(data)
            except Exception:
                pass

    def state(s):
        nm = lambda u: users[u]["name"] if u in users else u[4:]
        return {"t": "state", "phase": s.phase, "left": s.left, "bots": s.bots_on,
                "players": [{"id": u, "name": nm(u), "bet": b} for u, b in s.bets.items()]}

    def add_bot(s, name):
        humans = [b for u, b in s.bets.items() if not u.startswith("bot:")]
        ref = max(humans) if humans else 50
        s.bets["bot:" + name] = max(10, int(ref * rng.uniform(.5, 1.5)))

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
        r, acc, win, start = rng.uniform(0, pot), 0, ids[-1], 0
        for u in ids:
            if r < acc + s.bets[u]:
                win, start = u, acc
                break
            acc += s.bets[u]
        if start == 0 and win != ids[0]:
            start = pot - s.bets[win]
        target = 2 * math.pi * (start + s.bets[win] * rng.uniform(.1, .9)) / pot
        await s.bc({"t": "spin", "winner": win, "pot": pot, "target": target})
        await asyncio.sleep(SPIN_TIME)
        if win in users:
            users[win]["bal"] += pot
        for u in ids:
            if u in users:
                users[u]["bal"] = max(users[u]["bal"], MIN_BAL)
        save()
        wname = users[win]["name"] if win in users else win[4:]
        await s.bc({"t": "result", "winner": win, "name": wname, "pot": pot})
        for u in ids:
            await push(u)
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
    uid = str(u["id"])
    rec = users.setdefault(uid, {"name": "", "bal": START_BAL, "start": 0})
    rec["name"] = u.get("first_name", "Игрок")[:20]
    room = rooms.setdefault(re.sub(r"\W", "", q.get("room", "main"))[:32] or "main", Room())
    room.clients[ws] = uid
    conns.setdefault(uid, set()).add(ws)
    await push(uid)
    await ws.send_str(json.dumps(room.state()))
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
                    await ws.send_str(json.dumps({"t": "err", "msg": "Раунд уже идёт"}))
                elif a < 1 or a > rec["bal"]:
                    await ws.send_str(json.dumps({"t": "err", "msg": "Недостаточно люменов"}))
                else:
                    rec["bal"] -= a
                    room.bets[uid] = room.bets.get(uid, 0) + a
                    save()
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
            elif t == "earn_start":
                rec["start"] = time.time()
            elif t == "earn_end":
                dt = time.time() - rec.get("start", 0)
                if rec.get("start") and 19 <= dt <= 45:
                    try:
                        sc = max(0, min(int(m.get("score", 0)), EARN_MAX))
                    except Exception:
                        sc = 0
                    rec["bal"] += sc
                rec["start"] = 0
                save()
                await push(uid)
    finally:
        room.clients.pop(ws, None)
        conns.get(uid, set()).discard(ws)
    return ws


async def bot_loop():
    api, off = f"https://api.telegram.org/bot{TOKEN}/", 0
    async with ClientSession() as s:
        while True:
            try:
                async with s.get(api + "getUpdates", params={"offset": off, "timeout": 30}) as r:
                    data = await r.json()
                for u in data.get("result", []):
                    off = u["update_id"] + 1
                    m = u.get("message") or {}
                    if m.get("text", "").startswith("/start"):
                        room = re.sub(r"\W", "", m["text"].partition(" ")[2])[:32]
                        url = BASE + ("/?room=" + room if room else "/")
                        path = f"{BOT}/{APP_NAME}" if APP_NAME else BOT
                        link = f"https://t.me/{path}" + ("?startapp=" + room if room else "")
                        await s.post(api + "sendMessage", json={
                            "chat_id": m["chat"]["id"],
                            "parse_mode": "HTML",
                            "text": f'Рулетка на лумены.\n\n<a href="{link}">Открыть игру</a>',
                            "reply_markup": {"inline_keyboard": [[{"text": "Открыть игру", "web_app": {"url": url}}]]}})
            except Exception:
                await asyncio.sleep(3)


async def index(req):
    return web.FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))


async def on_start(app):
    if TOKEN:
        app["bot"] = asyncio.create_task(bot_loop())


app = web.Application()
app.router.add_get("/", index)
app.router.add_get("/ws", ws_handler)
app.on_startup.append(on_start)
if __name__ == "__main__":
    web.run_app(app, port=int(os.environ.get("PORT", 8080)))
