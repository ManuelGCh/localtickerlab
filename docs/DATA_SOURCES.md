# Fuentes de datos

StockLab es una herramienta **local y educativa**. No redistribuye datos de terceros
ni ofrece una instancia pública. Esta página documenta de dónde sale cada dato, cómo se
cachea y qué limitaciones tiene.

## Resumen

| Fuente | Uso | Tipo | Caché |
|---|---|---|---|
| **Nasdaq.com** | Universos, precios históricos, fundamentales, calendario de resultados, consenso y ratings | API pública no documentada | Precios hasta el último cierre; fundamentales 12 h |
| **Yahoo Finance** | Respaldo de precios históricos si Nasdaq falla | Endpoint público de gráficos | Igual que precios |
| **SEC EDGAR** | Calidad fundamental *point-in-time* desde XBRL | Datos públicos oficiales | 7 días |
| **Google News RSS** | Titulares, eventos y sentimiento aproximado | RSS público | 30 min |

La app muestra esta lista en **Cartera → Sistema → Fuentes de datos** y enlaza a cada
fuente. El pie de página también indica el origen.

## Nasdaq.com

Base: `https://api.nasdaq.com/api`

Se usa para:

- Listado del NASDAQ Composite y del NASDAQ-100.
- Histórico diario por ticker.
- Resumen, dividendos, capitalización, PER y rango de 52 semanas.
- Estados financieros trimestrales y anuales.
- Sorpresas de EPS, previsión de analistas, ratings y precio objetivo.
- Calendario de resultados.

Endpoints principales (pueden cambiar sin aviso):

```text
/api/screener/stocks
/api/quote/list-type/nasdaq100
/api/quote/{symbol}/historical
/api/quote/{symbol}/summary
/api/quote/{symbol}/dividends
/api/company/{symbol}/financials
/api/company/{symbol}/earnings-surprise
/api/analyst/{symbol}/earnings-forecast
/api/analyst/{symbol}/ratings
/api/calendar/earnings
```

Características:

- No requiere clave ni registro.
- Se envía `Referer`/`Origin` de nasdaq.com, como haría el navegador.
- **No es una API oficial con SLA.** Puede limitar peticiones, cambiar el esquema o
  devolver datos incompletos.
- Los precios son **cierres diarios**, no cotización intradía.
- No se redistribuye la base de datos.

## Yahoo Finance

Base: `https://query1.finance.yahoo.com` y `https://query2.finance.yahoo.com`

Se usa **solo como respaldo** si Nasdaq no devuelve un histórico válido:

```text
/v8/finance/chart/{symbol}?range=3y&interval=1d
```

Es un endpoint público no documentado, sin SLA. No se usa para fundamentales.

## SEC EDGAR

Base: `https://data.sec.gov` y `https://www.sec.gov`

Se usa para construir el score de calidad **sin look-ahead**: cada observación usa solo
trimestres presentados (`filed`) antes de la fecha de análisis.

Endpoints:

```text
https://www.sec.gov/files/company_tickers.json
https://data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/...
```

La SEC exige un `User-Agent` identificable. Configúralo antes de usar el backtest con
calidad:

```bash
export STOCKLAB_SEC_UA="StockLab research tu@email.com"
```

Las peticiones se espacian (~8/s) y se cachean. No se descargan documentos completos:
solo conceptos XBRL concretos.

## Google News RSS

Base: `https://news.google.com/rss/search`

Se usa para titulares por ticker:

```text
/rss/search?q={ticker}+stock&hl=es&gl=ES&ceid=ES:es
```

- Solo se analiza el **titular**, no el cuerpo.
- El sentimiento es una heurística de palabras, no un modelo validado.
- Los enlaces apuntan al medio original.
- Caché de 30 minutos.

## Flujo de datos

```text
Nasdaq / Yahoo ──► precios diarios ──► SQLite (prices)
Nasdaq         ──► fundamentales  ──► SQLite (fundamentals)
SEC EDGAR      ──► XBRL           ──► SQLite (sec_series)
Google News    ──► titulares      ──► SQLite (news_cache)

SQLite ──► métricas y score ──► screener / ficha / cartera
SQLite ──► observaciones point-in-time ──► backtest (data/backtest.db)
```

La base principal es `data/market.db`. La caché del backtest es independiente
(`data/backtest.db`) para no mezclar el histórico largo con los datos operativos.

## Frescura

- **Precios:** último cierre disponible. Se refrescan tras el cierre (18:00 ET) y se
  detectan series atrasadas para reintentar.
- **Fundamentales:** caché de 12 h; el escaneo diario refresca el top y completa el
  NASDAQ-100.
- **Noticias:** 30 min.
- **Calidad SEC:** 7 días.
- La fecha de cierre se muestra en la barra de resultados y el chip superior indica el
  último escaneo.

## Límites conocidos

- Sin cotización intradía ni tiempo real.
- Algunos tickers no tienen histórico o fundamentales en las fuentes públicas; se
  muestran como `s/d` o aparecen en “Fallidos”.
- El NASDAQ-100 tiene 101 cotizaciones porque `GOOG` y `GOOGL` son clases distintas.
- Los datos pueden contener errores, splits mal ajustados o retrasos.
- El backtest tiene sesgo de supervivencia y usa el universo actual.

## Uso responsable

- No redistribuyas la base de datos ni los datos descargados.
- Respeta los términos de uso y los límites de cada proveedor.
- No publiques una instancia del servidor: no tiene autenticación.
- No es asesoramiento financiero.

Si una fuente cambia, revisa `stocklab/sources/`, `stocklab/fundamentals.py`,
`stocklab/sec.py` y `stocklab/news.py`.
