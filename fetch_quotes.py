import os
import json
import time
import urllib.request

API_KEY = os.environ["FINNHUB_API_KEY"]

# ticker: peso aproximado en QQQ (%) -- ajustar trimestralmente
HOLDINGS = {
    "QQQ":   None,   # el propio índice, sin peso
    "NVDA":  9.0,
    "AAPL":  7.5,
    "MSFT":  5.5,
    "AVGO":  5.0,
    "AMZN":  4.9,
    "META":  3.3,
    "GOOGL": 3.2,
    "TSLA":  2.7,
}

def get_quote(symbol):
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={API_KEY}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())

def main():
    out = {"asof": int(time.time()), "qqq": None, "components": []}

    for symbol, weight in HOLDINGS.items():
        q = get_quote(symbol)
        # Finnhub quote fields: c=current, d=change, dp=percent change
        price = q.get("c")
        chg = q.get("d")
        chg_pct = q.get("dp")

        if symbol == "QQQ":
            out["qqq"] = {"price": price, "chg": chg, "chg_pct": chg_pct}
        else:
            impact = round((weight * (chg_pct or 0)) / 100, 3) if chg_pct is not None else None
            out["components"].append({
                "t": symbol,
                "w": weight,
                "price": price,
                "chg_pct": chg_pct,
                "impact": impact,
            })
        time.sleep(1)  # cortesía con el rate limit gratuito (60/min)

    out["components"].sort(key=lambda x: abs(x["impact"] or 0), reverse=True)

    with open("data.json", "w") as f:
        json.dump(out, f, indent=2)

if __name__ == "__main__":
    main()
