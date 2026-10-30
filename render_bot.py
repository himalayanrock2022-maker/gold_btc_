"""
Gold + US Oil + ETH - Big Swing Bot for Render
Channel: Hima_GoldCripto_Signals (-1003953683540)
Logic: Support bounce + 80%+ confidence + Big targets (like 2650 -> 2700)
"""

import os
import time
import requests
from flask import Flask
import threading

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Render ENV me daalna
CHANNEL_ID = "-1003953683540"
# For testing you can also use: CHAT_ID = "9019682054"

app = Flask(__name__)

# Trading pairs - yfinance symbols
SYMBOLS = {
    "XAU/USD": "GC=F",      # Gold Futures ~ XAUUSD
    "USOIL": "CL=F",        # WTI Crude Oil
    "ETH/USD": "ETH-USD",   # Ethereum
}

# Targets like user said: 2650 -> 2700 style
TARGETS = {
    "XAU/USD": {"min_pips": 5000, "min_dollars": 50, "support": 3936, "pivot": 4042, "resistance": 4092},
    "USOIL": {"min_pips": 500, "min_dollars": 5, "support": 68, "pivot": 72, "resistance": 76},
    "ETH/USD": {"min_pips": 2000, "min_dollars": 200, "support": 3500, "pivot": 3850, "resistance": 4100},
}

def calculate_confidence(price, support, pivot, resistance, rsi_sim=55, trend="up"):
    """Simple confidence score 0-100 based on your chart logic"""
    score = 50
    # Near support = higher buy confidence
    if price <= pivot and price >= support:
        score += 20
    if price <= support + (pivot-support)*0.2:  # Very close to support like your 4024 entry near 3954
        score += 15
    # Trend up
    if trend == "up":
        score += 10
    # RSI mock (in real bot replace with real RSI)
    if 40 < rsi_sim < 70:
        score += 10
    # Avoid resistance
    if price < resistance * 0.98:
        score += 5
    return min(score, 95)

def get_price(symbol_yf):
    try:
        # Simple price fetch using Yahoo
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_yf}?interval=15m&range=1d"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        price = data['chart']['result'][0]['meta']['regularMarketPrice']
        return float(price)
    except Exception as e:
        print(f"Price fetch error {symbol_yf}: {e}")
        return None

def send_signal(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Sent: {r.json()}")
    except Exception as e:
        print(f"Send error: {e}")

def check_and_signal():
    while True:
        for name, yf_symbol in SYMBOLS.items():
            price = get_price(yf_symbol)
            if not price:
                continue
            
            cfg = TARGETS[name]
            conf = calculate_confidence(price, cfg["support"], cfg["pivot"], cfg["resistance"])
            
            # ONLY send if 80%+ and near support (like your chart marking)
            if conf >= 80 and price <= cfg["pivot"] + 20:
                # Calculate big target like 2650 -> 2700
                if "XAU" in name:
                    tp1 = cfg["pivot"] + 30  # 4073 jaisa
                    tp2 = cfg["resistance"]  # 4092 jaisa - tumhara chart wala
                    tp_big = price + cfg["min_dollars"]  # 2700 wala logic
                elif "OIL" in name:
                    tp1 = price + 2
                    tp2 = price + cfg["min_dollars"]
                    tp_big = tp2
                else: # ETH
                    tp1 = price + 100
                    tp2 = price + cfg["min_dollars"]
                    tp_big = tp2

                msg = f"""📈 **STRONG BUY - {name}**
Jaise aap ne chart pe mark kiya tha

Entry: {price:.2f} (Support zone {cfg['support']} ke qareeb)
SL: {cfg['support']:.2f}
TP1: {tp1:.2f}
TP2: {tp2:.2f} (Resistance {cfg['resistance']})
BIG TP: {tp_big:.2f} (50$+ move jaise 2650->2700)

Confidence: {conf}%
Timeframe: 15m (aap wala)
Pivot: {cfg['pivot']}

⚠️ Demo signal - Real trade se pehle khud check karen
"""
                send_signal(msg)
        
        # Check every 15 min like your chart
        time.sleep(900)

@app.route("/")
def home():
    return "Gold + Oil + ETH Bot Running - Hima_GoldCripto_Signals | 80%+ filter ON"

@app.route("/test")
def test_signal():
    send_signal("✅ Test Signal: Bot is connected to -1003953683540\nTumhari chart jaisi entry ready hai!")
    return "Test sent"

if __name__ == "__main__":
    # Start background checker
    t = threading.Thread(target=check_and_signal, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
