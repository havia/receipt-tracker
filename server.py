"""
Receipt Price Tracker - WhatsApp Webhook Server
Receives receipt images via Meta WhatsApp Cloud API, extracts prices with Claude,
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

WHATSAPP_TOKEN = os.environ["WHATSAPP_TOKEN"]
WHATSAPP_PHONE_NUMBER_ID = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
WEBHOOK_VERIFY_TOKEN = os.environ["WEBHOOK_VERIFY_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

GRAPH_API_URL = "https://graph.facebook.com/v20.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized.")
    yield

app = FastAPI(lifespan=lifespan)


async def send_whatsapp_message(phone: str, message: str):
    """Send a text reply via Meta WhatsApp Cloud API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GRAPH_API_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages",
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message},
            },
            timeout=30,
        )
        resp.raise_for_status()
        logger.info(f"Sent reply to {phone}")


async def download_media(media_id: str) -> tuple[bytes, str]:
    """Download media from Meta using media_id. Returns (bytes, mime_type)."""
    async with httpx.AsyncClient() as client:
        # Step 1: resolve media_id → download URL + mime_type
        meta_resp = await client.get(
            f"{GRAPH_API_URL}/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            timeout=15,
        )
        meta_resp.raise_for_status()
        meta = meta_resp.json()
        url = meta["url"]
        mime_type = meta.get("mime_type", "image/jpeg")

        # Step 2: download actual bytes
        media_resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            timeout=30,
            follow_redirects=True,
        )
        media_resp.raise_for_status()
        return media_resp.content, mime_type


@app.get("/webhook")
async def webhook_verify(request: Request):
    """Meta webhook verification handshake."""
    params = request.query_params
    if (params.get("hub.mode") == "subscribe" and
            params.get("hub.verify_token") == WEBHOOK_VERIFY_TOKEN):
        return int(params["hub.challenge"])
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    logger.info(f"Webhook received: {json.dumps(payload)[:300]}")

    # Only handle whatsapp_business_account events
    if payload.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if change.get("field") != "messages":
                continue

            for msg in value.get("messages", []):
                await handle_message(msg)

    return {"status": "ok"}


async def handle_message(msg: dict):
    phone = msg.get("from", "")
    msg_type = msg.get("type", "")

    # Supported image types
    if msg_type not in ("image", "document"):
        await send_whatsapp_message(phone,
            "👋 שלח לי תמונה של קבלה ואני אנתח את המחירים!\n"
            "Send me a receipt photo and I'll analyze the prices!")
        return

    media_info = msg.get(msg_type, {})
    media_id = media_info.get("id")
    if not media_id:
        await send_whatsapp_message(phone, "❌ Couldn't find the image. Please try again.")
        return

    # Download media
    try:
        media_bytes, mime_type = await download_media(media_id)
    except Exception as e:
        logger.error(f"Failed to download media: {e}")
        await send_whatsapp_message(phone, "❌ Couldn't download the image. Please try again.")
        return

    media_b64 = base64.standard_b64encode(media_bytes).decode()

    await send_whatsapp_message(phone, "🔍 מנתח את הקבלה... / Analyzing your receipt...")

    # Extract items with Claude
    try:
        vendor, items = await extract_items_from_receipt(
            media_b64, mime_type, ANTHROPIC_API_KEY
        )
    except Exception as e:
        logger.error(f"Claude extraction failed: {e}")
        await send_whatsapp_message(phone, "❌ Couldn't read the receipt. Make sure the image is clear.")
        return

    if not items:
        await send_whatsapp_message(phone, "⚠️ No items found. Is this a grocery/store receipt?")
        return

    save_items(vendor, items)
    history = get_price_history()
    report = build_comparison_report(vendor, items, history)
    await send_whatsapp_message(phone, report)


@app.get("/health")
async def health():
    return {"status": "ok"}
