# Checklist de QA

## Automatizado

- [x] `python -m unittest discover -s tests -v` → 105 tests.
- [x] `python -m py_compile run.py stocklab/*.py`.
- [x] CI en Python 3.10, 3.12 y 3.14 (GitHub Actions).
- [x] `data/` fuera de git.
- [x] `gzip` en respuestas JSON.
- [x] Lighthouse Accesibilidad/SEO/Best Practices 100 en Chrome (Screener y Cartera).

## Escritorio — Chrome

- [x] Carga inicial y onboarding.
- [x] Screener: filtros, búsqueda, orden, columnas, vistas, herramientas.
- [x] Paginación y selección múltiple.
- [x] Acciones masivas: watchlist, comparador y CSV.
- [x] Ficha: Resumen, Resultados, Noticias, Métricas y Comparar.
- [x] Gráfico: 1M/3M/6M/1A/10A con histórico largo y eje por años.
- [x] Fundamentales y enriquecimiento.
- [x] Backtest: OOS, curva plegada, detalle, cancelación.
- [x] Cartera: curva, posiciones, edición, correlación y alertas.
- [x] Exportación/importación y backup.
- [x] Fuentes de datos en Sistema y pie.
- [x] Navegación con teclado en filas, sugerencias y botones.
- [x] Sin errores en consola.

## Móvil — Chrome 390 px

- [x] Sin scroll horizontal de página.
- [x] Tarjetas de screener.
- [x] Tarjetas de watchlist.
- [x] Tarjetas de posiciones.
- [x] Herramientas y filtros sin desbordar.
- [x] Charts y tablas con scroll interno.

## Pendiente de verificación manual

- [ ] Safari macOS.
- [ ] Firefox macOS.
- [ ] Safari iOS.
- [ ] Chrome Android.

Estos navegadores no se pudieron automatizar en el entorno de desarrollo; la checklist
debe completarse antes de etiquetar una release pública.

## Smoke test de API

```bash
curl -s http://127.0.0.1:8766/api/status
curl -s http://127.0.0.1:8766/api/system
curl -s "http://127.0.0.1:8766/api/screen/latest?universe=ndx"
curl -s "http://127.0.0.1:8766/api/analyze?symbol=AAPL"
curl -s http://127.0.0.1:8766/api/portfolio
curl -s http://127.0.0.1:8766/api/watchlist
curl -s http://127.0.0.1:8766/api/views
curl -s http://127.0.0.1:8766/api/backtest/latest
```

Comprobar que no hay 5xx y que `api/system` incluye `sources`.

## Antes de publicar

- [ ] Repo público y README renderizado.
- [ ] Social preview subida.
- [ ] Tag `v1.0.0`.
- [ ] `docs/DATA_SOURCES.md` y `docs/VALIDATION.md` revisados.
- [ ] Ningún dato personal en el historial de git.
- [ ] Vídeo grabado con datos de demo.
