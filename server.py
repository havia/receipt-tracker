"""
Receipt Price Tracker - WhatsApp Webhook Server
Receives receipt images via Wassenger, extracts prices with Claude,
stores history in SQLite, and replies with price comparisons.
"""

import os
import json
import base64
import httpx
import logging
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager

from database import init_db, save_items, get_price_history
from analyzer import extract_items_from_receipt, build_comparison_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WASSENGER_API_KEY = os.environ["WASSENGER_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
WASSENGER_API_URL = "https://api.wassenger.com/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized.")
    yield

app = FastAPI(lifespan=lifespan)


async def send_whatsapp_message(phone: str, message: str):
    """Send a text reply via Wassenger."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{WASSENGER_API_URL}/messages",
            headers={"Token": WASSENGER_API_KEY, "Content-Type": "application/json"},
            json={"phone": phone, "message": message},
            timeout=30,
        )
        resp.raise_for_status()
        logger.info(f"Sent reply to {phone}")


async def download_media(media_url: str) -> bytes:
    """Download media file from Wassenger CDN."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            media_url,
            headers={"Token": WASSENGER_API_KEY},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.content


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    logger.info(f"Webhook received: {json.dumps(payload)[:300]}")

    # Only process incoming messages
    event = payload.get("event") or payload.get("type") or ""
    if "message" not in event.lower() and "received" not in event.lower():
        return {"status": "ignored", "reason": "not a message event"}

    data = payload.get("data", payload)
    msg = data.get("message", data)

    phone = (data.get("phone") or
             data.get("from") or
             msg.get("from") or "").replace("@c.us", "").replace("@s.whatsapp.net", "")
    if not phone:
        return {"status": "ignored", "reason": "no phone number"}

    # Check if message contains media (image or PDF)
    media_url = None
    mime_type = None

    media = msg.get("media") or msg.get("image") or msg.get("document") or {}
    if isinstance(media, dict):
        media_url = media.get("url") or media.get("link")
        mime_type = media.get("mimetype") or media.get("type") or "image/jpeg"
    elif msg.get("type") in ("image", "document"):
        media_url = msg.get("url") or msg.get("link")
        mime_type = msg.get("mimetype", "image/jpeg")

    if not media_url:
        await send_whatsapp_message(phone,
            "👋 שלח לי תמונה או PDF של קבלה ואני אנתח את המחירים!\n"
            "Send me a receipt image or PDF and I'll analyze the prices!")
        return {"status": "ok", "action": "prompted for receipt"}

    # Download media
    try:
        media_bytes = await download_media(media_url)
    except Exception as e:
        logger.error(f"Failed to download media: {e}")
        await send_whatsapp_message(phone, "❌ Couldn't download the image. Please try again.")
        return {"status": "error"}

    media_b64 = base64.standard_b64encode(media_bytes).decode()

    # Notify user we're processing
    await send_whatsapp_message(phone, "🔍 Analyzing your receipt... please wait.")

    # Extract items with Claude
    try:
        vendor, items = await extract_items_from_receipt(
            media_b64, mime_type, ANTHROPIC_API_KEY
        )
    except Exception as e:
        logger.error(f"Claude extraction failed: {e}")
        await send_whatsapp_message(phone, "❌ Couldn't read the receipt. Make sure the image is clear.")
        return {"status": "error"}

    if not items:
        await send_whatsapp_message(phone, "⚠️ No items found in this receipt. Is it a grocery/store receipt?")
        return {"status": "ok"}

    # Save to DB and get history
    save_items(vendor, items)
    history = get_price_history()

    # Build comparison report
    report = build_comparison_report(vendor, items, history)
    await send_whatsapp_message(phone, report)

    return {"status": "ok", "vendor": vendor, "items_found": len(items)}


@app.get("/health")
async def health():
    return {"status": "ok"}
