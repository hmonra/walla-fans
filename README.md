# WallaFans 🛰️

Detección automática de interacciones en tu cuenta de **Wallapop** con notificación rica a **Telegram**, ejecución continua vía **GitHub Actions**, y datos históricos para análisis de negocio.

## Capacidades

| Evento | Detectado | ¿Quién? | Notificación |
|---|---|---|---|
| ⭐ Favorito a tu perfil | ⚠️ *no expuesto por la API web* (ver nota) | ❌ | — |
| 📦 Favorito a un anuncio | ✅ contador por anuncio +1/-1 | ⚠️ idem | Sí |
| 👀 Vistas de anuncios | ✅ delta por anuncio | — | Sí (si delta ≥ 20) |
| 💰 Cambio de precio | ✅ | — | Sí |
| 🛒 Cambio de estado (vendido/reservado) | ⚠️ no expuesto por la API web | — | — |
| 🚩 Reporte recibido | ✅ contador + hora | ❌ (Wallapop no lo revela) | Sí |
| 💬 Mensajes nuevos | ✅ contador de no leídos | ⚠️ solo contador en web | Silencioso (resumen) |

> **Nota honesta sobre "quién" y el favorito de perfil:** la API web
> **no expone** `profileFavoritedReceived` en `stats` (validado el 2026-08 con
> todos los `X-DeviceOS`). El contador de favoritos *por anuncio* sí existe
> (`counters.favorites` en `GET /api/v3/items/{id}`). La única vía potencial
> de identidad es el **historial de PubNub** (ver [Arquitectura](#arquitectura)).
> Si el canal no conserva historial o el evento no incluye `actorId`, los
> favoritos se reportan como contador + hora (sin nombre). Un experimento con
> una 2ª cuenta decidirá si el favorito de perfil es detectable por otra vía.

## Arquitectura

```
GitHub Actions (cada 30 min)
   │
   ├─ 1. Refresh token móvil (Keycloak, client android) → access token
   ├─ 2. Poller: stats del perfil + tus anuncios (listado + detalle con contadores)
   ├─ 3. PubNub history: eventos guardados en el canal desde la última vez (identidad)
   ├─ 4. Diff vs estado anterior → eventos
   ├─ 5. Telegram: mensaje(s) ricos (foto, botones, perfil del actor)
   └─ 6. Commit de state/ (state.json + events.jsonl) → línea temporal en git
```

- **poll.yml**: cada 30 min. **digest.yml**: resumen 21:00 (noche, corto) + 08:00 (mañana, completo), a prueba de cambio de hora (DST). **watchdog.yml**: alerta si el sistema deja de funcionar.
- Estado e historial viven en `state/` y se commitean, así que cada interacción queda registrada en el historial de git.

## Setup (una vez)

### 1. Bot de Telegram
1. Habla con **@BotFather** → `/newbot` → copia el **token**.
2. Abre tu bot → pulsa **Start**.
3. Consigue tu **chat_id**: manda un mensaje al bot y visita
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → campo `chat.id`.

### 2. Captura del refresh token móvil (en tu PC, una vez)
```bash
pip install -r requirements.txt
python scripts/capture_mobile_oauth.py
```
Se abre Chrome: **Fase 1** inicias sesión en la web de Wallapop con Google como
siempre (en Google puedes usar tu cuenta o "Prueba otra forma" → teléfono).
**Fase 2** el script reutiliza esa sesión (SSO de Keycloak) para obtener un
código de autorización del client `android` e intercambiarlo por un
**refresh token**, que verifica y guarda en tu `.env` local:
- `WALLAFANS_REFRESH_TOKEN` — **la credencial principal**
- `WALLAFANS_REFRESH_CLIENT_ID=android`

> **Cómo funciona el auth (validado):** `es.wallapop.com/api/auth/session`
> (vía cookie web) funciona en tu PC pero está **bloqueado desde los runners**
> de GitHub Actions (IP de datacenter EE.UU. → 403). En cambio
> `api.wallapop.com` y el endpoint de tokens de Keycloak (`accounts.wallapop.com`)
> **sí responden desde GitHub Actions**. Los clients móviles de Wallapop
> (`android`/`ios`) aceptan el grant OAuth `refresh_token`, así que un único
> refresh token capturado una vez permite a GitHub Actions renovar access
> tokens (5 min) para siempre, 24/7 y sin tu PC. La cookie de sesión web queda
> como fallback (solo local): `WALLAFANS_EMAIL`/`WALLAFANS_PASSWORD` (login
> Playwright) son la última alternativa.

### 3. Repositorio de GitHub
1. Crea un repo **vacío** (sin README/.gitignore/license — ya están en local; si lo creas con archivos habrá que hacer force-push).
2. Añade los **Secrets** (Settings → Secrets and variables → Actions):

| Secret | Valor |
|---|---|
| `WALLAFANS_REFRESH_TOKEN` | del `.env` local (**obligatorio**) |
| `WALLAFANS_REFRESH_CLIENT_ID` | `android` |
| `WALLAFANS_SESSION_COOKIE` | del `.env` local (fallback local) |
| `WALLAFANS_EMAIL` / `WALLAFANS_PASSWORD` | (última alternativa) |
| `WALLAFANS_USER_ID` | el de tu cuenta (de tu `.env` local) |
| `TELEGRAM_BOT_TOKEN` | del paso 1 |
| `TELEGRAM_CHAT_ID` | del paso 1 |
| `PUBNUB_SUBSCRIBE_KEY` / `PUBNUB_PUBLISH_KEY` | claves públicas (config) |
| `PUBNUB_AUTH_TOKEN` / `PUBNUB_CHANNEL` | de la captura (si hay) |
| `PUBNUB_LAST_TIMETOKEN` | `0` |

3. Sube el código:
```bash
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

### 4. Prueba
```bash
python -m wallafans.cli test --send
python -m wallafans.cli state
python -m wallafans.cli poll   # un ciclo completo, localmente
```

## Operación y mantenimiento

- **Renovar credenciales:** si el refresh token dejara de funcionar (cambio de contraseña, revocación), el `watchdog` avisará. Repite `python scripts/capture_mobile_oauth.py` y actualiza los secrets `WALLAFANS_REFRESH_TOKEN`/`WALLAFANS_REFRESH_CLIENT_ID`.
- **Exportar datos** para tu estudio: `python scripts/analytics.py --csv datos.csv`.
- **Ejecución manual** de un poll: Actions → *WallaFans - Poll* → *Run workflow*.

## Seguridad
- `.env` y `secrets_local.json` están en `.gitignore` y **no se suben**.
- Ningún token se hardcodea; todo llega por secrets de GitHub.
- Las claves de PubNub son públicas (están en el bundle JS de Wallapop) y no son secretos.
- Uso personal de los datos de tu propia cuenta. No compartas los datos de terceros públicamente.

## Límites conocidos
- **Reportes:** solo contador + hora (Wallapop oculta quién y el motivo incluso al reportado).
- **Favorito de perfil:** no expuesto por la API web; experimento con 2ª cuenta en fase de validación.
- **Mensajes:** solo contador de no leídos en la web (el listado de conversaciones devuelve 405/404); sin identidad del remitente por esta vía.
- **Estado del anuncio (vendido/reservado):** no expuesto por el detalle web del item.