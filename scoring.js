/*
 * Motor de scoring de entradas/salidas (CALL / PUT) para SPY y QQQ.
 * Funciona en el navegador (window.Scoring) y en Node (require) para pruebas.
 *
 * Pesos (máx. 10): estructura 2, nivel GEX 2, régimen gamma 1, agresor 2,
 * Q Delta 1, volumen/Bollinger 1, ratio QQQ/SPY 1.
 * Los umbrales y tolerancias son un punto de partida: calibrar con backtest.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Scoring = factory();
})(typeof self !== 'undefined' ? self : this, function () {

  const CFG = {
    levelTol: 0.0015,        // 0.15% del precio: "cerca" de un muro
    breakTol: 0.0035,        // 0.35%: ruptura/pérdida reciente de Zero Gamma
    aggressorMin: 10,        // desequilibrio mínimo (%) para contar como flujo agresor
    relVolMin: 1.5,
    thresholds: { aplus: 8, valid: 6, marginal: 5 },
    contracts: { aplus: 5, base: 3, reduced: 2 },
    webullFraction: 0.25,    // Webull replica a 1/4 del tamaño
    maxTrades: 3,
    maxConsecLosses: 2,
    maxDailyLoss: -150,
    maxSpreadPct: 10,
    stopPct: 10,             // stop sobre la prima
    noMoveMinutes: 3,
    // ventanas de entrada (minutos desde 00:00 ET): 9:30-12:00 y 13:30-16:00
    windows: [[570, 720], [810, 960]],
    forceCloseMin: 945,      // 15:45 ET
  };

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function near(price, level, tol) {
    return price !== null && level !== null && Math.abs(price - level) <= price * tol;
  }

  function normalize(inp) {
    const g = inp.gex || {};
    return {
      symbol: inp.symbol || 'QQQ',
      price: num(inp.price), vwap: num(inp.vwap),
      ema9: num(inp.ema9), ema21: num(inp.ema21), ema50: num(inp.ema50),
      bbMid: num(inp.bbMid), relVol: num(inp.relVol),
      zeroGamma: num(g.zeroGamma ?? inp.zeroGamma), callWall: num(g.callWall ?? inp.callWall),
      putWall: num(g.putWall ?? inp.putWall), netGex: num(g.netGex ?? inp.netGex),
      agresor: num(inp.agresor), qDelta: num(inp.qDelta), qDeltaPrev: num(inp.qDeltaPrev),
      ratio: inp.ratio || null,   // 'up' | 'down' | null (QQQ relativo a SPY)
      spreadPct: num(inp.spreadPct),
      tradesToday: num(inp.tradesToday) ?? 0,
      consecLosses: num(inp.consecLosses) ?? 0,
      dailyPnl: num(inp.dailyPnl) ?? 0,
      maxSpreadPct: num(inp.maxSpreadPct) ?? CFG.maxSpreadPct,
    };
  }

  function minutesET(date) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(date || new Date());
    const h = Number(parts.find(p => p.type === 'hour').value) % 24;
    const m = Number(parts.find(p => p.type === 'minute').value);
    return h * 60 + m;
  }

  function parseHHMM(s) {
    const m = /^(\d{1,2}):(\d{2})$/.exec((s || '').trim());
    if (!m) return null;
    const v = Number(m[1]) * 60 + Number(m[2]);
    return v >= 0 && v < 1440 ? v : null;
  }

  function fmtMin(min) {
    const h = String(Math.floor(min / 60)).padStart(2, '0');
    const m = String(min % 60).padStart(2, '0');
    return h + ':' + m;
  }

  // ---- Filtros previos: si falla uno => NO ENTRAR ----
  function gates(n, nowMin) {
    const gexOk = [n.zeroGamma, n.callWall, n.putWall, n.netGex].every(v => v !== null);
    const inWindow = CFG.windows.some(([a, b]) => nowMin >= a && nowMin < b);
    const beforeClose = nowMin < CFG.forceCloseMin;
    const spreadKnown = n.spreadPct !== null;
    return [
      { id: 'gex', label: 'GEX completo (Zero Gamma, Call Wall, Put Wall, Net GEX)', ok: gexOk,
        detail: gexOk ? 'ok' : 'faltan datos de GEX' },
      { id: 'window', label: 'Ventana de entrada (9:30-12:00 / 13:30-16:00 ET)', ok: inWindow && beforeClose,
        detail: fmtMin(nowMin) + ' ET' + (inWindow && !beforeClose ? ' (después del cierre forzado 15:45)' : '') },
      { id: 'trades', label: 'Menos de ' + CFG.maxTrades + ' trades hoy', ok: n.tradesToday < CFG.maxTrades,
        detail: n.tradesToday + ' hechos' },
      { id: 'losses', label: 'Menos de ' + CFG.maxConsecLosses + ' pérdidas seguidas', ok: n.consecLosses < CFG.maxConsecLosses,
        detail: n.consecLosses + ' seguidas' },
      { id: 'dailyLoss', label: 'Pérdida diaria sobre ' + CFG.maxDailyLoss + ' USD', ok: n.dailyPnl > CFG.maxDailyLoss,
        detail: n.dailyPnl + ' USD' },
      { id: 'spread', label: 'Spread del contrato dentro del máximo', ok: spreadKnown && n.spreadPct <= n.maxSpreadPct,
        detail: spreadKnown ? n.spreadPct + '% (máx. ' + n.maxSpreadPct + '%)' : 'falta el spread' },
    ];
  }

  // ---- Scoring por lado ----
  function scoreSide(side, n) {
    const call = side === 'CALL';
    const parts = [];
    const add = (id, label, max, ok, detail, known) =>
      parts.push({ id, label, max, pts: ok ? max : 0, ok: !!ok, known: known !== false, detail });

    // 1. Estructura (EMA 9/21/50 + VWAP)
    const sKnown = [n.price, n.vwap, n.ema9, n.ema21, n.ema50].every(v => v !== null);
    let sOk = false;
    if (sKnown) {
      sOk = call
        ? n.price > n.vwap && n.ema9 > n.ema21 && n.ema21 > n.ema50
        : n.price < n.vwap && n.ema9 < n.ema21 && n.ema21 < n.ema50;
    }
    add('structure', 'Estructura (VWAP + EMA 9/21/50)', 2, sOk,
      sKnown ? (sOk ? 'alineada' : 'mixta o en contra') : 'faltan datos', sKnown);

    // 2. Nivel GEX
    const lKnown = [n.price, n.zeroGamma, n.callWall, n.putWall].every(v => v !== null);
    let lOk = false, lDetail = 'faltan datos';
    if (lKnown) {
      if (call) {
        const bounce = near(n.price, n.putWall, CFG.levelTol) && n.price >= n.putWall * (1 - CFG.levelTol);
        const broke = n.price > n.zeroGamma && (n.price - n.zeroGamma) <= n.price * CFG.breakTol;
        lOk = bounce || broke;
        lDetail = bounce ? 'rebote en Put Wall' : broke ? 'ruptura sobre Zero Gamma' : 'sin nivel activo';
      } else {
        const reject = near(n.price, n.callWall, CFG.levelTol);
        const lost = n.price < n.zeroGamma && (n.zeroGamma - n.price) <= n.price * CFG.breakTol;
        lOk = reject || lost;
        lDetail = reject ? 'rechazo en Call Wall' : lost ? 'pérdida de Zero Gamma' : 'sin nivel activo';
      }
    }
    add('level', 'Nivel GEX (muro / Zero Gamma)', 2, lOk, lDetail, lKnown);

    // 3. Régimen gamma
    const rKnown = [n.price, n.netGex, n.zeroGamma, n.putWall, n.callWall].every(v => v !== null);
    let rOk = false, rDetail = 'faltan datos';
    if (rKnown) {
      if (n.netGex > 0) {
        rOk = call ? near(n.price, n.putWall, CFG.levelTol) : near(n.price, n.callWall, CFG.levelTol);
        rDetail = 'GEX +: reversión ' + (rOk ? 'en el muro' : 'sin muro cerca');
      } else {
        rOk = call ? n.price > n.zeroGamma : n.price < n.zeroGamma;
        rDetail = 'GEX −: momentum ' + (rOk ? 'a favor' : 'en contra');
      }
    }
    add('regime', 'Régimen gamma', 1, rOk, rDetail, rKnown);

    // 4. Agresor
    const aKnown = n.agresor !== null;
    const aOk = aKnown && (call ? n.agresor >= CFG.aggressorMin : n.agresor <= -CFG.aggressorMin);
    add('agresor', 'Agresor (' + (call ? 'compras al ask' : 'ventas al bid') + ')', 2, aOk,
      aKnown ? n.agresor + '%' : 'faltan datos', aKnown);

    // 5. Q Delta
    const qKnown = n.qDelta !== null;
    let qOk = false;
    if (qKnown) {
      const growing = n.qDeltaPrev === null || (call ? n.qDelta >= n.qDeltaPrev : n.qDelta <= n.qDeltaPrev);
      qOk = (call ? n.qDelta > 0 : n.qDelta < 0) && growing;
    }
    add('qdelta', 'Q Delta (signo y tendencia)', 1, qOk, qKnown ? String(n.qDelta) : 'faltan datos', qKnown);

    // 6. Volumen relativo + media central de Bollinger
    const vKnown = [n.relVol, n.bbMid, n.price].every(v => v !== null);
    const vOk = vKnown && n.relVol >= CFG.relVolMin && (call ? n.price > n.bbMid : n.price < n.bbMid);
    add('volume', 'Volumen rel. ≥ ' + CFG.relVolMin + 'x + media Bollinger', 1, vOk,
      vKnown ? n.relVol + 'x' : 'faltan datos', vKnown);

    // 7. Ratio QQQ/SPY (asunción: en SPY se invierte la lectura)
    const tKnown = n.ratio === 'up' || n.ratio === 'down';
    let tOk = false;
    if (tKnown) {
      const qqqFavored = n.ratio === 'up';
      const wantsQqq = n.symbol === 'QQQ';
      tOk = wantsQqq ? (call ? qqqFavored : !qqqFavored) : (call ? !qqqFavored : qqqFavored);
    }
    add('ratio', 'Ratio QQQ/SPY a favor', 1, tOk,
      tKnown ? (n.ratio === 'up' ? 'QQQ más fuerte' : 'SPY más fuerte') : 'sin lectura', tKnown);

    const total = parts.reduce((s, p) => s + p.pts, 0);
    return { side, total, parts, structurePts: parts[0].pts };
  }

  // ---- Veredicto de entrada ----
  function evaluateEntry(inp, nowMin) {
    const n = normalize(inp);
    const now = nowMin ?? minutesET(new Date());
    const g = gates(n, now);
    const call = scoreSide('CALL', n);
    const put = scoreSide('PUT', n);
    const best = call.total === put.total ? null : (call.total > put.total ? call : put);
    const failed = g.filter(x => !x.ok);

    const out = { gates: g, call, put, verdict: 'NO ENTRAR', side: null, contracts: 0,
      webullContracts: 0, note: '', levels: null, score: best ? best.total : Math.max(call.total, put.total) };

    if (failed.length) {
      out.note = 'Filtro: ' + failed.map(f => f.label).join('; ');
      return out;
    }
    if (!best) {
      out.note = 'CALL y PUT empatan en puntaje: sin dirección clara.';
      return out;
    }

    const T = CFG.thresholds, C = CFG.contracts;
    const structOk = best.structurePts === 2;
    let contracts = 0, label = 'NO ENTRAR', note = '';

    if (best.total >= T.aplus && structOk) {
      contracts = C.aplus; label = 'A+'; note = 'Setup A+: tamaño máximo.';
    } else if (best.total >= T.valid) {
      contracts = structOk ? C.base : C.reduced; label = 'ENTRADA';
      note = structOk ? 'Entrada válida: tamaño base.' : 'Estructura mixta: tamaño reducido aunque el puntaje alcance.';
    } else if (best.total === T.marginal && structOk) {
      contracts = C.base; label = 'ENTRADA';
      note = 'Puntaje marginal con estructura alineada: tamaño base o menos.';
    } else {
      note = 'Puntaje ' + best.total + '/10 insuficiente.';
    }

    out.verdict = contracts ? label : 'NO ENTRAR';
    out.side = contracts ? best.side : null;
    out.contracts = contracts;
    out.webullContracts = contracts ? Math.max(1, Math.round(contracts * CFG.webullFraction)) : 0;
    out.note = note;
    if (contracts && n.price !== null) {
      const call = best.side === 'CALL';
      const target = call ? n.callWall : n.putWall;
      const validTarget = target !== null && (call ? target > n.price : target < n.price);
      out.levels = {
        entry: n.price,
        stopPremiumPct: -CFG.stopPct,
        partialTarget: validTarget ? target : null,
        trailing: 'estructura 1m-5m / media central',
      };
    }
    return out;
  }

  // ---- Salidas ----
  // pos: { side, entryPremium, premium, minutesOpen, partialDone }
  function evaluateExit(pos, inp, nowMin) {
    const n = normalize(inp);
    const now = nowMin ?? minutesET(new Date());
    const call = pos.side === 'CALL';
    const entry = num(pos.entryPremium), cur = num(pos.premium), mins = num(pos.minutesOpen);
    const signals = [];
    const push = (id, label, hit, action, detail) => signals.push({ id, label, hit: !!hit, action, detail });

    const pnlPct = entry && cur !== null ? ((cur - entry) / entry) * 100 : null;
    push('stop', 'Stop: −' + CFG.stopPct + '% de la prima',
      pnlPct !== null && pnlPct <= -CFG.stopPct, 'ALL', pnlPct !== null ? pnlPct.toFixed(1) + '%' : 'falta prima');

    push('time', 'Cierre forzado 15:45 ET', now >= CFG.forceCloseMin, 'ALL', fmtMin(now) + ' ET');

    push('nomove', 'Vela de ' + CFG.noMoveMinutes + ' min sin movimiento a favor',
      mins !== null && mins >= CFG.noMoveMinutes && pnlPct !== null && pnlPct <= 0, 'ALL',
      mins !== null ? mins + ' min abierta' : 'falta el tiempo');

    const aRev = n.agresor !== null && (call ? n.agresor < 0 : n.agresor > 0);
    const qRev = n.qDelta !== null && (call ? n.qDelta < 0 : n.qDelta > 0);
    push('flow', 'Agresor se invierte o Q Delta cambia de signo', aRev || qRev,
      pnlPct !== null && pnlPct > 0 ? 'HALF' : 'ALL',
      (aRev ? 'agresor en contra ' : '') + (qRev ? 'Q Delta en contra' : '') || 'sin inversión');

    const target = call ? n.callWall : n.putWall;
    const atTarget = n.price !== null && target !== null && (call
      ? n.price >= target * (1 - CFG.levelTol) : n.price <= target * (1 + CFG.levelTol));
    push('target', 'Llegada al muro objetivo (' + (call ? 'Call Wall' : 'Put Wall') + ')',
      atTarget && !pos.partialDone, 'HALF', target !== null ? 'muro ' + target : 'falta GEX');

    const ref = n.vwap;
    const crossed = n.price !== null && ref !== null && (call ? n.price < ref : n.price > ref);
    push('cross', 'Precio cruza el VWAP en contra', crossed, 'ALL', ref !== null ? 'VWAP ' + ref : 'falta VWAP');

    const hits = signals.filter(s => s.hit);
    const action = hits.some(s => s.action === 'ALL') ? 'CERRAR TODO'
      : hits.some(s => s.action === 'HALF') ? 'CERRAR 50%' : 'MANTENER';
    return { action, signals, pnlPct };
  }

  return { CFG, evaluateEntry, evaluateExit, minutesET, parseHHMM, fmtMin, normalize };
});
