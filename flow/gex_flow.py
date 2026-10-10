#!/usr/bin/env python3
"""
gex_flow.py - Niveles GEX y flujo de opciones ESTIMADO para SPY / QQQ.

Qué calcula
  * GEX por strike (Black-Scholes, convención dealer: calls +, puts -),
    Call Wall, Put Wall, Gamma Flip y GEX neto.
  * Flujo estimado: cambios de volumen por contrato entre snapshots, con el
    lado del agresor inferido comparando el último precio contra el punto medio
    bid/ask. NO es el tape real (ver tape_test.py): es una aproximación.
  * Técnicos del subyacente (VWAP, EMA 9/21/50, media de Bollinger, volumen rel.).

Qué escribe
  * flow/flow_<SIM>.json  -> lo lee flow.html
  * signals.json          -> lo lee scoring.html (GEX, agresor, Q Delta, técnicos)

Uso (en tu VPS o tu computadora, con: pip install yfinance)
  python3 flow/gex_flow.py --symbol SPY --loop 60
  python3 flow/gex_flow.py --symbol QQQ --loop 60 --git-push 300
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RATE = 0.045                 # tasa libre de riesgo aproximada
MIN_T_SECONDS = 1800         # piso de 30 min para 0DTE (evita gamma infinito)
BAND = 0.08                  # muros: solo strikes a +-8% del precio
PROFILE_BAND = 0.03          # perfil mostrado: +-3%
MAX_SERIES = 400


# ----------------------------------------------------------------- matemática
def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_greeks(spot, strike, t_years, iv, is_call, r=RATE):
    """Devuelve (delta, gamma) de Black-Scholes."""
    if spot <= 0 or strike <= 0 or iv <= 0 or t_years <= 0:
        return 0.0, 0.0
    sd = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / sd
    gamma = norm_pdf(d1) / (spot * sd)
    delta = norm_cdf(d1) if is_call else norm_cdf(d1) - 1
    return delta, gamma


def years_to_expiry(expiry, now):
    """expiry 'YYYY-MM-DD' (vence 16:00 ET). Piso de 30 min."""
    d = datetime.strptime(expiry, "%Y-%m-%d").replace(hour=16, tzinfo=ET)
    secs = max((d - now).total_seconds(), MIN_T_SECONDS)
    return secs / (365 * 24 * 3600)


def _gamma_dollars(c, spot):
    """GEX en USD por cada 1% de movimiento, con signo (calls +, puts -)."""
    _, gamma = bs_greeks(spot, c["strike"], c["t"], c["iv"], c["is_call"])
    g = gamma * c["oi"] * 100 * spot * spot * 0.01
    return g if c["is_call"] else -g


def usable(c):
    return c["oi"] > 0 and c["iv"] is not None and c["iv"] >= 0.01 and c["t"] > 0


def gex_by_strike(contracts, spot):
    out = {}
    for c in contracts:
        if not usable(c):
            continue
        row = out.setdefault(c["strike"], {"call": 0.0, "put": 0.0})
        row["call" if c["is_call"] else "put"] += _gamma_dollars(c, spot)
    return out


def net_gex_at(contracts, spot_hyp):
    return sum(_gamma_dollars(c, spot_hyp) for c in contracts if usable(c))


def gamma_flip(contracts, spot):
    """Precio donde el GEX neto cruza cero (el cruce más cercano al precio)."""
    if not any(usable(c) for c in contracts):
        return None
    pts = [spot * (1 + i / 400) for i in range(-40, 41)]  # +-10%, paso 0.25%
    vals = [net_gex_at(contracts, s) for s in pts]
    best = None
    for i in range(len(pts) - 1):
        a, b = vals[i], vals[i + 1]
        if a == 0 or a * b < 0:
            x = pts[i] if a == 0 else pts[i] + (pts[i + 1] - pts[i]) * (abs(a) / (abs(a) + abs(b)))
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return round(best, 2) if best is not None else None


def compute_levels(contracts, spot):
    by = gex_by_strike(contracts, spot)
    near = {k: v for k, v in by.items() if abs(k - spot) <= spot * BAND}
    call_wall = max(near, key=lambda k: near[k]["call"]) if near and any(v["call"] > 0 for v in near.values()) else None
    put_wall = min(near, key=lambda k: near[k]["put"]) if near and any(v["put"] < 0 for v in near.values()) else None
    net_total = sum(v["call"] + v["put"] for v in by.values())
    profile = [
        {"strike": k, "call": round(v["call"]), "put": round(v["put"])}
        for k, v in sorted(by.items()) if abs(k - spot) <= spot * PROFILE_BAND
    ]
    return {
        "call_wall": call_wall,
        "put_wall": put_wall,
        "gamma_flip": gamma_flip(contracts, spot),
        "net_gex_total": round(net_total),
        "profile": profile,
    }


# ---------------------------------------------------------------------- flujo
def flow_bucket(contracts, prev_vol, spot):
    """
    Flujo estimado entre dos snapshots.
    Para cada contrato con volumen nuevo: lado = último precio vs punto medio.
    dirección = lado * (+1 call / -1 put)  -> +alcista, -bajista.
    Devuelve (bucket, nuevo_prev_vol). El primer snapshot no produce flujo.
    """
    flow = total = qdelta = 0.0
    new_vol = {}
    for c in contracts:
        v = c["volume"] or 0
        new_vol[c["id"]] = v
        pv = prev_vol.get(c["id"])
        if pv is None:
            continue
        dv = v - pv
        last = c["last"]
        if dv <= 0 or not last or last <= 0:
            continue
        bid, ask = c["bid"], c["ask"]
        if not bid or not ask or bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / 2
        side = 1 if last > mid else (-1 if last < mid else 0)
        direction = side * (1 if c["is_call"] else -1)
        premium = dv * last * 100
        total += premium
        flow += direction * premium
        qdelta += direction * abs(c.get("delta") or 0) * dv * 100 * spot
    agg = max(-1.0, min(1.0, flow / total)) if total else 0.0
    return {"flow": flow, "total": total, "qdelta": qdelta, "agg": agg}, new_vol


# ------------------------------------------------------------------ técnicos
def ema(values, n):
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def technicals(bars, bb_tf=5, bb_len=20):
    """bars: velas de 1 min [{'close','high','low','volume'}]. Faltantes -> None."""
    out = {"vwap": None, "ema9": None, "ema21": None, "ema50": None, "bbMid": None, "relVol": None}
    if not bars:
        return out
    closes = [b["close"] for b in bars]
    vol = sum(b["volume"] for b in bars)
    if vol > 0:
        out["vwap"] = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars) / vol
    for n in (9, 21, 50):
        if len(closes) >= n:
            out["ema%d" % n] = ema(closes, n)
    tf_closes = [closes[i] for i in range(bb_tf - 1, len(closes), bb_tf)]
    if len(tf_closes) >= bb_len:
        out["bbMid"] = sum(tf_closes[-bb_len:]) / bb_len
    tf_vols = [sum(b["volume"] for b in bars[i - bb_tf + 1:i + 1]) for i in range(bb_tf - 1, len(bars), bb_tf)]
    if len(tf_vols) >= 4 and sum(tf_vols[:-1]) > 0:
        out["relVol"] = tf_vols[-1] / (sum(tf_vols[:-1]) / (len(tf_vols) - 1))
    return {k: (round(v, 3) if v is not None else None) for k, v in out.items()}


# ----------------------------------------------------------- proceso (puro)
def is_market_open(now_et):
    m = now_et.hour * 60 + now_et.minute
    return now_et.weekday() < 5 and 570 <= m < 960


def process(symbol, spot, contracts, bars, state, now_et, force=False, source="yfinance"):
    """Calcula todo a partir de datos ya descargados. Devuelve (flow_json, state, signals)."""
    today = now_et.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "prev_vol": {}, "series": [], "cum": 0.0}

    if is_market_open(now_et) or force:
        bucket, new_vol = flow_bucket(contracts, state["prev_vol"], spot)
        state["prev_vol"] = new_vol
        if state["series"] or state.get("seen_first"):
            state["cum"] += bucket["flow"]
            state["series"].append({
                "t": now_et.strftime("%H:%M"), "price": round(spot, 2),
                "flow": round(bucket["flow"]), "cum_flow": round(state["cum"]),
                "total": round(bucket["total"]), "qdelta": round(bucket["qdelta"]),
                "agg": round(bucket["agg"], 3),
            })
            state["series"] = state["series"][-MAX_SERIES:]
        state["seen_first"] = True  # el primer snapshot solo fija el volumen base

    levels = compute_levels(contracts, spot)
    out = {
        "symbol": symbol, "demo": False, "estimated": True, "source": source,
        "updated": now_et.isoformat(), "spot": round(spot, 2),
        "levels": levels, "series": state["series"],
    }

    s = state["series"]
    last5, prev5 = s[-5:], s[-10:-5]
    agresor = round(sum(b["agg"] for b in last5) / len(last5) * 100, 1) if last5 else None
    sums = lambda xs: round(sum(b["qdelta"] for b in xs)) if xs else None
    tech = technicals(bars)
    signals = {
        "vwap": tech["vwap"], "ema9": tech["ema9"], "ema21": tech["ema21"], "ema50": tech["ema50"],
        "bbMid": tech["bbMid"], "relVol": tech["relVol"],
        "gex": {
            "zeroGamma": levels["gamma_flip"], "callWall": levels["call_wall"],
            "putWall": levels["put_wall"], "netGex": levels["net_gex_total"],
        },
        "agresor": agresor,
        "qDelta": sums(s[-3:]), "qDeltaPrev": sums(s[-6:-3]),
        "spreadPct": None,   # depende del contrato elegido: se llena a mano
    }
    return out, state, signals


def merge_signals(path, symbol, signals, now_et):
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
    prev = data.get(symbol, {})
    for k, v in signals.items():
        if k == "gex":
            prev.setdefault("gex", {})
            prev["gex"].update(v)
        elif k == "spreadPct" and v is None:
            continue   # el spread es del contrato elegido; no pisar lo que haya
        else:
            prev[k] = v
    data[symbol] = prev
    data["asof"] = now_et.isoformat()
    return data


# ------------------------------------------------------------- yfinance / E/S
def _num(x, default=None):
    try:
        v = float(x)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def snapshot(symbol, n_expiries, now_et):
    import yfinance as yf  # import tardío: las pruebas no lo necesitan
    tk = yf.Ticker(symbol)
    hist = tk.history(period="1d", interval="1m")
    if hist is None or hist.empty:
        raise RuntimeError("sin velas de 1 min para " + symbol)
    bars = [{"close": float(r.Close), "high": float(r.High), "low": float(r.Low), "volume": float(r.Volume)}
            for r in hist.itertuples()]
    spot = bars[-1]["close"]
    contracts = []
    for exp in list(tk.options)[:n_expiries]:
        ch = tk.option_chain(exp)
        t = years_to_expiry(exp, now_et)
        for is_call, df in ((True, ch.calls), (False, ch.puts)):
            for r in df.to_dict("records"):
                strike = _num(r.get("strike"))
                if strike is None:
                    continue
                iv = _num(r.get("impliedVolatility"))
                delta, _ = bs_greeks(spot, strike, t, iv or 0, is_call)
                contracts.append({
                    "id": str(r.get("contractSymbol")), "strike": strike, "is_call": is_call,
                    "oi": _num(r.get("openInterest"), 0) or 0, "volume": _num(r.get("volume"), 0) or 0,
                    "last": _num(r.get("lastPrice")), "bid": _num(r.get("bid")), "ask": _num(r.get("ask")),
                    "iv": iv, "t": t, "delta": delta,
                })
    return spot, contracts, bars


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def git_push(paths, message):
    try:
        subprocess.run(["git", "-C", ROOT, "add", *paths], check=True)
        if subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode == 0:
            return
        subprocess.run(["git", "-C", ROOT, "commit", "-q", "-m", message], check=True)
        subprocess.run(["git", "-C", ROOT, "pull", "--rebase", "-q"], check=True)
        subprocess.run(["git", "-C", ROOT, "push", "-q"], check=True)
        print("  git push ok")
    except Exception as e:  # no detener el ciclo por un fallo de red
        print("  git push falló:", e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="SPY", choices=["SPY", "QQQ"])
    ap.add_argument("--expiries", type=int, default=2, help="vencimientos a incluir (2 = 0DTE + 1DTE)")
    ap.add_argument("--loop", type=int, default=0, help="segundos entre snapshots (0 = una vez)")
    ap.add_argument("--force", action="store_true", help="registrar flujo aunque el mercado esté cerrado")
    ap.add_argument("--signals", default=os.path.join(ROOT, "signals.json"), help="ruta de signals.json ('' = no escribir)")
    ap.add_argument("--git-push", type=int, default=0, metavar="SEG", help="commit+push cada SEG segundos (0 = no)")
    a = ap.parse_args()

    flow_path = os.path.join(HERE, "flow_%s.json" % a.symbol)
    state_path = os.path.join(HERE, "state_%s.json" % a.symbol)
    last_push = 0.0

    while True:
        now = datetime.now(ET)
        try:
            state = json.load(open(state_path)) if os.path.exists(state_path) else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        try:
            spot, contracts, bars = snapshot(a.symbol, a.expiries, now)
            out, state, signals = process(a.symbol, spot, contracts, bars, state, now, a.force)
            write_json(flow_path, out)
            write_json(state_path, state)
            if a.signals:
                write_json(a.signals, merge_signals(a.signals, a.symbol, signals, now))
            lv = out["levels"]
            print("[%s] %s spot=%s CW=%s PW=%s flip=%s buckets=%d" % (
                now.strftime("%H:%M:%S"), a.symbol, out["spot"], lv["call_wall"], lv["put_wall"],
                lv["gamma_flip"], len(out["series"])))
            if a.git_push and time.time() - last_push >= a.git_push:
                paths = [flow_path] + ([a.signals] if a.signals else [])
                git_push(paths, "Actualiza flujo %s %s" % (a.symbol, now.strftime("%H:%M")))
                last_push = time.time()
        except Exception as e:
            print("[%s] error: %s" % (now.strftime("%H:%M:%S"), e), file=sys.stderr)
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
