import hmac, hashlib, json, math, os, secrets, sqlite3, time
from urllib.parse import parse_qsl

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ==================== تنظیمات ====================
BOT_TOKEN = "8846145059:AAGLLYBS21rPEJ4iXJSRuz3bgRkLqT-okU0"
DIAMOND_RATE = 40
DB_PATH = os.path.join(os.getcwd(), "data", "vip_bet.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TAX_PERCENT = 5          # مالیات انتقال (٪) - روی مبلغ اضافه می‌شود
MAX_TRANSFER = 1_000_000
MIN_BET = 10             # حداقل شرط بازی
MAX_BET = 1_000_000
CELLS = 9                # ۳×۳
HOUSE_EDGE = 0.03        # سهم ربات در ضریب‌ها (۳٪)
INIT_DATA_MAX_AGE = 86400

# ==================== FastAPI ====================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Auth(BaseModel):
    initData: str

class TransferIn(Auth):
    receiver: int
    amount: int

class MinesStart(Auth):
    bet: int
    mines: int

class MinesReveal(Auth):
    cell: int


@app.on_event("startup")
def init_tables():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS mines_games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bet INTEGER,
            mines INTEGER,
            bombs TEXT,
            revealed TEXT,
            state TEXT,
            payout INTEGER DEFAULT 0,
            created_at INTEGER
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS rocket_games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            bet INTEGER,
            crash_point REAL,
            started_at REAL,
            state TEXT,
            cashed_mult REAL DEFAULT 0,
            payout INTEGER DEFAULT 0,
            created_at INTEGER
        )""")


# ==================== دیتابیس ====================
def get_balance(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        r = conn.execute("SELECT diamonds FROM users WHERE user_id=?", (user_id,)).fetchone()
        return int(r[0]) if r else 0

def is_active(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        r = conn.execute("SELECT is_self_active FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(r[0]) if r else False

def get_ref_count(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        r = conn.execute("SELECT count FROM referrals WHERE user_id=?", (user_id,)).fetchone()
        return int(r[0]) if r else 0


class Tx:
    """تراکنش امن: یا همه‌چیز انجام می‌شود یا هیچ‌چیز."""
    def __enter__(self):
        self.conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, et, ev, tb):
        try:
            self.conn.execute("COMMIT" if et is None else "ROLLBACK")
        finally:
            self.conn.close()
        return False


def bal_in(conn, uid):
    r = conn.execute("SELECT diamonds FROM users WHERE user_id=?", (uid,)).fetchone()
    return int(r[0]) if r else None


# ==================== احراز هویت ====================
def check_init_data(init_data: str) -> dict:
    data = dict(parse_qsl(init_data))
    received = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(calc, received):
        raise HTTPException(403, "Invalid initData")
    try:
        if time.time() - int(data.get("auth_date", 0)) > INIT_DATA_MAX_AGE:
            raise HTTPException(403, "نشست منقضی شده؛ مینی‌اپ را دوباره باز کنید")
        return json.loads(data["user"])
    except (KeyError, ValueError):
        raise HTTPException(403, "Invalid initData")


def notify(chat_id: int, text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


# ==================== پروفایل ====================
@app.post("/api/profile")
def profile(body: Auth):
    uid = check_init_data(body.initData)["id"]
    return {
        "balance": get_balance(uid),
        "diamond_rate": DIAMOND_RATE,
        "is_active": is_active(uid),
        "ref_count": get_ref_count(uid),
        "hourly": 2,
        "tax_percent": TAX_PERCENT,
        "min_bet": MIN_BET,
    }


# ==================== انتقال الماس ====================
@app.post("/api/transfer")
def transfer(body: TransferIn, bg: BackgroundTasks):
    sender = check_init_data(body.initData)["id"]
    receiver, amount = body.receiver, body.amount

    if amount <= 0:
        raise HTTPException(400, "مقدار الماس باید بیشتر از صفر باشد")
    if amount > MAX_TRANSFER:
        raise HTTPException(400, f"حداکثر مقدار هر انتقال {MAX_TRANSFER:,} الماس است")
    if receiver == sender:
        raise HTTPException(400, "نمی‌توانید به خودتان الماس انتقال بدهید")

    tax = math.ceil(amount * TAX_PERCENT / 100)
    total = amount + tax

    with Tx() as c:
        s = bal_in(c, sender)
        r = bal_in(c, receiver)
        if r is None:
            raise HTTPException(404, "کاربری با این آیدی عددی پیدا نشد (گیرنده باید ربات را استارت کرده باشد)")
        if s is None or s < total:
            raise HTTPException(
                400,
                f"موجودی کافی نیست. برای انتقال {amount:,} الماس با مالیات به {total:,} الماس نیاز دارید، موجودی شما {(s or 0):,} است.",
            )
        c.execute("UPDATE users SET diamonds = diamonds - ? WHERE user_id=?", (total, sender))
        c.execute("UPDATE users SET diamonds = diamonds + ? WHERE user_id=?", (amount, receiver))
        new_s, new_r = s - total, r + amount

    bg.add_task(
        notify, receiver,
        f"💎 {amount:,} الماس از طرف کاربر {sender} به حساب شما واریز شد.\n\nموجودی جدید شما: {new_r:,}",
    )
    return {
        "ok": True, "amount": amount, "tax": tax, "total": total,
        "receiver": receiver, "sender_balance": new_s, "receiver_balance": new_r,
    }


# ==================== بازی الماس‌یاب (Mines) ====================
def mult(mines: int, k: int) -> float:
    """ضریب بعد از باز کردن k خانه سالم (احتمال واقعی × (1 - سهم ربات))."""
    m = 1.0
    for i in range(k):
        m *= (CELLS - i) / (CELLS - mines - i)
    return round(m * (1 - HOUSE_EDGE), 2)


def get_active(c, uid):
    row = c.execute(
        "SELECT game_id, bet, mines, bombs, revealed FROM mines_games WHERE user_id=? AND state='active'",
        (uid,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "bet": row[1], "mines": row[2],
            "bombs": json.loads(row[3]), "revealed": json.loads(row[4])}


def make_view(bet, mines, revealed, balance):
    k = len(revealed)
    cur = mult(mines, k) if k else 1.0
    return {
        "bet": bet, "mines": mines, "revealed": revealed,
        "multiplier": cur,
        "payout": int(bet * cur) if k else 0,
        "next_multiplier": mult(mines, k + 1) if k < CELLS - mines else None,
        "balance": balance,
    }


@app.post("/api/mines/current")
def mines_current(body: Auth):
    uid = check_init_data(body.initData)["id"]
    with Tx() as c:
        g = get_active(c, uid)
        bal = bal_in(c, uid) or 0
    if not g:
        return {"active": False, "balance": bal}
    return {"active": True, **make_view(g["bet"], g["mines"], g["revealed"], bal)}


@app.post("/api/mines/start")
def mines_start(body: MinesStart):
    uid = check_init_data(body.initData)["id"]
    if body.mines not in (1, 2, 3):
        raise HTTPException(400, "تعداد مین باید ۱ تا ۳ باشد")
    if body.bet < MIN_BET:
        raise HTTPException(400, f"حداقل شرط {MIN_BET} الماس است")
    if body.bet > MAX_BET:
        raise HTTPException(400, f"حداکثر شرط {MAX_BET:,} الماس است")

    with Tx() as c:
        if get_active(c, uid):
            raise HTTPException(400, "یک بازی نیمه‌کاره دارید؛ ابتدا آن را تمام کنید")
        bal = bal_in(c, uid)
        if bal is None or bal < body.bet:
            raise HTTPException(400, f"موجودی کافی نیست (موجودی شما {(bal or 0):,} الماس است)")
        bombs = secrets.SystemRandom().sample(range(CELLS), body.mines)
        c.execute("UPDATE users SET diamonds = diamonds - ? WHERE user_id=?", (body.bet, uid))
        c.execute(
            "INSERT INTO mines_games (user_id, bet, mines, bombs, revealed, state, created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, body.bet, body.mines, json.dumps(bombs), "[]", "active", int(time.time())),
        )
        new_bal = bal - body.bet
    return make_view(body.bet, body.mines, [], new_bal)


@app.post("/api/mines/reveal")
def mines_reveal(body: MinesReveal):
    uid = check_init_data(body.initData)["id"]
    if not 0 <= body.cell < CELLS:
        raise HTTPException(400, "خانه نامعتبر است")

    with Tx() as c:
        g = get_active(c, uid)
        if not g:
            raise HTTPException(400, "بازی فعالی وجود ندارد")
        if body.cell in g["revealed"]:
            raise HTTPException(400, "این خانه قبلاً باز شده است")
        bal = bal_in(c, uid) or 0

        # بمب
        if body.cell in g["bombs"]:
            c.execute("UPDATE mines_games SET state='lost' WHERE game_id=?", (g["id"],))
            return {"result": "bomb", "bombs": g["bombs"], "revealed": g["revealed"],
                    "lost": g["bet"], "balance": bal}

        g["revealed"].append(body.cell)

        # همه خانه‌های سالم باز شد → برداشت خودکار
        if len(g["revealed"]) == CELLS - g["mines"]:
            payout = int(g["bet"] * mult(g["mines"], len(g["revealed"])))
            c.execute("UPDATE users SET diamonds = diamonds + ? WHERE user_id=?", (payout, uid))
            c.execute(
                "UPDATE mines_games SET state='cashed', revealed=?, payout=? WHERE game_id=?",
                (json.dumps(g["revealed"]), payout, g["id"]),
            )
            return {"result": "win_all", "bombs": g["bombs"], "revealed": g["revealed"],
                    "payout": payout, "profit": payout - g["bet"], "balance": bal + payout}

        c.execute("UPDATE mines_games SET revealed=? WHERE game_id=?",
                  (json.dumps(g["revealed"]), g["id"]))
        view = make_view(g["bet"], g["mines"], g["revealed"], bal)

    return {"result": "safe", **view}


@app.post("/api/mines/cashout")
def mines_cashout(body: Auth):
    uid = check_init_data(body.initData)["id"]
    with Tx() as c:
        g = get_active(c, uid)
        if not g:
            raise HTTPException(400, "بازی فعالی وجود ندارد")
        if not g["revealed"]:
            raise HTTPException(400, "ابتدا حداقل یک خانه را باز کنید")
        payout = int(g["bet"] * mult(g["mines"], len(g["revealed"])))
        c.execute("UPDATE users SET diamonds = diamonds + ? WHERE user_id=?", (payout, uid))
        c.execute("UPDATE mines_games SET state='cashed', payout=? WHERE game_id=?", (payout, g["id"]))
        bal = bal_in(c, uid) or 0
    return {"result": "cashed", "payout": payout, "profit": payout - g["bet"],
            "bombs": g["bombs"], "revealed": g["revealed"], "balance": bal}


# ==================== بازی پرواز راکت (Crash) ====================
# ضریب با زمان: m(t) = e^(A*t + B*t^2)  → اول آرام، بعد کم‌کم شتاب می‌گیرد
ROCKET_A = 0.045
ROCKET_B = 0.0012
ROCKET_MAX = 200.0   # سقف نقطه ترکیدن


def rocket_mult(t: float) -> float:
    return math.floor(math.exp(ROCKET_A * t + ROCKET_B * t * t) * 100) / 100


def rocket_time_for(cp: float) -> float:
    """چند ثانیه بعد از شروع، ضریب به cp می‌رسد (یعنی راکت می‌ترکد)."""
    x = math.log(cp)
    return (-ROCKET_A + math.sqrt(ROCKET_A ** 2 + 4 * ROCKET_B * x)) / (2 * ROCKET_B)


def gen_crash_point() -> float:
    """نقطه ترکیدن مخفی؛ احتمال رسیدن به x برابر ≈ (1 - HOUSE_EDGE) / x"""
    u = secrets.SystemRandom().random()
    cp = (1 - HOUSE_EDGE) / (1 - u)
    return min(ROCKET_MAX, max(1.0, math.floor(cp * 100) / 100))


def get_rocket(c, uid):
    row = c.execute(
        "SELECT game_id, bet, crash_point, started_at FROM rocket_games WHERE user_id=? AND state='flying'",
        (uid,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "bet": row[1], "cp": row[2], "started": row[3]}


def rocket_settle_lost(c, g):
    c.execute("UPDATE rocket_games SET state='lost' WHERE game_id=?", (g["id"],))


class RocketStart(Auth):
    bet: int


@app.post("/api/rocket/current")
def rocket_current(body: Auth):
    uid = check_init_data(body.initData)["id"]
    with Tx() as c:
        g = get_rocket(c, uid)
        bal = bal_in(c, uid) or 0
        if g:
            elapsed = time.time() - g["started"]
            if elapsed >= rocket_time_for(g["cp"]):
                rocket_settle_lost(c, g)
                return {"active": False, "balance": bal, "crashed": True,
                        "crash_point": g["cp"], "bet": g["bet"]}
            return {"active": True, "bet": g["bet"], "elapsed": elapsed,
                    "balance": bal, "a": ROCKET_A, "b": ROCKET_B}
    return {"active": False, "balance": bal}


@app.post("/api/rocket/start")
def rocket_start(body: RocketStart):
    uid = check_init_data(body.initData)["id"]
    if body.bet < MIN_BET:
        raise HTTPException(400, f"حداقل شرط {MIN_BET} الماس است")
    if body.bet > MAX_BET:
        raise HTTPException(400, f"حداکثر شرط {MAX_BET:,} الماس است")
    with Tx() as c:
        g = get_rocket(c, uid)
        if g:
            if time.time() - g["started"] >= rocket_time_for(g["cp"]):
                rocket_settle_lost(c, g)
            else:
                raise HTTPException(400, "یک پرواز در جریان دارید")
        bal = bal_in(c, uid)
        if bal is None or bal < body.bet:
            raise HTTPException(400, f"موجودی کافی نیست (موجودی شما {(bal or 0):,} الماس است)")
        c.execute("UPDATE users SET diamonds = diamonds - ? WHERE user_id=?", (body.bet, uid))
        c.execute(
            "INSERT INTO rocket_games (user_id, bet, crash_point, started_at, state, created_at) VALUES (?,?,?,?,?,?)",
            (uid, body.bet, gen_crash_point(), time.time(), "flying", int(time.time())),
        )
        new_bal = bal - body.bet
    return {"bet": body.bet, "elapsed": 0, "balance": new_bal, "a": ROCKET_A, "b": ROCKET_B}


@app.post("/api/rocket/state")
def rocket_state(body: Auth):
    uid = check_init_data(body.initData)["id"]
    with Tx() as c:
        g = get_rocket(c, uid)
        bal = bal_in(c, uid) or 0
        if not g:
            return {"state": "none", "balance": bal}
        elapsed = time.time() - g["started"]
        if elapsed >= rocket_time_for(g["cp"]):
            rocket_settle_lost(c, g)
            return {"state": "crashed", "crash_point": g["cp"], "lost": g["bet"], "balance": bal}
        return {"state": "flying", "multiplier": rocket_mult(elapsed), "elapsed": elapsed}


@app.post("/api/rocket/cashout")
def rocket_cashout(body: Auth):
    uid = check_init_data(body.initData)["id"]
    with Tx() as c:
        g = get_rocket(c, uid)
        if not g:
            raise HTTPException(400, "پرواز فعالی وجود ندارد")
        now = time.time()
        elapsed = now - g["started"]
        t_crash = rocket_time_for(g["cp"])
        bal = bal_in(c, uid) or 0
        if elapsed >= t_crash:  # قبل از رسیدن درخواست، ترکیده بود
            rocket_settle_lost(c, g)
            return {"result": "crashed", "crash_point": g["cp"], "lost": g["bet"], "balance": bal}
        m = rocket_mult(elapsed)
        payout = int(g["bet"] * m)
        c.execute("UPDATE users SET diamonds = diamonds + ? WHERE user_id=?", (payout, uid))
        c.execute("UPDATE rocket_games SET state='cashed', cashed_mult=?, payout=? WHERE game_id=?",
                  (m, payout, g["id"]))
        return {"result": "cashed", "multiplier": m, "payout": payout, "profit": payout - g["bet"],
                "crash_point": g["cp"], "elapsed": elapsed, "balance": bal + payout}


# ==================== صفحات ====================
@app.get("/")
def root():
    return FileResponse(os.path.join(BASE_DIR, "miniapp.html"))

@app.get("/health")
def health():
    return {"status": "ok", "message": "VIP Mini App API"}
