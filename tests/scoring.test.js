// Pruebas del motor de scoring. Ejecutar: node tests/scoring.test.js
const assert = require('assert');
const S = require('../scoring.js');

const T = (h, m) => h * 60 + m;

// CALL de manual: rebote en Put Wall, GEX positivo, todo alineado
const base = {
  symbol: 'QQQ', price: 750.5, vwap: 749, ema9: 750, ema21: 749.5, ema50: 748,
  gex: { zeroGamma: 745, callWall: 756, putWall: 750, netGex: 1.2 },
  agresor: 25, qDelta: 4, qDeltaPrev: 2, relVol: 1.8, bbMid: 749.8, ratio: 'up',
  spreadPct: 3, tradesToday: 0, consecLosses: 0, dailyPnl: 0,
};

// 1) Setup A+ CALL: 10/10 -> 5 contratos (Webull 1)
let r = S.evaluateEntry(base, T(10, 0));
assert.strictEqual(r.call.total, 10);
assert.strictEqual(r.verdict, 'A+');
assert.strictEqual(r.side, 'CALL');
assert.strictEqual(r.contracts, 5);
assert.strictEqual(r.webullContracts, 1);
assert.strictEqual(r.levels.partialTarget, 756);

// 2) Sin GEX => NO ENTRAR aunque todo lo demás esté perfecto
r = S.evaluateEntry({ ...base, gex: {} }, T(10, 0));
assert.strictEqual(r.verdict, 'NO ENTRAR');
assert.ok(r.gates.find(g => g.id === 'gex' && !g.ok));

// 3) Zona muerta 12:00-13:30 y después de 15:45
assert.strictEqual(S.evaluateEntry(base, T(12, 30)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry(base, T(9, 29)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry(base, T(15, 50)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry(base, T(13, 30)).verdict, 'A+');

// 4) Disciplina: 3 trades, 2 pérdidas seguidas, pérdida diaria
assert.strictEqual(S.evaluateEntry({ ...base, tradesToday: 3 }, T(10, 0)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry({ ...base, consecLosses: 2 }, T(10, 0)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry({ ...base, dailyPnl: -150 }, T(10, 0)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry({ ...base, tradesToday: 2 }, T(10, 0)).verdict, 'A+');

// 5) Spread: desconocido o por encima del máximo bloquea
assert.strictEqual(S.evaluateEntry({ ...base, spreadPct: null }, T(10, 0)).verdict, 'NO ENTRAR');
assert.strictEqual(S.evaluateEntry({ ...base, spreadPct: 12 }, T(10, 0)).verdict, 'NO ENTRAR');

// 6) Estructura mixta (EMAs desalineadas) con puntaje alto => tamaño reducido, no A+
r = S.evaluateEntry({ ...base, ema9: 748, ema21: 749.5 }, T(10, 0));
assert.strictEqual(r.call.structurePts, 0);
assert.strictEqual(r.call.total, 8);
assert.strictEqual(r.verdict, 'ENTRADA');
assert.strictEqual(r.contracts, 2);

// 7) PUT simétrico: rechazo en Call Wall con GEX positivo
const putSetup = {
  ...base, price: 755.8, vwap: 757, ema9: 756, ema21: 756.5, ema50: 758,
  agresor: -30, qDelta: -5, qDeltaPrev: -2, bbMid: 756.2, ratio: 'down',
};
r = S.evaluateEntry(putSetup, T(10, 0));
assert.strictEqual(r.put.total, 10);
assert.strictEqual(r.side, 'PUT');
assert.strictEqual(r.levels.partialTarget, 750);

// 8a) Marginal: 5/10 con estructura alineada => entrada base (o menos)
r = S.evaluateEntry({ ...base, agresor: -20, qDelta: -1, relVol: 0.8, ratio: 'down' }, T(10, 0));
assert.strictEqual(r.call.total, 5);
assert.strictEqual(r.verdict, 'ENTRADA');
assert.strictEqual(r.contracts, 3);
// 8b) Puntaje bajo => NO ENTRAR (flujo/volumen en contra y estructura rota)
r = S.evaluateEntry({ ...base, ema9: 748, agresor: -20, qDelta: -1, relVol: 0.8, ratio: 'down' }, T(10, 0));
assert.ok(r.call.total < 5);
assert.strictEqual(r.verdict, 'NO ENTRAR');
// 8c) 6/10 sin estructura alineada => entrada con tamaño reducido
r = S.evaluateEntry({ ...base, ema9: 748, agresor: 25, qDelta: 4, relVol: 0.8, ratio: 'down' }, T(10, 0));
assert.strictEqual(r.call.structurePts, 0);
assert.strictEqual(r.call.total, 6);
assert.strictEqual(r.verdict, 'ENTRADA');
assert.strictEqual(r.contracts, 2);
// 8d) 5/10 sin estructura alineada => NO ENTRAR
r = S.evaluateEntry({ ...base, ema9: 748, agresor: 25, qDelta: -1, relVol: 0.8, ratio: 'down' }, T(10, 0));
assert.strictEqual(r.call.structurePts, 0);
assert.strictEqual(r.call.total, 5);
assert.strictEqual(r.verdict, 'NO ENTRAR');

// 9) Empate CALL/PUT => sin dirección
r = S.evaluateEntry({ ...base, price: 749.9, vwap: 749.9, ema9: 1, ema21: 1, ema50: 1, agresor: 0, qDelta: 0,
  relVol: 0, ratio: null, gex: { zeroGamma: 700, callWall: 800, putWall: 600, netGex: 1 } }, T(10, 0));
assert.strictEqual(r.verdict, 'NO ENTRAR');

// 10) Datos faltantes no suman puntos
r = S.evaluateEntry({ symbol: 'QQQ', price: 750, gex: { zeroGamma: 745, callWall: 756, putWall: 750, netGex: 1 },
  spreadPct: 2 }, T(10, 0));
assert.ok(r.call.parts.filter(p => !p.known).length >= 4);
assert.strictEqual(r.verdict, 'NO ENTRAR');

// 11) Ratio en SPY se invierte (SPY más fuerte favorece CALL en SPY)
const spy = { ...base, symbol: 'SPY', ratio: 'down' };
assert.strictEqual(S.evaluateEntry(spy, T(10, 0)).call.parts.find(p => p.id === 'ratio').pts, 1);

// ---- Salidas ----
const pos = { side: 'CALL', entryPremium: 2.0, premium: 2.2, minutesOpen: 5, partialDone: false };
const ok = { ...base };
assert.strictEqual(S.evaluateExit(pos, ok, T(10, 30)).action, 'MANTENER');

// stop -10%
assert.strictEqual(S.evaluateExit({ ...pos, premium: 1.8 }, ok, T(10, 30)).action, 'CERRAR TODO');
// sin movimiento tras 3 min
assert.strictEqual(S.evaluateExit({ ...pos, premium: 2.0, minutesOpen: 3 }, ok, T(10, 30)).action, 'CERRAR TODO');
assert.strictEqual(S.evaluateExit({ ...pos, premium: 2.0, minutesOpen: 2 }, ok, T(10, 30)).action, 'MANTENER');
// flujo en contra con ganancia => 50%, con pérdida => todo
assert.strictEqual(S.evaluateExit(pos, { ...ok, agresor: -15 }, T(10, 30)).action, 'CERRAR 50%');
assert.strictEqual(S.evaluateExit({ ...pos, premium: 1.95 }, { ...ok, qDelta: -1 }, T(10, 30)).action, 'CERRAR TODO');
// muro objetivo => 50% (solo si no se tomó parcial)
const atWall = { ...ok, price: 755.9 };
assert.strictEqual(S.evaluateExit(pos, atWall, T(10, 30)).action, 'CERRAR 50%');
assert.strictEqual(S.evaluateExit({ ...pos, partialDone: true }, atWall, T(10, 30)).action, 'MANTENER');
// cruce de VWAP en contra
assert.strictEqual(S.evaluateExit(pos, { ...ok, price: 748.5 }, T(10, 30)).action, 'CERRAR TODO');
// cierre forzado
assert.strictEqual(S.evaluateExit(pos, ok, T(15, 45)).action, 'CERRAR TODO');
// PUT simétrico
const putPos = { side: 'PUT', entryPremium: 2.0, premium: 2.3, minutesOpen: 6, partialDone: false };
assert.strictEqual(S.evaluateExit(putPos, putSetup, T(11, 0)).action, 'MANTENER');
assert.strictEqual(S.evaluateExit(putPos, { ...putSetup, agresor: 20 }, T(11, 0)).action, 'CERRAR 50%');

// utilidades de hora
assert.strictEqual(S.parseHHMM('09:30'), 570);
assert.strictEqual(S.parseHHMM('25:00'), null);
assert.strictEqual(S.fmtMin(945), '15:45');
assert.strictEqual(S.minutesET(new Date('2026-10-12T14:00:00Z')), 10 * 60); // EDT = UTC-4

console.log('OK: todas las pruebas del scoring pasaron');
