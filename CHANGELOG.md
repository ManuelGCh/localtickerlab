# Changelog

## v1.1.0 — 2026-11-23

### Cambio de nombre

- El proyecto pasa a llamarse **LocalTickerLab** para evitar colisiones con otros
  productos y dejar claro el enfoque local.
- Nuevo repositorio: `ManuelGCh/localtickerlab`.
- Identificadores actualizados: paquete `localtickerlab`, variables
  `LOCALTICKERLAB_*`, agente `com.localtickerlab.daily` y `LocalTickerLab.command`.
- Capturas, social preview e iconos regenerados con la nueva marca.

## v1.0.0 — 2026-11-23

Primera versión publicable como laboratorio local de análisis y validación.

### Incluye

- Screener NASDAQ-100 y Composite con puntuación técnica, señal, objetivo, stop, R:R,
  RSI, fuerza relativa y calidad contextual.
- Análisis individual con velas, medias, volumen, RSI, métricas y resumen; rango
  **10A** con hasta ~10 años (límite de la fuente).
- Fundamentales de Nasdaq.com y calidad point-in-time desde SEC EDGAR.
- Noticias por ticker desde Google News RSS.
- Backtest walk-forward con split in-sample/out-of-sample, costes, deciles y calibración.
- Cartera con P&L, riesgo, correlación, curva de evolución vs QQQ y edición de posiciones.
- Alertas de precio, RSI, señal, puntuación, máximo de 52 semanas, volumen y resultados.
- Watchlist y vistas guardadas en SQLite, con exportación/importación y backup.
- Escaneo diario opcional con launchd y bloqueo global de escaneos.
- UI responsive, onboarding, paginación, acciones masivas y navegación por teclado.

### Aviso legal y uso

- Proyecto educativo, no asesoramiento financiero; el backtest publica el resultado
  negativo y la calidad fundamental es contexto, no señal.
- Sin redistribución de datos: la base SQLite es local y no se versiona.
- SEC EDGAR pide un contacto real; la app avisa si `LOCALTICKERLAB_SEC_UA` no está definido.

### Calidad

- 105 tests de Python.
- CI para Python 3.10, 3.12 y 3.14.
- Lighthouse 100 en Chrome en las vistas principales.
- Validación documentada en `docs/VALIDATION.md`.
- Fuentes y límites documentados en `docs/DATA_SOURCES.md`.

### Limitaciones conocidas

- Precios de cierre diario, no intradía.
- Fuentes públicas no oficiales salvo SEC, sin SLA.
- Sesgo de supervivencia en el backtest.
- Algunos tickers sin histórico o fundamentales.
- No es asesoramiento financiero.
