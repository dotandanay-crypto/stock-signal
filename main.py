from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
import urllib.request
import urllib.parse
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Stock Market Signal Bot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8780786951")


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN:
        logger.warning("Telegram not configured")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }).encode()
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
        logger.info("Telegram message sent!")
        return True
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


async def get_fear_and_greed() -> dict:
    """Fetch CNN Fear & Greed index."""
    urls = [
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        "https://fear-and-greed-index.p.rapidapi.com/v1/fgi",
    ]
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                urls[0],
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                    "Referer": "https://www.cnn.com/markets/fear-and-greed",
                }
            )
            data = resp.json()
            score = float(data["fear_and_greed"]["score"])
            rating = data["fear_and_greed"]["rating"]
            return {"value": score, "rating": rating, "ok": True}
    except Exception as e:
        logger.error(f"Fear&Greed error: {e}")
        return {"value": None, "ok": False}


async def get_vix() -> dict:
    """Fetch VIX from Yahoo Finance."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=1d",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            data = resp.json()
            price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            return {"value": float(price), "ok": True}
    except Exception as e:
        logger.error(f"VIX error: {e}")
        return {"value": None, "ok": False}


async def get_s5fi() -> dict:
    """Fetch S5FI from Barchart."""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.barchart.com/stocks/quotes/%24S5FI",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                }
            )
            text = resp.text
            # Look for the price in the page
            import re
            # Pattern: last price in the page
            patterns = [
                r'"lastPrice":\s*([\d.]+)',
                r'"regularMarketPrice":\s*([\d.]+)',
                r'data-ng-value="([\d.]+)"',
            ]
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    value = float(match.group(1))
                    if 0 < value < 100:  # S5FI is always 0-100
                        return {"value": value, "ok": True}

            # Try Yahoo Finance with different symbol formats
            for symbol in ["S5FI", "%24S5FI", "SP500.FI"]:
                try:
                    r = await client.get(
                        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    data = r.json()
                    price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                    if 0 < float(price) < 100:
                        return {"value": float(price), "ok": True}
                except Exception:
                    continue

        return {"value": None, "ok": False}
    except Exception as e:
        logger.error(f"S5FI error: {e}")
        return {"value": None, "ok": False}


async def check_signals():
    logger.info("Checking market signals...")

    fg, vix, s5fi = await get_fear_and_greed(), await get_vix(), await get_s5fi()

    now = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Log current values
    logger.info(f"Fear&Greed: {fg}")
    logger.info(f"VIX: {vix}")
    logger.info(f"S5FI: {s5fi}")

    # Check if all data fetched successfully
    if not fg["ok"] or not vix["ok"] or not s5fi["ok"]:
        failed = []
        if not fg["ok"]: failed.append("Fear&Greed")
        if not vix["ok"]: failed.append("VIX")
        if not s5fi["ok"]: failed.append("S5FI")
        logger.warning(f"Failed to fetch: {failed}")
        return {
            "signal": False,
            "error": f"לא ניתן לקרוא: {', '.join(failed)}",
            "fear_greed": fg,
            "vix": vix,
            "s5fi": s5fi
        }

    # Check conditions
    cond_fg = fg["value"] < 11
    cond_vix = vix["value"] > 28
    cond_s5fi = s5fi["value"] < 20

    all_positive = cond_fg and cond_vix and cond_s5fi

    if all_positive:
        msg = (
            f"🚨 *הזדמנות קניה משמעותית של שוק המניות - הגבר חשיפה*\n\n"
            f"📊 *ערכים נוכחיים:*\n"
            f"• Fear & Greed: `{fg['value']:.1f}` ✅ (מתחת ל-11)\n"
            f"• VIX: `{vix['value']:.2f}` ✅ (מעל 28)\n"
            f"• S5FI: `{s5fi['value']:.1f}` ✅ (מתחת ל-20)\n\n"
            f"_{now}_"
        )
        send_telegram(msg)
        logger.info("Signal sent!")
    else:
        logger.info("No signal — conditions not met")

    return {
        "signal": all_positive,
        "timestamp": now,
        "fear_greed": {"value": fg["value"], "condition": cond_fg, "threshold": "< 11"},
        "vix": {"value": vix["value"], "condition": cond_vix, "threshold": "> 28"},
        "s5fi": {"value": s5fi["value"], "condition": cond_s5fi, "threshold": "< 20"},
    }


@app.get("/")
async def root():
    return {"status": "ok", "message": "Stock Market Signal Bot"}


@app.get("/check")
async def check():
    result = await check_signals()
    return result
