# Publicar LocalTickerLab

## Antes de publicar

- [x] `data/` no está en el repo (base de datos, logs, `notify.env`, tokens de Telegram).
- [x] Las capturas usan datos de demostración y no muestran rutas, correos ni la terminal.
- [x] El README incluye el aviso: proyecto educativo, no asesoramiento financiero.
- [x] Se citan las fuentes (nasdaq.com, SEC EDGAR, Google News RSS) como endpoints públicos
      no oficiales y de uso personal/educativo.
- [x] No se publica una instancia del servidor: consume datos de terceros y requeriría
      autenticación, límites y una revisión de ToS. Mejor vídeo + repositorio.
- [x] Licencia MIT incluida.
- [x] Repo creado en GitHub y README renderizado: https://github.com/ManuelGCh/localtickerlab
- [x] Social preview subida y verificada (1280×640).
- [x] Tag `v1.0.0`, release y changelog revisado.
- [x] CI en verde para Python 3.10/3.12/3.14.
- [x] Fuentes de datos visibles en la app y en `docs/DATA_SOURCES.md`.
- [x] Validación y límites en `docs/VALIDATION.md`.
- [x] QA verificada en Chrome, Firefox escritorio y Safari iOS; resto opcional
      (`docs/QA_CHECKLIST.md`).

## Assets

- Icono: `web/assets/brand/icon.svg` · favicon y PNG en `web/assets/brand/`
- Social preview de GitHub: `docs/social-preview.png` (1280×640)
- `docs/screenshots/01-screener.png`
- `docs/screenshots/02-analisis.png`
- `docs/screenshots/03-backtest.png`
- `docs/screenshots/04-cartera.png`
- Vídeo de demostración: pendiente de grabar con datos demo (75–85 s).

## Qué no decir

- Que predice el mercado o que genera rentabilidad.
- Que la calidad fundamental “mejora” la señal: los datos dicen lo contrario.
- Que es una herramienta de inversión: es un laboratorio de análisis y validación.

## Después

- Responder dudas técnicas (arquitectura, datos, backtest).
- Si alguien quiere probarlo: clonar, `python3 run.py` y `scripts/seed_demo.py`.
