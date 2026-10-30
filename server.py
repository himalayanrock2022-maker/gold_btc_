from __future__ import annotations
import os, json, sqlite3, logging, hashlib
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
import requests
from engine import make_signal

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("hima-research")
app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "/var/data/hima_signals.sqlite")
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
TE_API_KEY = os.getenv("TE_API_KEY", "")
REQUIRE_NEWS_CHECK = os.getenv("REQUIRE_NEWS_CHECK", "true").lower() == "true"
CFG = {k: os.getenv(k, d) for k, d in {
    "MIN_SCORE": "8", "SL_ATR": "1.5", "MAX_SPREAD_ATR": "0.18"
}.items()}


def db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.execute("CREATE TABLE IF NOT EXISTS sent (fingerprint TEXT PRIMARY KEY, symbol TEXT, side TEXT, bar_time TEXT, sent_at TEXT)")
    return con


def news_status() -> tuple[bool, str]:
    """Trading Economics calendar check; fail closed when configured as required."""
    if not TE_API_KEY:
        return (not REQUIRE_NEWS_CHECK, "News API key missing" if REQUIRE_NEWS_CHECK else "News filter disabled")
    now = datetime.now(timezone.utc)
    start, end = (now - timedelta(minutes=30)).date(), (now + timedelta(minutes=30)).date()
    # Official country-and-date endpoint; API key is supplied as the c parameter.
    try:
        r = requests.get(f"https://api.tradingeconomics.com/calendar/country/united%20states/{start}/{end}", params={"c": TE_API_KEY}, timeout=12)
        r.raise_for_status()
        events = r.json()
        for e in events if isinstance(events, list) else []:
            imp = str(e.get("Importance", "")).lower()
            country = str(e.get("Country", "")).lower()
            raw = e.get("Date") or e.get("date")
            if "united states" not in country or imp not in ("3", "high") or not raw:
                continue
            when = pd_timestamp(raw)
            if when and abs((when - now).total_seconds()) <= 30*60:
                return False, f"High-impact US event near: {e.get('Event', 'US event')}"
        return True, "No high-impact US event within +/-30 minutes"
    except Exception as exc:
        log.warning("News calendar unavailable: %s", exc)
        return (not REQUIRE_NEWS_CHECK, "News calendar unavailable; fail-closed policy applied" if REQUIRE_NEWS_CHECK else "News calendar unavailable")


def pd_timestamp(value):
    try:
        from dateutil.parser import isoparse
        dt = isoparse(str(value))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception:
        return None


def send_telegram(text: str):
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError("BOT_TOKEN and CHANNEL_ID must be configured")
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
        "chat_id": CHANNEL_ID, "text": text, "disable_web_page_preview": True
    }, timeout=15)
    r.raise_for_status()
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API rejected message: {body}")


def fmt_signal(s: dict, news_note: str) -> str:
    side_icon = "🟢" if s["side"] == "BUY" else "🔴"
    return (
        f"{side_icon} HIMA RESEARCH | {s['side']} {s['symbol']}\n\n"
        f"Entry: {s['entry']}\nSL: {s['sl']}\n"
        f"TP1: {s['tp1']}\nTP2: {s['tp2']}\nTP3: {s['tp3']}\n\n"
        f"Confluence: {s['score_label']}\n"
        f"H4/H1: {s['trend_h4']}/{s['trend_h1']}\n"
        f"M15 RSI: {s['rsi_m15']:.1f} | ADX: {s['adx_m15']:.1f}\n"
        f"Spread: {s['spread']} | ATR: {s['atr_m15']}\n"
        f"Closed M15 candle: {s['bar_time']}\n\n"
        f"Research checks:\n- " + "\n- ".join(s["reasons"]) +
        f"\n- News: {news_note}\n\n"
        "⚠️ Research alert only, not financial advice. Verify spread, contract size and execution on Exness before trading."
    )


@app.get("/")
def health():
    return jsonify({"service": "Hima Gold Research Bot", "status": "ok", "mode": "signals_only"})


@app.post("/ingest")
def ingest():
    if not BRIDGE_TOKEN or request.headers.get("X-Bridge-Token") != BRIDGE_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    if payload.get("schema") != "hima.mt5.v1" or not isinstance(payload.get("markets"), dict):
        return jsonify({"error": "invalid payload schema"}), 400
    news_ok, news_note = news_status()
    output = []
    for symbol, market in payload["markets"].items():
        try:
            bars = market["bars"]
            tick = market["tick"]
            signal = make_signal(symbol, bars, tick, CFG)
            if not signal:
                output.append({"symbol": symbol, "status": "no_setup"})
                continue
            if not news_ok:
                output.append({"symbol": symbol, "status": "blocked_by_news", "reason": news_note})
                continue
            fp_src = f"{symbol}|{signal['side']}|{signal['bar_time']}"
            fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()
            con = db()
            try:
                con.execute("INSERT INTO sent VALUES (?, ?, ?, ?, ?)", (fingerprint, symbol, signal["side"], signal["bar_time"], datetime.now(timezone.utc).isoformat()))
                con.commit()
            except sqlite3.IntegrityError:
                output.append({"symbol": symbol, "status": "duplicate_suppressed"})
                continue
            finally:
                con.close()
            try:
                send_telegram(fmt_signal(signal, news_note))
                output.append({"symbol": symbol, "status": "sent", "side": signal["side"], "score": signal["score"]})
            except Exception:
                # Remove dedupe row so next bridge push can retry delivery.
                con = db(); con.execute("DELETE FROM sent WHERE fingerprint=?", (fingerprint,)); con.commit(); con.close()
                raise
        except Exception as exc:
            log.exception("Analysis failed for %s", symbol)
            output.append({"symbol": symbol, "status": "error", "error": str(exc)})
    return jsonify({"received_at": datetime.now(timezone.utc).isoformat(), "news": news_note, "results": output})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
