# WallaFans 🛰️

Detección automática de interacciones en tu cuenta de **Wallapop** con notificación rica a **Telegram**, ejecución continua vía **GitHub Actions**, y datos históricos para análisis de negocio.

## Capacidades

| Evento | Detectado | ¿Quién? | Notificación |
|---|---|---|---|
| ⭐ Favorito a tu perfil | ✅ contador +1/-1 con hora | ⚠️ si el historial PubNub lo aporta | Sí |
| 📦 Favorito a un anuncio | ✅ contador por anuncio +1/-1 | ⚠️ idem | Sí |
| 👀 Vistas de anuncios | ✅ delta por anuncio | — | Sí (si delta ≥ 20) |
| 💰 Cambio de precio | ✅ | — | Sí |
| 🛒 Cambio de estado (vendido/reservado) | ✅ | — | Sí |
| 🚩 Reporte recibido | ✅ contador + hora | ❌ (Wallapop no lo revela) | Sí |
| 💬 Nuevos contactos/mensajes | ✅ identidad + texto | ✅ | Solo resumen diario |

> **Nota honesta sobre "quién":** Wallapop no expone quién te da favorito. La única vía es el **historial de PubNub** (ver [Arquitectura](#arquitectura)). Si el canal no conserva historial o el evento no incluye `actorId`, los favoritos se reportan como contador + hora (sin nombre). El resto de señales (contactos, mensajes) sí incluyen identidad.

## Arquitectura

```
GitHub Actions (cada 30 min)
   │
   ├─ 1. Refresh token → access token (Keycloak)
   ├─ 2. Poller: stats del perfil + tus anuncios + conversaciones
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

### 2. Captura de sesión (en tu PC)
```bash
pip install -r requirements.txt
python scripts/capture_one_time.py
```
Se abre Chrome, inicias sesión manual en Wallapop (240 s de margen). El script guarda en `secrets_local.json` (git-ignored) y en tu `.env` local:
- `WALLAFANS_REFRESH_TOKEN` (si el flujo lo emite) — o `WALLAFANS_SESSION_COOKIE`
- `PUBNUB_AUTH_TOKEN`, `PUBNUB_CHANNEL`
- Y registra todas las llamadas API (para validar el poller).

Si no se captura refresh token, alternativas: poner `WALLAFANS_EMAIL`/`WALLAFANS_PASSWORD` (login Playwright automático, más lento y frágil) o usar el fallback de cookie.

### 3. Repositorio de GitHub
1. Crea un repo (privado o público; los secrets nunca se exponen).
2. Añade los **Secrets** (Settings → Secrets and variables → Actions):

| Secret | Valor |
|---|---|
| `WALLAFANS_REFRESH_TOKEN` | del `.env` local |
| `WALLAFANS_SESSION_COOKIE` | (fallback) |
| `WALLAFANS_EMAIL` / `WALLAFANS_PASSWORD` | (fallback) |
| `WALLAFANS_USER_ID` | `USER_ID_PLACEHOLDER` |
| `TELEGRAM_BOT_TOKEN` | del paso 1 |
| `TELEGRAM_CHAT_ID` | del paso 1 |
| `PUBNUB_SUBSCRIBE_KEY` / `PUBNUB_PUBLISH_KEY` | claves públicas (config) |
| `PUBNUB_AUTH_TOKEN` / `PUBNUB_CHANNEL` | de la captura (si hay) |
| `PUBNUB_LAST_TIMETOKEN` | `0` |

3. Sube el código:
```bash
git init
git add .
git commit -m "walla sentry"
git branch -M main
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

- **Renovar sesión:** el refresh token caduca (~30 días). Cuando `watchdog` avise, repite `python scripts/capture_one_time.py` y actualiza el secret.
- **Exportar datos** para tu estudio: `python scripts/analytics.py --csv datos.csv`.
- **Ejecución manual** de un poll: Actions → *WallaFans - Poll* → *Run workflow*.

## Seguridad
- `.env` y `secrets_local.json` están en `.gitignore` y **no se suben**.
- Ningún token se hardcodea; todo llega por secrets de GitHub.
- Las claves de PubNub son públicas (están en el bundle JS de Wallapop) y no son secretos.
- Uso personal de los datos de tu propia cuenta. No compartas los datos de terceros públicamente.

## Límites conocidos
- **Reportes:** solo contador + hora (Wallapop oculta quién y el motivo incluso al reportado).
- **Identidad de favoritos:** depende del historial PubNub (experimento con 2ª cuenta en fase de validación).
- Los tokens web duran 5 min; por eso el sistema depende del refresh token capturado.