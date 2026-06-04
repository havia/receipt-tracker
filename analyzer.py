"""
Claude-powered receipt analyzer.
1. extract_items_from_receipt — sends image to Claude, gets structured JSON back
2. build_comparison_report    — compares new items against DB history
"""

import json
import httpx
from database import normalize, get_item_trend

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"

EXTRACT_SYSTEM = """You are a receipt parser. The user sends a photo or scan of a grocery/store receipt.

Extract ALL line items and return ONLY a JSON object in this exact format — no markdown, no explanation:

{
  "vendor": "Store name (guess from receipt header or items)",
  "items": [
    {"name": "Milk 3% 1L", "price": 6.90, "unit": "1L"},
    {"name": "Bread whole wheat", "price": 12.50, "unit": null}
  ]
}

Rules:
- price is the final unit price in local currency (NIS/₪ assumed unless stated otherwise)
- unit is the package size if visible (e.g. "1L", "500g", "kg"), otherwise null
- name should be descriptive enough to match across vendors (include fat%, size, brand if visible)
- If the receipt is not a grocery/store receipt, return {"vendor": null, "items": []}
- Never include totals, discounts, or non-product lines
"""


async def extract_items_from_receipt(
    image_b64: str,
    mime_type: str,
    api_key: str,
) -> tuple[str, list[dict]]:
    """
    Send receipt image to Claude and extract structured item list.
    Returns (vendor_name, items_list).
    """
    # Determine media type for Anthropic API
    if "pdf" in mime_type:
        content_type = "application/pdf"
        source_type = "base64"
        media_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": content_type, "data": image_b64},
        }
    else:
        # Default to image
        if mime_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            mime_type = "image/jpeg"
        media_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime_type, "data": image_b64},
        }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 2000,
                "system": EXTRACT_SYSTEM,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            media_block,
                            {"type": "text", "text": "Parse this receipt."},
                        ],
                    }
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()

    raw = resp.json()["content"][0]["text"].strip()
    # Strip any accidental markdown fences
    raw = raw.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(raw)

    vendor = parsed.get("vendor") or "Unknown Vendor"
    items = parsed.get("items") or []
    return vendor, items


def build_comparison_report(
    vendor: str,
    new_items: list[dict],
    history: dict[str, list[dict]],
) -> str:
    """
    Compare newly scanned items against stored price history.
    Returns a human-readable WhatsApp message.
    """
    lines = [f"🧾 *{vendor}* receipt processed — {len(new_items)} items\n"]

    alerts = []       # Price anomalies
    comparisons = []  # Cross-vendor comparisons

    for item in new_items:
        key = normalize(item["name"])
        price = item["price"]
        unit = item.get("unit") or ""

        vendor_history = history.get(key, [])

        # --- Cross-vendor comparison ---
        other_vendors = [v for v in vendor_history if v["vendor"] != vendor]
        if other_vendors:
            for other in other_vendors:
                diff_pct = ((price - other["price"]) / other["price"]) * 100
                sign = "+" if diff_pct > 0 else ""
                emoji = "🔴" if abs(diff_pct) > 15 else "🟡" if abs(diff_pct) > 5 else "🟢"
                comparisons.append(
                    f"{emoji} *{item['name']}* {unit}\n"
                    f"   Here: ₪{price:.2f} | {other['vendor']}: ₪{other['price']:.2f} "
                    f"({sign}{diff_pct:.0f}%)"
                )

        # --- Price trend (same vendor) ---
        trend = get_item_trend(key, vendor, limit=3)
        # trend[0] is the JUST saved record, so look at [1] for previous
        if len(trend) >= 2:
            prev_price = trend[1]["price"]
            diff_pct = ((price - prev_price) / prev_price) * 100
            if abs(diff_pct) >= 5:
                sign = "+" if diff_pct > 0 else ""
                emoji = "📈" if diff_pct > 0 else "📉"
                alerts.append(
                    f"{emoji} *{item['name']}* price changed at {vendor}: "
                    f"₪{prev_price:.2f} → ₪{price:.2f} ({sign}{diff_pct:.0f}%)"
                )

    if alerts:
        lines.append("*⚠️ Price Changes (vs your last visit):*")
        lines.extend(alerts)
        lines.append("")

    if comparisons:
        lines.append("*🏪 Cross-Vendor Comparison:*")
        lines.extend(comparisons)
        lines.append("")

    if not alerts and not comparisons:
        lines.append("✅ No anomalies found. Prices look consistent with history.")
        lines.append("_(Send more receipts from other stores to enable comparisons!)_")

    lines.append(f"📊 Total items tracked in DB grows with each receipt you send.")
    return "\n".join(lines)
