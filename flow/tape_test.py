#!/usr/bin/env python3
"""
tape_test.py - ¿Tu fuente de datos entrega el TAPE real de opciones?

Para clasificar al agresor de verdad necesitas, por cada operación: precio, tamaño y el
bid/ask vigente en ese instante. Esta prueba verifica eso con tu fuente.

Uso
  python3 flow/tape_test.py selftest
  python3 flow/tape_test.py ibkr    --symbol SPY --expiry 20261016 --strike 780 --right C [--port 7497]
  python3 flow/tape_test.py polygon --symbol SPY --expiry 2026-10-16 --strike 780 --right C --key TU_API_KEY

Requisitos: ibkr -> pip install ib_insync (TWS/Gateway abierto y suscripción OPRA)
            polygon -> solo Python estándar (plan con trades y quotes de opciones)
Elige un strike cercano al precio actual y un vencimiento activo.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


# -------------------------------------------------------------- lógica pura
def classify(price, bid, ask):
    """+1 compra agresiva (al ask o por encima), -1 venta agresiva (al bid o por debajo), 0 ambigua."""
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        return 0
    if price >= ask:
        return 1
    if price <= bid:
        return -1
    mid = (bid + ask) / 2
    if price > mid:
        return 1
    if price < mid:
        return -1
    return 0


def match_quotes(trades, quotes):
    """
    Asocia cada trade con la última cotización a su mismo instante o antes (as-of join).
    trades: [{'ts','price','size'}]; quotes: [{'ts','bid','ask'}] (ts comparables).
    Devuelve [{'ts','price','size','bid','ask','side'}]; side=0 si no hay cotización previa.
    """
    qs = sorted(quotes, key=lambda q: q["ts"])
    out, j, cur = [], 0, None
    for t in sorted(trades, key=lambda t: t["ts"]):
        while j < len(qs) and qs[j]["ts"] <= t["ts"]:
            cur = qs[j]
            j += 1
        if cur is None:
            out.append({**t, "bid": None, "ask": None, "side": 0})
        else:
            out.append({**t, "bid": cur["bid"], "ask": cur["ask"], "side": classify(t["price"], cur["bid"], cur["ask"])})
    return out


def summarize(matched):
    n = len(matched)
    buy = sum(m["size"] for m in matched if m["side"] == 1)
    sell = sum(m["size"] for m in matched if m["side"] == -1)
    amb = sum(m["size"] for m in matched if m["side"] == 0)
    tot = buy + sell + amb
    return {"trades": n, "contratos_compra": buy, "contratos_venta": sell, "contratos_ambiguos": amb,
            "pct_clasificado": round(100 * (buy + sell) / tot, 1) if tot else 0.0}


def option_ticker(symbol, expiry, right, strike):
    """Formato OCC con prefijo de Polygon: O:SPY261016C00780000 (expiry 'YYYY-MM-DD')."""
    yy, mm, dd = expiry[2:4], expiry[5:7], expiry[8:10]
    return "O:%s%s%s%s%s%08d" % (symbol, yy, mm, dd, right.upper(), round(float(strike) * 1000))


def verdict(trades, quotes):
    if not trades:
        return "FALLA: sin trades. Tu fuente no entrega el tape (o no tienes la suscripción)."
    if not quotes:
        return "PARCIAL: hay trades pero no cotizaciones: no puedes clasificar al agresor."
    s = summarize(match_quotes(trades, quotes))
    if s["pct_clasificado"] >= 80:
        return "OK: tape utilizable (%s%% del volumen clasificado)." % s["pct_clasificado"]
    return "PARCIAL: solo %s%% del volumen se pudo clasificar." % s["pct_clasificado"]


# ------------------------------------------------------------------ fuentes
def run_polygon(a):
    ticker = option_ticker(a.symbol, a.expiry, a.right, a.strike)
    print("Contrato:", ticker)
    res = {}
    failed = False
    for kind in ("trades", "quotes"):
        url = "https://api.polygon.io/v3/%s/%s?limit=200&order=desc&apiKey=%s" % (kind, ticker, a.key)
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                res[kind] = json.loads(r.read().decode()).get("results", [])
            print("  %s: HTTP 200, %d registros" % (kind, len(res[kind])))
        except urllib.error.HTTPError as e:
            failed = True
            print("  %s: HTTP %s (%s)" % (kind, e.code, e.read().decode()[:120]))
            res[kind] = []
        except Exception as e:
            failed = True
            print("  %s: error de red: %s" % (kind, e))
            res[kind] = []
    if failed and not (res["trades"] and res["quotes"]):
        print("INCONCLUSO: no se pudo consultar. HTTP 401/403 = clave inválida o tu plan no incluye "
              "trades/quotes de opciones; un error de red = revisa tu conexión. No significa que el tape no exista.")
        return
    trades = [{"ts": t.get("sip_timestamp"), "price": t.get("price"), "size": t.get("size", 0)} for t in res["trades"] if t.get("sip_timestamp")]
    quotes = [{"ts": q.get("sip_timestamp"), "bid": q.get("bid_price"), "ask": q.get("ask_price")} for q in res["quotes"] if q.get("sip_timestamp")]
    print(verdict(trades, quotes))


def run_ibkr(a):
    try:
        from ib_insync import IB, Option
    except ImportError:
        sys.exit("Falta ib_insync: pip install ib_insync")
    ib = IB()
    errs = []
    ib.errorEvent += lambda reqId, code, msg, c=None: errs.append((code, msg))
    ib.connect(a.host, a.port, clientId=a.client_id, timeout=10)
    c = Option(a.symbol, a.expiry, float(a.strike), a.right.upper(), "SMART", currency="USD")
    if not ib.qualifyContracts(c):
        sys.exit("Contrato no encontrado: revisa vencimiento/strike")
    print("Contrato:", c.localSymbol)
    t_last = ib.reqTickByTickData(c, "AllLast")
    t_ba = ib.reqTickByTickData(c, "BidAsk")
    print("Escuchando %d s…" % a.seconds)
    ib.sleep(a.seconds)
    trades = [{"ts": x.time.timestamp(), "price": x.price, "size": x.size} for x in (t_last.tickByTicks or [])]
    quotes = [{"ts": x.time.timestamp(), "bid": x.bidPrice, "ask": x.askPrice} for x in (t_ba.tickByTicks or [])]
    print("  trades:", len(trades), "| cotizaciones:", len(quotes))
    for code, msg in errs[-5:]:
        print("  IBKR %s: %s" % (code, msg))
    if any(code in (354, 10089, 10167) for code, _ in errs):
        print("  Pista: falta la suscripción de datos de opciones (OPRA).")
    print(verdict(trades, quotes))
    ib.disconnect()


def selftest():
    # trades y cotizaciones sintéticos con resultado conocido
    quotes = [{"ts": 1, "bid": 1.00, "ask": 1.10}, {"ts": 10, "bid": 1.20, "ask": 1.30}]
    trades = [
        {"ts": 2, "price": 1.10, "size": 10},    # al ask  -> compra
        {"ts": 3, "price": 1.00, "size": 5},     # al bid  -> venta
        {"ts": 4, "price": 1.05, "size": 7},     # en el medio exacto -> ambigua
        {"ts": 11, "price": 1.28, "size": 20},   # sobre el medio de la 2.ª cotización -> compra
        {"ts": 0, "price": 1.10, "size": 3},     # antes de cualquier cotización -> ambigua
    ]
    m = match_quotes(trades, quotes)
    s = summarize(m)
    assert [x["side"] for x in m] == [0, 1, -1, 0, 1], [x["side"] for x in m]
    assert s["contratos_compra"] == 30 and s["contratos_venta"] == 5 and s["contratos_ambiguos"] == 10, s
    assert classify(1.2, 0, 1.3) == 0 and classify(1.2, 1.3, 1.1) == 0
    assert option_ticker("SPY", "2026-10-16", "c", 780) == "O:SPY261016C00780000"
    assert option_ticker("QQQ", "2026-10-16", "P", 751.5) == "O:QQQ261016P00751500"
    assert verdict([], []).startswith("FALLA") and verdict(trades, []).startswith("PARCIAL")
    print("OK: autotest de clasificación pasó", s)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    for name in ("ibkr", "polygon"):
        p = sub.add_parser(name)
        p.add_argument("--symbol", default="SPY")
        p.add_argument("--expiry", required=True, help="ibkr: YYYYMMDD · polygon: YYYY-MM-DD")
        p.add_argument("--strike", required=True, type=float)
        p.add_argument("--right", default="C", choices=["C", "P", "c", "p"])
        if name == "ibkr":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=7497, help="TWS paper 7497 · live 7496 · Gateway 4002/4001")
            p.add_argument("--client-id", type=int, default=77)
            p.add_argument("--seconds", type=int, default=30)
        else:
            p.add_argument("--key", required=True)
    a = ap.parse_args()
    {"selftest": lambda: selftest(), "ibkr": lambda: run_ibkr(a), "polygon": lambda: run_polygon(a)}[a.cmd]()


if __name__ == "__main__":
    main()
