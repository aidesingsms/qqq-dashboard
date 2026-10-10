# Flujo GEX (SPY / QQQ)

Réplica de un panel de flujo de opciones: precio con **Call Wall / Gamma Flip / Put Wall**, **Agresor**, **Flujo** (instantáneo y acumulado), **Premium total**, **Q Delta** y perfil GEX por strike.

- Dashboard: `../flow.html` (muestra **DEMO** hasta que haya datos reales).
- Motor de datos: `gex_flow.py` → escribe `flow/flow_SPY.json`, `flow/flow_QQQ.json` y actualiza `../signals.json`, que alimenta el panel de scoring (`../scoring.html`).
- Prueba de tape: `tape_test.py`.

## Qué es exacto y qué es estimado

| Dato | Calidad |
|---|---|
| Call Wall, Put Wall, Gamma Flip, GEX neto | Calculado con Open Interest e IV (Black-Scholes, calls +, puts −). Razonable, pero depende de la fuente |
| Flujo, Agresor, Q Delta | **Estimado**: cambios de volumen entre snapshots, lado inferido con último precio vs. punto medio bid/ask. No es el tape real |
| VWAP, EMAs, media de Bollinger, volumen rel. | De velas de 1 min (Bollinger sobre velas de 5 min) |

Las órdenes de varias patas distorsionan el agresor (también en herramientas comerciales). Para flujo exacto necesitas el tape de opciones (trade a trade con bid/ask): corre la prueba de abajo.

## Correr el motor (en tu VPS o computadora)

```bash
pip install yfinance
python3 flow/gex_flow.py --symbol SPY --loop 60      # una vez por minuto
python3 flow/gex_flow.py --symbol QQQ --loop 60
```

- Fuera de horario (9:30-16:00 ET) calcula niveles pero no agrega flujo. `--force` lo agrega igual (para pruebas).
- El primer snapshot del día solo fija el volumen base; el flujo aparece desde el segundo.
- `--expiries 2` usa 0DTE + 1DTE (cámbialo si quieres más vencimientos).
- `--git-push 300` hace commit y push de los JSON cada 5 minutos para que GitHub Pages los muestre. Úsalo con criterio: es un repositorio público y los datos de mercado de tu proveedor pueden tener condiciones de uso que limiten republicarlos. Alternativa privada: sirve la carpeta localmente con `python3 -m http.server` y abre `flow.html` desde ahí.
- `spreadPct` en `signals.json` no se calcula (depende del contrato que elijas): se llena a mano en la página.

## Prueba de tape (¿tu fuente da el tape real?)

```bash
python3 flow/tape_test.py selftest                                                    # lógica, sin red
python3 flow/tape_test.py ibkr    --symbol SPY --expiry 20261016 --strike 780 --right C
python3 flow/tape_test.py polygon --symbol SPY --expiry 2026-10-16 --strike 780 --key TU_API_KEY
```

Elige un strike cercano al precio actual y un vencimiento activo; corre con el mercado abierto.

| Resultado | Qué significa |
|---|---|
| `OK` | Hay trades y cotizaciones, ≥80% clasificado: puedes calcular agresor real |
| `PARCIAL` | Falta alguna parte o se clasifica poco |
| `FALLA` | Tu fuente no entrega el tape |
| `INCONCLUSO` | No se pudo consultar (red, clave o plan) |

IBKR necesita TWS o Gateway abierto y la suscripción de datos de opciones (OPRA); el error 354 indica que falta. No es una recomendación de operación.
