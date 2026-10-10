"""Pruebas de flow/gex_flow.py (sin red). Ejecutar: python3 tests/test_flow.py"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "flow"))
import gex_flow as g  # noqa: E402

ET = g.ET
T2D = 2 / 365  # ~2 días


def contract(strike, is_call, oi=0, volume=0, last=1.0, bid=0.9, ask=1.1, iv=0.2, t=T2D, spot=778, cid=None):
    delta, _ = g.bs_greeks(spot, strike, t, iv, is_call)
    return {"id": cid or ("%s%s" % ("C" if is_call else "P", strike)), "strike": strike, "is_call": is_call,
            "oi": oi, "volume": volume, "last": last, "bid": bid, "ask": ask, "iv": iv, "t": t, "delta": delta}


class Math(unittest.TestCase):
    def test_greeks_sane(self):
        dc, gc = g.bs_greeks(100, 100, 0.1, 0.2, True)
        dp, gp = g.bs_greeks(100, 100, 0.1, 0.2, False)
        self.assertAlmostEqual(dc, 0.53, delta=0.03)
        self.assertAlmostEqual(dc - dp, 1.0, places=6)      # paridad put-call de delta
        self.assertAlmostEqual(gc, gp, places=9)
        self.assertGreater(gc, 0)
        self.assertEqual(g.bs_greeks(100, 100, 0, 0.2, True), (0.0, 0.0))

    def test_years_floor(self):
        now = datetime(2026, 10, 12, 15, 50, tzinfo=ET)
        t = g.years_to_expiry("2026-10-12", now)       # faltan 10 min -> piso de 30 min
        self.assertAlmostEqual(t * 365 * 24 * 3600, g.MIN_T_SECONDS, delta=1)


class Gex(unittest.TestCase):
    def setUp(self):
        self.spot = 778.0
        self.cs = [
            contract(790, True, oi=5000), contract(785, True, oi=1000), contract(770, False, oi=1500),
            contract(775, False, oi=5000), contract(780, True, oi=800),
        ]

    def test_signs_and_walls(self):
        by = g.gex_by_strike(self.cs, self.spot)
        self.assertGreater(by[790]["call"], 0)
        self.assertLess(by[775]["put"], 0)
        lv = g.compute_levels(self.cs, self.spot)
        self.assertEqual(lv["call_wall"], 790)
        self.assertEqual(lv["put_wall"], 775)

    def test_flip_is_a_zero_of_net_gex(self):
        cs = [contract(790, True, oi=5000), contract(770, False, oi=5000)]
        flip = g.gamma_flip(cs, 778)
        self.assertIsNotNone(flip)
        self.assertAlmostEqual(flip, 780, delta=1.0)
        tot = abs(g.net_gex_at(cs, 790)) + abs(g.net_gex_at(cs, 770))
        self.assertLess(abs(g.net_gex_at(cs, flip)), tot * 0.05)

    def test_no_flip_when_one_sided(self):
        self.assertIsNone(g.gamma_flip([contract(790, True, oi=5000)], 778))
        self.assertIsNone(g.gamma_flip([], 778))

    def test_unusable_contracts_ignored(self):
        cs = [contract(790, True, oi=0), contract(790, True, oi=100, iv=0.0)]
        self.assertEqual(g.gex_by_strike(cs, 778), {})
        lv = g.compute_levels(cs, 778)
        self.assertIsNone(lv["call_wall"])
        self.assertIsNone(lv["put_wall"])


class Flow(unittest.TestCase):
    def test_directions(self):
        spot = 778
        prev = {"C780": 100, "P776": 100, "C781": 100, "P775": 100}
        cs = [
            contract(780, True, volume=110, last=1.10, bid=0.9, ask=1.1, cid="C780"),    # compra call  -> +
            contract(776, False, volume=110, last=1.10, bid=0.9, ask=1.1, cid="P776"),   # compra put   -> -
            contract(781, True, volume=110, last=0.90, bid=0.9, ask=1.1, cid="C781"),    # venta call   -> -
            contract(775, False, volume=110, last=0.90, bid=0.9, ask=1.1, cid="P775"),   # venta put    -> +
        ]
        b, new = g.flow_bucket(cs, prev, spot)
        prem = 10 * 1.0 * 100
        self.assertAlmostEqual(b["total"], 10 * 100 * (1.1 + 1.1 + 0.9 + 0.9))
        self.assertAlmostEqual(b["flow"], prem * (1.1 - 1.1 - 0.9 + 0.9))
        self.assertEqual(new["C780"], 110)

    def test_first_snapshot_and_edge_cases(self):
        cs = [contract(780, True, volume=500, last=1.1, bid=0.9, ask=1.1, cid="X")]
        b, new = g.flow_bucket(cs, {}, 778)              # sin snapshot previo: sin flujo
        self.assertEqual((b["flow"], b["total"]), (0, 0))
        self.assertEqual(new["X"], 500)
        b, _ = g.flow_bucket(cs, {"X": 500}, 778)        # sin volumen nuevo
        self.assertEqual(b["total"], 0)
        cs2 = [contract(780, True, volume=510, last=1.0, bid=0.9, ask=1.1, cid="X")]   # last == mid
        b, _ = g.flow_bucket(cs2, {"X": 500}, 778)
        self.assertEqual(b["flow"], 0)
        self.assertGreater(b["total"], 0)
        cs3 = [contract(780, True, volume=510, last=1.0, bid=0, ask=0, cid="X")]      # sin cotización
        self.assertEqual(g.flow_bucket(cs3, {"X": 500}, 778)[0]["total"], 0)

    def test_agg_bounded(self):
        cs = [contract(780, True, volume=900, last=1.1, bid=0.9, ask=1.1, cid="X")]
        b, _ = g.flow_bucket(cs, {"X": 100}, 778)
        self.assertEqual(b["agg"], 1.0)


class Technicals(unittest.TestCase):
    def bars(self, n, start=100.0, step=0.1, vol=1000):
        out = []
        for i in range(n):
            c = start + i * step
            out.append({"close": c, "high": c + 0.05, "low": c - 0.05, "volume": vol})
        return out

    def test_uptrend(self):
        t = g.technicals(self.bars(120))
        self.assertGreater(t["ema9"], t["ema21"])
        self.assertGreater(t["ema21"], t["ema50"])
        self.assertIsNotNone(t["vwap"])
        self.assertIsNotNone(t["bbMid"])
        self.assertAlmostEqual(t["relVol"], 1.0, places=3)

    def test_insufficient_data_is_none(self):
        t = g.technicals(self.bars(30))
        self.assertIsNotNone(t["ema9"])
        self.assertIsNone(t["ema50"])
        self.assertIsNone(t["bbMid"])
        self.assertEqual(g.technicals([])["vwap"], None)

    def test_relvol_spike(self):
        bars = self.bars(100)
        for b in bars[-5:]:
            b["volume"] = 3000
        self.assertAlmostEqual(g.technicals(bars)["relVol"], 3.0, delta=0.2)


class Process(unittest.TestCase):
    def run_cycle(self, state, now, vol):
        cs = [contract(790, True, oi=5000, volume=vol, last=1.1, bid=0.9, ask=1.1, cid="C790"),
              contract(770, False, oi=5000, volume=vol, last=0.9, bid=0.9, ask=1.1, cid="P770")]
        return g.process("SPY", 778.0, cs, Technicals().bars(120), state, now)

    def test_cycle_state_and_signals(self):
        t0 = datetime(2026, 10, 12, 10, 0, tzinfo=ET)       # lunes, mercado abierto
        out, st, sig = self.run_cycle({}, t0, 100)
        self.assertEqual(out["series"], [])                  # primer snapshot: solo base
        self.assertIsNone(sig["agresor"])
        out, st, sig = self.run_cycle(st, datetime(2026, 10, 12, 10, 1, tzinfo=ET), 150)
        self.assertEqual(len(out["series"]), 1)
        b = out["series"][0]
        self.assertGreater(b["total"], 0)
        self.assertEqual(sig["gex"]["callWall"], 790)
        self.assertEqual(sig["gex"]["putWall"], 770)
        self.assertIsNotNone(sig["gex"]["zeroGamma"])
        self.assertIsNotNone(sig["ema9"])
        self.assertTrue(out["estimated"] and not out["demo"])
        # compra de call a ask y venta de put al bid: ambas alcistas
        self.assertGreater(b["flow"], 0)
        self.assertEqual(sig["agresor"], 100.0)

    def test_closed_market_adds_no_bucket_and_new_day_resets(self):
        sat = datetime(2026, 10, 10, 18, 0, tzinfo=ET)
        out, st, _ = self.run_cycle({}, sat, 100)
        out, st, _ = self.run_cycle(st, sat, 200)
        self.assertEqual(out["series"], [])
        st = {"date": "2026-10-09", "prev_vol": {"C790": 1}, "series": [{"t": "10:00"}], "cum": 5.0}
        out, st, _ = self.run_cycle(st, sat, 100)
        self.assertEqual(st["date"], "2026-10-10")
        self.assertEqual(st["series"], [])

    def test_merge_signals_keeps_other_symbol_and_spread(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "signals.json")
            with open(p, "w") as f:
                json.dump({"asof": None, "QQQ": {"vwap": 1, "spreadPct": 4}, "SPY": {"spreadPct": 3}}, f)
            now = datetime(2026, 10, 12, 10, 0, tzinfo=ET)
            sig = {"vwap": 778.1, "gex": {"zeroGamma": 780}, "agresor": 12.0, "spreadPct": None}
            data = g.merge_signals(p, "SPY", sig, now)
            self.assertEqual(data["QQQ"], {"vwap": 1, "spreadPct": 4})
            self.assertEqual(data["SPY"]["spreadPct"], 3)
            self.assertEqual(data["SPY"]["vwap"], 778.1)
            self.assertEqual(data["SPY"]["gex"]["zeroGamma"], 780)
            self.assertEqual(data["asof"], now.isoformat())

    def test_market_hours(self):
        self.assertTrue(g.is_market_open(datetime(2026, 10, 12, 9, 30, tzinfo=ET)))
        self.assertFalse(g.is_market_open(datetime(2026, 10, 12, 16, 0, tzinfo=ET)))
        self.assertFalse(g.is_market_open(datetime(2026, 10, 10, 11, 0, tzinfo=ET)))   # sábado


if __name__ == "__main__":
    unittest.main(verbosity=1)
