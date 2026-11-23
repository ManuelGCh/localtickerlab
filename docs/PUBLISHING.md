# Publicar StockLab

## Antes de publicar

- [x] `data/` no está en el repo (base de datos, logs, `notify.env`, tokens de Telegram).
- [x] Las capturas usan datos de demostración y no muestran rutas, correos ni la terminal.
- [x] El README incluye el aviso: proyecto educativo, no asesoramiento financiero.
- [x] Se citan las fuentes (nasdaq.com, SEC EDGAR, Google News RSS) como endpoints públicos
      no oficiales y de uso personal/educativo.
- [x] No se publica una instancia del servidor: consume datos de terceros y requeriría
      autenticación, límites y una revisión de ToS. Mejor vídeo + repositorio.
- [x] Licencia MIT incluida.
- [x] Repo creado en GitHub y README renderizado: https://github.com/ManuelGCh/stocklab-nasdaq
- [x] Social preview subida y verificada (1280×640).
- [x] Tag `v1.0.0`, release y changelog revisado.
- [x] CI en verde para Python 3.10/3.12/3.14.
- [x] Fuentes de datos visibles en la app y en `docs/DATA_SOURCES.md`.
- [x] Validación y límites en `docs/VALIDATION.md`.
- [ ] QA manual completada en Safari/Firefox/móvil (`docs/QA_CHECKLIST.md`).

## Assets

- Icono: `web/assets/brand/icon.svg` · favicon y PNG en `web/assets/brand/`
- Social preview de GitHub: `docs/social-preview.png` (1280×640)
- `docs/screenshots/01-screener.png`
- `docs/screenshots/02-analisis.png`
- `docs/screenshots/03-backtest.png`
- `docs/screenshots/04-cartera.png`
- Vídeo de 30 s: buscar `AAPL` → ficha → screener → backtest.

## LinkedIn (borrador)

> He construido **StockLab**, un analizador del NASDAQ que corre en local.
>
> Analiza ~5.000 valores, calcula señal, objetivo, stop y R:R, añade resultados
> trimestrales y calidad fundamental, y permite validar todo con backtest walk-forward
> y datos point-in-time de la SEC.
>
> Lo que más me ha enseñado no es la señal: es **medirla**. A 21 días el IC técnico ronda
> 0,06; a 63 días desaparece. La calidad fundamental no mejora la selección en la muestra.
> Publiqué el resultado negativo igual que el positivo.
>
> Stack: Python (solo biblioteca estándar) + SQLite + JS vanilla. Sin dependencias.
>
> Proyecto educativo. No es asesoramiento financiero.
>
> Código y vídeo: [enlace]

Hashtags: `#Python #DataEngineering #Fintech #Quant #BuildInPublic`

## X / Twitter (hilo)

1. He construido un analizador del NASDAQ que corre en local. 5.000 valores, señales,
   fundamentales, backtest y cartera. Sin dependencias: Python stdlib + SQLite + JS.
   [captura]
2. Lo interesante no es la señal, es la validación: 21d IC ≈ 0,06; 63d ≈ 0. La calidad
   fundamental no mejora la selección. Prefiero publicar eso a vender humo.
3. Arquitectura: ingesta con caché, indicadores en Python puro, backtest walk-forward,
   calidad point-in-time con SEC, dashboard local.
4. Código: [repo]. Proyecto educativo, no asesoramiento financiero.

## Qué no decir

- Que predice el mercado o que genera rentabilidad.
- Que la calidad fundamental “mejora” la señal: los datos dicen lo contrario.
- Que es una herramienta de inversión: es un laboratorio de análisis y validación.

## Después

- Responder dudas técnicas (arquitectura, datos, backtest).
- Si alguien quiere probarlo: clonar, `python3 run.py` y `scripts/seed_demo.py`.
