# Seguridad y privacidad

LocalTickerLab está diseñado como una aplicación **local, monousuario y de solo lectura sobre
datos públicos**. No está pensado para exponerse en Internet.

## Modelo de seguridad

- Escucha en `127.0.0.1` por defecto.
- No tiene autenticación, usuarios, sesiones ni permisos.
- No incluye telemetría ni llamadas a servicios de analítica.
- Los datos, la cartera, la watchlist y las vistas se guardan en SQLite en `data/`.
- Las credenciales opcionales de notificación viven en `data/notify.env`, que está
  ignorado por git.
- No se incluye ninguna base de datos ni token en el repositorio.

## No exponer el servidor

No publiques el puerto en Internet ni lo pongas detrás de un proxy sin añadir antes:

- autenticación,
- TLS,
- límites de peticiones,
- protección CSRF,
- separación de usuarios,
- revisión de los términos de las fuentes de datos.

La app usa peticiones GET para varias acciones locales. Es aceptable en `localhost`; no
lo sería en una instancia pública.

## Datos de terceros

Las fuentes se describen en [DATA_SOURCES.md](docs/DATA_SOURCES.md). Son endpoints
públicos no oficiales salvo SEC EDGAR. No redistribuyas la base de datos ni los datos
descargados y respeta sus términos y límites.

La SEC requiere un `User-Agent` identificable:

```bash
export LOCALTICKERLAB_SEC_UA="LocalTickerLab research tu@email.com"
```

## Secretos

- No subas `data/`, `notify.env`, tokens de Telegram ni claves.
- Si publicas el repo, revisa el historial de git por correos o rutas personales.
- Usa `.gitignore` y un email noreply de GitHub si no quieres exponer tu correo.

## Copias de seguridad

- `GET /api/backup` descarga una copia completa de SQLite.
- `GET /api/user-data/export` exporta posiciones, alertas, watchlist y vistas en JSON.
- Guarda las copias fuera del repo.

## Reportar un problema

Abre un aviso privado de seguridad en GitHub (Security → Report a vulnerability) en lugar
de publicar un issue con detalles explotables. Incluye versión, sistema operativo, pasos
y el impacto esperado.

## Versiones

Se da soporte a la versión publicada más reciente. No hay garantía de compatibilidad con
ramas antiguas.
