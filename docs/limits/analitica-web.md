# Analítica web y consentimiento — límites

Catálogo cuantitativo de la medición de audiencia: rutas medidas, cookies, almacenamiento, dominios permitidos y lo que deliberadamente no soporta. El comportamiento está en el gemelo [`../features/analitica-web.md`](../features/analitica-web.md).

## 1. Identificadores de la propiedad

| Dato | Valor exacto | Nota |
|---|---|---|
| Cuenta de Google Analytics | `MISSELA` (id `404529382`) | Propiedad de `amuelas30@gmail.com`, la misma cuenta dueña del proyecto de Google Cloud |
| Propiedad GA4 | `missela.app` (id `549749203`) | España · GMT+02:00 Spain Time · EUR |
| Flujo de datos | `MISSELA Web` → `https://missela.app` (stream id `15431619023`) | Único flujo; no hay apps iOS/Android |
| Measurement ID | `G-DZYMBF4SEG` | El que se hornea en el bundle |
| Variable de build | `VITE_GA_MEASUREMENT_ID` | Vacía = feature completamente desactivada |

## 2. Alcance de medición

| Elemento | Valor exacto |
|---|---|
| Rutas medidas | `/`, `/privacy`, `/terms`, `/login` — y solo esas cuatro |
| Normalización | Se ignora la barra final (`/privacy/` cuenta como `/privacy`) |
| Rutas nunca medidas | Todo lo demás: `/home`, `/create-mailbox` y cualquier `/m/:mailboxId/**` |
| Eventos enviados | Únicamente `page_view`, con `page_path`, `page_location` y `page_title` |
| Parámetros de identidad | Ninguno: sin `user_id`, sin propiedades de usuario, sin eventos personalizados |
| Envío automático de página | Desactivado en los dos sitios: `config` con `send_page_view: false` en el frontend **y** el sub-ajuste "page changes based on browser history events" apagado en la medición mejorada de la propiedad |

Los demás sub-ajustes de la medición mejorada (scroll, clics salientes, búsqueda del sitio, interacción con vídeos, descargas de archivos) siguen activos, pero solo pueden dispararse en las páginas públicas, que es donde el script está cargado.

## 3. Consentimiento

| Elemento | Valor exacto |
|---|---|
| Dónde se guarda la elección | `localStorage`, clave `analyticsConsent` |
| Valores admitidos | `granted` \| `denied`; cualquier otro valor se lee como "sin responder" |
| Caducidad de la elección | Ninguna: persiste hasta que se borren los datos del sitio |
| Sin responder o rechazado | Cero peticiones a dominios de Google, cero cookies |
| Dónde aparece el aviso | Solo en las cuatro rutas medidas |
| Bloqueo de la interfaz | Ninguno: el aviso es una barra inferior, no un modal |

## 4. Cookies

| Cookie | Quién la pone | Cuándo | Caducidad |
|---|---|---|---|
| Cookie de sesión de Missela | La propia aplicación | Al iniciar sesión | Ver [`autenticacion-y-cuentas.md`](autenticacion-y-cuentas.md) |
| `_ga` | El script de Google | Solo tras aceptar | 2 años |
| `_ga_G-DZYMBF4SEG` | El script de Google | Solo tras aceptar | 2 años |

La elección de consentimiento **no** se guarda en una cookie.

## 5. Dominios permitidos en la CSP

Añadidos a la cabecera `Content-Security-Policy-Report-Only` del `Caddyfile`:

| Directiva | Dominios añadidos |
|---|---|
| `script-src` | `https://www.googletagmanager.com` |
| `connect-src` | `https://*.google-analytics.com`, `https://*.analytics.google.com`, `https://www.googletagmanager.com` |
| `img-src` | `https://*.google-analytics.com`, `https://www.googletagmanager.com` |

La CSP sigue en modo **solo-reporte**: no bloquea nada, únicamente registra violaciones en la consola.

## 6. Qué NO soporta

- **No mide la aplicación autenticada.** Es una promesa explícita de la política de privacidad, no una limitación técnica pendiente de levantar.
- **No hay eventos de producto ni conversiones.** Solo se envía `page_view`; no hay eventos de "cuenta conectada", "correo enviado" ni similares, porque todos ocurrirían en rutas no medidas.
- **No usa Google Consent Mode.** Se descartó frente a la carga condicional: con Consent Mode el script se descarga siempre y envía señales sin cookie aunque el visitante rechace. La opción elegida es más estricta a cambio de perder el tráfico modelado de quien rechaza.
- **No se puede cambiar de opinión desde la interfaz.** No hay ajuste para revocar el consentimiento; la vía es borrar los datos del sitio en el navegador. Un panel de preferencias de cookies sería la ampliación natural si algún día hace falta.
- **No hay banner en la aplicación autenticada**, porque ahí no se mide nada.
- **No mide a quien rechaza, ni siquiera de forma agregada.** Las cifras de Analytics son un suelo, no el tráfico real.
- **Los informes por título de página no distinguen entre las cuatro rutas.** Las páginas públicas no escriben `document.title` (invariante de un único escritor, ver `frontend_guide.md` §17.4), así que las cuatro llegan a Google con el mismo `page_title`: `MISSELA`. Para separarlas hay que mirar los informes por **ruta** (`page_location`, que sí es correcta y única por página), no por título. Darles título propio obligaría a romper ese invariante del frontend.
- **No hay medición del lado del servidor.** Ningún endpoint del backend registra visitas; si el navegador no envía el evento, no existe.
- **No hay alertas ni informes automáticos.** Los datos solo se consultan entrando a la consola de Google Analytics.
