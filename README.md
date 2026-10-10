# qqq-dashboard

Dashboard en GitHub Pages para QQQ / SPY.

- `index.html` — movers de los componentes con más peso (datos de Finnhub vía GitHub Actions).
- `scoring.html` — scoring de entradas y salidas CALL / PUT para SPY y QQQ.
- `flow.html` — flujo de opciones y niveles GEX (Call Wall, Put Wall, Gamma Flip, Agresor, Q Delta). Datos de demostración hasta que corras `flow/gex_flow.py`; ver `flow/README.md`.

Pruebas: `node tests/scoring.test.js` y `python3 tests/test_flow.py`.

## Scoring de entradas (`scoring.html`)

Motor en `scoring.js` (se prueba con `node tests/scoring.test.js`).

**Filtros previos** (si falla uno: NO ENTRAR): GEX completo, ventana 9:30-12:00 / 13:30-16:00 ET (sin entradas desde las 15:45), menos de 3 trades, menos de 2 pérdidas seguidas, pérdida diaria sobre −150 USD y spread dentro del máximo.

**Puntaje (máx. 10, por lado):** estructura VWAP + EMA 9/21/50 (2), nivel GEX (2), régimen gamma (1), agresor (2), Q Delta (1), volumen relativo + media de Bollinger (1), ratio QQQ/SPY (1).

| Puntaje | Veredicto | Contratos |
|---|---|---|
| 8-10 con estructura alineada | A+ | 5 |
| 6-7 | Entrada | 3 (2 si la estructura es mixta) |
| 5 con estructura alineada | Entrada marginal | 3 o menos |
| menos | NO ENTRAR | 0 |

Webull replica a 1/4 del tamaño.

**Salidas:** stop −10% de la prima, vela de 3 min sin movimiento a favor, inversión de agresor / Q Delta, llegada al muro objetivo (50%), cruce del VWAP en contra y cierre forzado a las 15:45 ET.

## De dónde salen los datos

Prioridad por campo: lo que escribas en la página > `signals.json` > `data.json` (solo precio).

`data.json` solo trae precio y variación del día (Finnhub, sin volumen ni intradía), así que VWAP, EMAs, GEX, Agresor y Q Delta no se pueden calcular desde ahí. `flow/gex_flow.py` los calcula y los escribe en `signals.json` (con `--git-push` los sube al repo); la página lo lee cada 60 s. Cualquier otro proceso (por ejemplo tu VPS con IBKR) también puede escribirlo con la misma estructura.

Tolerancias y umbrales editables en `CFG` dentro de `scoring.js`. No es una recomendación financiera.
