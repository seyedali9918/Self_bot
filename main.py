import hmac, hashlib, json
from urllib.parse import parse_qsl
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import sqlite3
import os

# ==================== تنظیمات ====================
BOT_TOKEN = "8200221816:AAEy7BSmi08HwAJY7QNLl9WdE6StI90LDqg"
DIAMOND_RATE = 40
DB_PATH = os.path.join(os.getcwd(), "data", "vip_bet.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==================== FastAPI ====================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ==================== توابع دیتابیس ====================
def get_balance(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT diamonds FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        return int(r[0]) if r else 0

def is_active(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_self_active FROM users WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        return bool(r[0]) if r else False

def get_ref_count(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count FROM referrals WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        return int(r[0]) if r else 0

# ==================== احراز هویت ====================
def check_init_data(init_data: str):
    data = dict(parse_qsl(init_data))
    received = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise HTTPException(403, "Invalid initData")
    return json.loads(data["user"])

# ==================== API Endpoints ====================
@app.post("/api/profile")
async def profile(req: Request):
    body = await req.json()
    user = check_init_data(body["initData"])
    user_id = user["id"]
    return {
        "balance": get_balance(user_id),
        "diamond_rate": DIAMOND_RATE,
        "is_active": is_active(user_id),
        "ref_count": get_ref_count(user_id),
        "hourly": 2,
    }

@app.get("/")
async def root():
    return FileResponse(os.path.join(BASE_DIR, "miniapp.html"))

@app.get("/health")
async def health():
    return {"status": "ok", "message": "VIP Mini App API"}
