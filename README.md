# Receipt Price Tracker 🧾

Track grocery prices across vendors via WhatsApp. Send a receipt photo → get instant price comparisons.

## Setup (Railway — ~10 minutes)

### 1. Clone & deploy
```bash
# Fork/clone this repo, then in Railway:
# New Project → Deploy from GitHub → select this repo
```

### 2. Set environment variables in Railway dashboard
```
WASSENGER_API_KEY=your_wassenger_key
ANTHROPIC_API_KEY=your_anthropic_key
```

### 3. Set Wassenger webhook URL
In Wassenger dashboard → Webhooks → add your Railway URL:
```
https://your-app.railway.app/webhook
```
Events to enable: **Message Received**

### 4. Start sending receipts!
Send any grocery receipt photo to your WhatsApp number and you'll get:
- 📈 Price changes since your last visit to the same store
- 🏪 Cross-vendor price comparisons (once you have 2+ stores)
- ⚠️ Anomaly alerts when price diff > 15%

---

## Local development
```bash
pip install -r requirements.txt
export WASSENGER_API_KEY=...
export ANTHROPIC_API_KEY=...
uvicorn server:app --reload --port 8000
# Use ngrok to expose locally: ngrok http 8000
```

## How it works
1. Wassenger receives your WhatsApp image and POSTs to `/webhook`
2. Claude (vision) extracts every line item + price from the receipt
3. Items are stored in SQLite with vendor + timestamp
4. On each new receipt, Claude compares prices cross-vendor and flags changes
