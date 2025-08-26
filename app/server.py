import os, uuid, json
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
import httpx

from app.db import (
    init_db, set_premium, log_event, daily_counts
)

load_dotenv()
init_db()

SHOP_ID = os.getenv("YK_SHOP_ID")
SECRET_KEY = os.getenv("YK_SECRET_KEY")
BASE_PUBLIC_URL = os.getenv("BASE_PUBLIC_URL", "http://localhost:8000")
PRICE_VALUE = os.getenv("PRICE_VALUE", "490.00")
PRICE_CURRENCY = os.getenv("PRICE_CURRENCY", "RUB")
BASE_YK = "https://api.yookassa.ru/v3"

app = FastAPI()

def yk():
    return httpx.Client(base_url=BASE_YK, auth=(SHOP_ID, SECRET_KEY), timeout=20)

def read_file(name: str) -> str:
    p = os.path.join(os.path.dirname(__file__), name)
    with open(p, "r", encoding="utf-8") as f:
        return f.read()

# --- Pages ---
@app.get("/", response_class=HTMLResponse)
def landing():
    return HTMLResponse(read_file("landing.html"))

@app.get("/offer", response_class=HTMLResponse)
def offer():
    return HTMLResponse(read_file("offer.html"))

@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return HTMLResponse(read_file("privacy.html"))

@app.get("/thankyou", response_class=HTMLResponse)
def thankyou():
    return HTMLResponse(read_file("thankyou.html"))

# --- Metrics API ---
@app.post("/m/event")
async def metric_event(req: Request,
                       user_agent: str | None = Header(default=None),
                       referer: str | None = Header(default=None)):
    data = await req.json()
    event = data.get("event")
    meta = data.get("meta")
    if not event:
        raise HTTPException(status_code=400, detail="event required")
    log_event(event, json.dumps(meta) if meta else None, user_agent, referer)
    return {"ok": True}

@app.get("/m/daily")
def metric_daily():
    return JSONResponse(daily_counts())

# --- Payments (YooKassa minimal flow) ---
@app.post("/pay")
async def pay(payload: dict):
    user_ref = payload.get("user_ref")
    email = payload.get("email")
    if not user_ref:
        raise HTTPException(status_code=400, detail="user_ref required")

    data = {
        "amount": {"value": PRICE_VALUE, "currency": PRICE_CURRENCY},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"{BASE_PUBLIC_URL}/thankyou"},
        "description": "Test of Discernment — расширенная версия",
        "metadata": {"product": "tod_pro", "user_ref": user_ref, "plan": "one_time"},
    }
    if email:
        data["receipt"] = {
            "customer": {"email": email},
            "items": [{
                "description": "Доступ к расширенной версии",
                "quantity": "1.00",
                "amount": {"value": PRICE_VALUE, "currency": PRICE_CURRENCY},
                "vat_code": 1
            }]
        }

    idem = str(uuid.uuid4())
    log_event("api_pay_create", json.dumps({"user_ref": user_ref}), None, None)
    with yk() as client:
        r = client.post("/payments", json=data, headers={"Idempotence-Key": idem})
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=r.text)
        j = r.json()

    url = j["confirmation"]["confirmation_url"]
    return JSONResponse({"payment_id": j["id"], "url": url})

@app.post("/yookassa/webhook")
async def webhook(req: Request):
    body = await req.json()
    payment_id = body.get("object", {}).get("id")
    if not payment_id:
        raise HTTPException(status_code=400, detail="no payment id")

    with yk() as client:
        r = client.get(f"/payments/{payment_id}")
        if r.status_code >= 300:
            raise HTTPException(status_code=500, detail=r.text)
        j = r.json()

    if j.get("status") == "succeeded":
        user_ref = j.get("metadata", {}).get("user_ref")
        if user_ref:
            set_premium(user_ref, payment_id, "succeeded")
            # Optional notify Telegram
            BOT_TOKEN = os.getenv("BOT_TOKEN")
            if BOT_TOKEN and user_ref.startswith("tg:"):
                chat_id = user_ref.split(":", 1)[1]
                text = "✅ Оплата прошла! Доступ к расширенной версии активирован — нажми /start_pro"
                try:
                    with httpx.Client(timeout=10) as client:
                        client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                    json={"chat_id": chat_id, "text": text})
                except Exception:
                    pass

    return {"ok": True}
