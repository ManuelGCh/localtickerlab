# Validación del modelo

Este documento resume qué se midió, cómo y qué conclusión se extrae. El resultado
negativo se publica igual que el positivo.

## Pregunta

¿La puntuación técnica ordena los retornos futuros mejor que el azar? ¿Aporta algo la
calidad fundamental? ¿Se mantiene fuera de muestra?

## Metodología

- **Observaciones:** cada valor y fecha de rebalanceo genera una observación con la
  puntuación técnica y el retorno futuro del horizonte elegido.
- **Split:** 70 % inicial in-sample y 30 % final out-of-sample, por fechas.
- **Walk-forward:** folds secuenciales con optimización de pesos en el tramo de
  entrenamiento y evaluación en el siguiente.
- **IC:** correlación de Spearman entre puntuación y retorno futuro, calculada por fecha
  y promediada.
- **Cartera:** top N por puntuación, equiponderada, con coste de 10 pb por rebalanceo.
- **Curva:** los periodos se muestrean para que no se solapen. Si el horizonte es mayor
  que el rebalanceo, la curva no encadena retornos solapados.
- **Calidad SEC:** se usa el trimestre presentado antes de cada fecha (`filed`), no el
  último disponible a día de hoy.
- **Benchmark:** QQQ desde la misma fuente de precios.

## Resultados

### Horizonte 21 sesiones

| Métrica | Valor |
|---|---:|
| Muestra | 50 valores · 3 años |
| IC in-sample | 0,056 |
| IC out-of-sample | 0,068 |
| IC de calidad SEC | 0,002 |

Lectura: señal modesta y positiva en esta muestra. La calidad no aporta de forma
significativa.

### Horizonte 63 sesiones

| Métrica | Valor |
|---|---:|
| Muestra | 295 valores · 5 años |
| IC in-sample | −0,006 |
| IC out-of-sample | −0,015 |
| IC de calidad SEC | −0,031 |

Lectura: **sin edge** en este periodo. La calidad fundamental, usada como filtro, no
mejora la ordenación; incluso empeora ligeramente. Por eso la calidad se muestra como
contexto y no entra en la puntuación ni en la señal.

## Conclusión

- El modelo técnico no demuestra una ventaja robusta a 3 meses.
- El resultado cambia con el horizonte y la muestra: 21 días es más favorable, 63 días
  no.
- No se justifica subir la complejidad ni optimizar pesos con estos datos.
- La calidad fundamental es información de contexto, no una señal validada.
- El proyecto se presenta como **laboratorio de ingeniería y validación**, no como
  sistema de inversión.

## Limitaciones

1. **Sesgo de supervivencia:** se usan valores cotizados actualmente.
2. **Universo cambiante:** la selección por liquidez se hace con datos actuales.
3. **Precios de cierre:** no hay intradía, slippage real ni libros de órdenes.
4. **Costes simplificados:** 10 pb fijos por rebalanceo.
5. **Sin dividendos:** los retornos son de precio.
6. **Múltiples pruebas:** probar horizontes y configuraciones aumenta el riesgo de
   sobreajuste; por eso se reporta el resultado principal y no solo el mejor.
7. **Cobertura:** algunos valores no tienen fundamentales o histórico suficiente.
8. **Muestra limitada:** 50–295 valores y 3–5 años no permiten conclusiones fuertes.

## Reproducir

Desde la pestaña **Backtest**, con `Top 300`, horizonte `63 sesiones`, rebalanceo mensual,
5 años, coste 10 pb y calidad SEC activada.

También por API:

```bash
curl "http://127.0.0.1:8766/api/backtest/start?symbols=300&horizon=63&rebalance=21&years=5&cost=10&top_n=20&quality=1"
```

La caché de 10 años está en `data/backtest.db`. La primera ejecución descarga históricos;
las siguientes usan caché.

## Qué haría falta para cambiar la conclusión

- Más historia y universo point-in-time sin supervivencia.
- Datos oficiales con ajustes corporativos verificados.
- Validación en otro mercado y otro régimen.
- Costes y slippage realistas.
- Prueba de estabilidad de parámetros fuera de muestra.
- Informe pre-registrado antes de mirar resultados.

Hasta entonces, la conclusión es la que es: **no hay evidencia suficiente de edge**.
