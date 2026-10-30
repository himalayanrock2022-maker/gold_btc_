"""Run on Windows where Exness MT5 terminal is installed and logged in."""
from __future__ import annotations
import os, time, logging, requests
from datetime import datetime, timezone
import MetaTrader5 as mt5

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hima-mt5-bridge")
SERVER_URL = os.environ["SIGNAL_SERVER_URL"].rstrip("/")
BRIDGE_TOKEN = os.environ["BRIDGE_TOKEN"]
INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "900"))
TIMEFRAMES = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
# Set exact names from Exness Market Watch. Common variants are only examples.
SYMBOLS = {
    "XAUUSD": os.getenv("EXNESS_GOLD_SYMBOL", "XAUUSD"),
    "USOIL": os.getenv("EXNESS_OIL_SYMBOL", "USOIL"),
    "ETHUSD": os.getenv("EXNESS_ETH_SYMBOL", "ETHUSD"),
}


def connect():
    terminal_path = os.getenv("MT5_TERMINAL_PATH")
    kwargs = {"path": terminal_path} if terminal_path else {}
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    login = os.getenv("EXNESS_LOGIN")
    password = os.getenv("EXNESS_PASSWORD")
    server = os.getenv("EXNESS_SERVER")
    if login and password and server:
        if not mt5.login(int(login), password=password, server=server):
            raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")
    info = mt5.account_info()
    if info is None:
        raise RuntimeError("No MT5 account session. Log in to Exness terminal first.")
    log.info("Connected to MT5 login=%s server=%s", info.login, info.server)


def fetch_symbol(symbol: str) -> dict:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol {symbol} not found. Check exact Exness Market Watch name.")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Cannot select symbol {symbol}: {mt5.last_error()}")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.bid <= 0 or tick.ask <= 0:
        raise RuntimeError(f"No valid tick for {symbol}")
    bars = {}
    for tf_name, tf_code in TIMEFRAMES.items():
        # Start at position 1: exclude the still-forming candle.
        rates = mt5.copy_rates_from_pos(symbol, tf_code, 1, 300)
        if rates is None or len(rates) < 220:
            raise RuntimeError(f"Insufficient {tf_name} history for {symbol}: {mt5.last_error()}")
        bars[tf_name] = [{
            "time": datetime.fromtimestamp(int(r["time"]), timezone.utc).isoformat(),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "tick_volume": int(r["tick_volume"]),
        } for r in rates]
    return {"bars": bars, "tick": {"bid": float(tick.bid), "ask": float(tick.ask), "time": int(tick.time), "digits": int(info.digits)}}


def scan_once():
    markets, errors = {}, {}
    for label, broker_symbol in SYMBOLS.items():
        try:
            markets[label] = fetch_symbol(broker_symbol)
        except Exception as exc:
            log.exception("Fetch failed for %s", label)
            errors[label] = str(exc)
    if not markets:
        raise RuntimeError(f"No markets fetched: {errors}")
    payload = {"schema": "hima.mt5.v1", "sent_at": datetime.now(timezone.utc).isoformat(), "markets": markets, "fetch_errors": errors}
    r = requests.post(f"{SERVER_URL}/ingest", json=payload, headers={"X-Bridge-Token": BRIDGE_TOKEN}, timeout=60)
    r.raise_for_status()
    log.info("Server response: %s", r.text[:2000])


if __name__ == "__main__":
    connect()
    try:
        while True:
            started = time.time()
            try:
                scan_once()
            except Exception:
                log.exception("Scan failed; will retry next cycle")
            time.sleep(max(10, INTERVAL_SECONDS - (time.time() - started)))
    finally:
        mt5.shutdown()
