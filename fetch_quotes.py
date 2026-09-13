import os
import json
import time
import math
import urllib.request

API_KEY = os.environ["FINNHUB_API_KEY"]
HISTORY_FILE = "history.json"
BB_PERIOD = 20      # periodos para SMA/stdev del Bollinger Bandwidth
MAX_HISTORY = 100    # tope de puntos guardados por símbolo (evita crecer sin control)

# pesos aproximados por índice (%) -- ajustar trimestralmente
INDEX_HOLDINGS = {
    "QQQ": {
        "NVDA": 9.0, "AAPL": 7.5, "MSFT": 5.5, "AVGO": 5.0,
        "AMZN": 4.9, "META": 3.3, "GOOGL": 3.2, "TSLA": 2.7,
    },
    "SPY": {
        "NVDA": 8.0, "AAPL": 7.0, "MSFT": 5.5, "AMZN": 3.9,
        "META": 2.9, "AVGO": 2.6, "GOOGL": 2.3, "GOOG": 1.8,
        "TSLA": 1.7, "JPM": 1.6,
    },
}

def get_quote(symbol):
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={API_KEY}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def update_history(history, symbol, price, ts):
    if price is None:
        return
    series = history.setdefault(symbol, [])
    series.append({"t": ts, "c": price})
    if len(series) > MAX_HISTORY:
        del series[: len(series) - MAX_HISTORY]

def bollinger_bandwidth(history, symbol, period=BB_PERIOD):
    series = history.get(symbol, [])
    if len(series) < period:
        return {"ready": False, "n": len(series), "period": period}
    closes = [p["c"] for p in series[-period:]]
    sma = sum(closes) / period
    variance = sum((c - sma) ** 2 for c in closes) / period
    stdev = math.sqrt(variance)
    upper = sma + 2 * stdev
    lower = sma - 2 * stdev
    if sma == 0:
        return None
    bandwidth_pct = ((upper - lower) / sma) * 100
    return {
        "ready": True,
        "sma": round(sma, 3),
        "upper": round(upper, 3),
        "lower": round(lower, 3),
        "bandwidth_pct": round(bandwidth_pct, 3),
        "n": len(closes),
        "period": period,
    }

def main():
    # símbolo único a consultar: índices + unión de todos los componentes
    all_symbols = set(INDEX_HOLDINGS.keys())
    for holdings in INDEX_HOLDINGS.values():
        all_symbols.update(holdings.keys())

    quotes = {}
    for symbol in sorted(all_symbols):
        quotes[symbol] = get_quote(symbol)
        time.sleep(1)  # cortesía con el rate limit gratuito (60/min)

    now = int(time.time())
    history = load_history()
    for index_symbol in INDEX_HOLDINGS.keys():
        update_history(history, index_symbol, quotes[index_symbol].get("c"), now)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

    out = {"asof": now, "indices": {}}

    for index_symbol, holdings in INDEX_HOLDINGS.items():
        q = quotes[index_symbol]
        components = []
        for symbol, weight in holdings.items():
            cq = quotes[symbol]
            chg_pct = cq.get("dp")
            impact = round((weight * (chg_pct or 0)) / 100, 3) if chg_pct is not None else None
            components.append({
                "t": symbol,
                "w": weight,
                "price": cq.get("c"),
                "chg_pct": chg_pct,
                "impact": impact,
            })
        components.sort(key=lambda x: abs(x["impact"] or 0), reverse=True)

        out["indices"][index_symbol] = {
            "price": q.get("c"),
            "chg": q.get("d"),
            "chg_pct": q.get("dp"),
            "components": components,
            "bollinger": bollinger_bandwidth(history, index_symbol),
        }

    with open("data.json", "w") as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    main()
