# Páginas públicas (landing, privacidad y términos) — límites y topes

Catálogo cuantitativo de las páginas accesibles sin sesión. El comportamiento y el porqué viven en su gemelo: **[../features/paginas-publicas.md](../features/paginas-publicas.md)**.

## Rutas

| Ruta | Qué es | Acceso |
| --- | --- | --- |
| `/` | Landing de marketing (índice público) | Anónimo: landing · Autenticado: redirección a `/home` |
| `/home` | Gateway de bandejas (antes era el índice `/`) | Solo autenticado (tras `RequireAuth`) |
| `/privacy` | Política de privacidad | Público (con o sin sesión) |
| `/terms` | Términos de servicio | Público (con o sin sesión) |

- `/privacy` y `/terms` se cargan en diferido (chunk propio); la landing va en el bundle de arranque.
- Ningún `navigate('/')` / `to="/"` preexistente cambió: la redirección de `/` a `/home` los cubre.

## Idioma

| Regla | Valor exacto |
| --- | --- |
| Preferencia guardada | `localStorage['lang']`, valores `es` \| `en` — la MISMA clave que el ajuste de idioma de la aplicación ([ajustes.md](ajustes.md)) |
| Precedencia | guardada > detección por navegador |
| Detección (solo páginas públicas) | `navigator.language` que empiece por `es` → español; **cualquier otro valor (o ausente) → inglés** |
| Detección (resto de la aplicación, sin cambios) | `en*` → inglés; cualquier otro → español |
| Persistencia de la detección | Nunca — solo el selector EN/ES explícito escribe la clave |
| Idiomas disponibles | 2 (español, inglés); sin más locales |

## Contenido fijo

| Elemento | Valor |
| --- | --- |
| Fecha "Última actualización" de privacidad y términos | July 15, 2026 / 15 de julio de 2026 (fija en el contenido, versionada con la app) |
| Contacto | `support@missela.app` (enlace `mailto:`) |
| Copyright del pie | © 2026 Missela |
| Tarjetas de características de la landing | 6 |
| Pasos de "cómo funciona" | 3 |

## Enlaces externos (todos `target="_blank" rel="noopener noreferrer"`)

| Destino | URL |
| --- | --- |
| Google API Services User Data Policy (sección 4 de la privacidad) | `https://developers.google.com/terms/api-services-user-data-policy` |
| Revocación de permisos de Google | `https://myaccount.google.com/permissions` |
| Revocación de permisos de Microsoft | `https://account.live.com/consent/Manage` |
| AEPD (sección 8 de la privacidad) | `https://www.aepd.es` |

## Disposición responsive

| Umbral | Efecto |
| --- | --- |
| < 640 px | Tarjetas de la landing en 1 columna; los enlaces centrales de la nav (características / privacidad / términos) se ocultan — quedan logo, selector EN/ES y botón de entrar |
| ≥ 640 px (`sm`) | Tarjetas en 2 columnas; pasos de "cómo funciona" en fila; nav completa |
| ≥ 1024 px (`lg`) | Tarjetas en 3 columnas |

## Qué NO soporta (deliberado)

- **Sin SEO en servidor**: la SPA se renderiza en cliente — no hay SSR ni prerender, así que un crawler sin JavaScript ve el HTML vacío del shell. Aceptado: el objetivo es la revisión OAuth y los visitantes humanos, no el posicionamiento.
- **Sin sitemap.xml, robots.txt propio, meta description ni etiquetas OG** — mismo motivo.
- **Sin `<title>` por página**: las tres páginas conservan el título fijo `MISSELA` de `index.html`; el único escritor de título en runtime sigue siendo el badge de no-leídos de la aplicación ([contador-no-leidos.md](contador-no-leidos.md)).
- **Sin idioma en la URL** (`/en/…`, `?lang=…`): la elección vive solo en el navegador, así que un enlace compartido no fija el idioma del receptor.
- **Sin banner de cookies**: solo existe la cookie de sesión esencial (declarado en la propia política); no hay nada que consentir.
- **Sin captura de pantalla del producto en el hero**: punto abierto aceptado del borrador aprobado — puede añadirse después sin re-verificación mientras no cambie la marca.
- **Sin analítica ni rastreo** de ningún tipo en las páginas públicas (coherente con lo que promete la política).
- **El enlace "Features"/"Características" de la nav solo existe dentro de la landing** (es un ancla local); desde las páginas legales no hay acceso directo a esa sección.
- **El contenido legal es estático y se versiona con la aplicación**: sin CMS, sin historial de versiones visible — cambiar el texto exige un nuevo borrador aprobado y un despliegue.
- **Residual de idioma aceptado**: un navegador ni español ni inglés ve la landing en inglés pero `/login` en español hasta la primera elección explícita (el defecto de la aplicación no se tocó a propósito).
