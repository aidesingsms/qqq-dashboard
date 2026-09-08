import os
import json
import time
import urllib.request

API_KEY = os.environ["FINNHUB_API_KEY"]

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

def main():
    # símbolo único a consultar: índices + unión de todos los componentes
    all_symbols = set(INDEX_HOLDINGS.keys())
    for holdings in INDEX_HOLDINGS.values():
        all_symbols.update(holdings.keys())

    quotes = {}
    for symbol in sorted(all_symbols):
        quotes[symbol] = get_quote(symbol)
        time.sleep(1)  # cortesía con el rate limit gratuito (60/min)

    out = {"asof": int(time.time()), "indices": {}}

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
        }

    with open("data.json", "w") as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    main()
