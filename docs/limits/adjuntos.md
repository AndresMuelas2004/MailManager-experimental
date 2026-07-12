# Adjuntos — límites y topes (MVP)

Catálogo cuantitativo de **hasta dónde llega** la gestión de adjuntos: tamaños, cantidades, TTL, reintentos, concurrencia, umbrales de subida y la lista de "lo que NO soporta". El comportamiento y los flujos están en **[../features/adjuntos.md](../features/adjuntos.md)**.

Salvo que se indique lo contrario, los límites de tamaño/cantidad se aplican **al enviar**, no al recibir: la recepción no tiene filtro de tipo ni de tamaño (ver § 6).

---

## 1. Tamaños y cantidades (envío / composición)

| Límite | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Tamaño máximo por archivo individual | **25 MB** (`25 * 1024 * 1024` = 26 214 400 B) | Cliente (primera línea) + backend (segunda línea) | Es el límite estándar de Gmail. Rechazo inmediato en el composer. |
| Tamaño máximo total del mensaje (cuerpo + todos los adjuntos) | **25 MB**, uniforme Gmail/Outlook | Cliente + backend | El contador "X / 25 MB" no cambia al cambiar la cuenta de origen. |
| Número máximo de adjuntos por correo / borrador | **25** | Cliente + backend | Límite duro; el archivo nº 26 se rechaza. |
| Cuerpo HTTP multipart de subida de un adjunto | **30 MB** (`30 * 1024 * 1024`) → `413 request_too_large` | Backend, antes de leer el cuerpo | Cushion de 5 MB sobre el cap de 25 MB/fichero para cubrir el sobrecoste de las fronteras multipart. Se evalúa contra la cabecera `Content-Length`; las peticiones sin ella (chunked) caen al límite interno de Starlette. |

### 1.1 Por qué el total es uniforme (25 MB) y no asimétrico

Outlook permite por defecto hasta 35 MB y configurable hasta 150 MB. Mantener un techo distinto por proveedor introducía complejidad real (contador que cambia con la cuenta, casos del cambio de cuenta con exceso de adjuntos ya cargados, dos números en pantalla) a cambio de ~10 MB extra que solo aprovechan correos cercanos al techo. Para casos por encima de 25 MB la respuesta correcta sigue siendo Drive/OneDrive — capacidad que la app tampoco implementa (ver § 7).

**Caveat aceptado**: si un tenant Outlook está configurado por debajo de 25 MB (raro; el default histórico es 35 MB), la app no lo detecta y los correos pueden rebotar con NDR. Si está por encima (50/150 MB), la app limita "de más", pero esos clientes tienen Drive/OneDrive para los casos extremos.

---

## 2. Blocklist de extensiones (solo al ENVIAR)

- Es la **unión** de la blocklist oficial de Gmail y la de Microsoft Exchange Online: la regla más estricta de las dos. Garantiza que cualquier archivo que pase la validación llegará al destinatario sin que ningún proveedor lo bloquee.
- Se valida **en el cliente antes de subir** (feedback inmediato) y se **revalida en el backend** (segunda red de seguridad). Motivo de la doble validación: Gmail rechaza síncronamente, pero Outlook devuelve `202 Accepted` y luego manda un NDR diferido — solo validando en cliente se consigue un error inmediato y consistente entre ambos.
- La fuente canónica es la lista del backend; el frontend la espeja en `frontend/src/lib/blocked_extensions.json` (sincronización manual, con test de paridad en CI que detecta divergencias).

Extensiones bloqueadas (lista completa, sin punto inicial; el chequeo es por la extensión tras el último punto, en minúsculas):

```
ade adp apk app appcontent-ms application appref-ms appx appxbundle asp aspx asx
bas bat bgi cab cdxml cer chm cmd cnt com cpl crt csh der diagcab diagcfg diagpkg
dll dmg ex ex_ exe fxp gadget grp hlp hpj hta htc img inf ins iso isp its jar jnlp
js jse ksh lib lnk mad maf mag mam maq mar mas mat mau mav maw mcf mda mdb mde mdt
mdw mdz mht mhtml mjs msc msh msh1 msh1xml msh2 msh2xml mshxml msi msix msixbundle
msp mst msu nsh ops osd pcd pif pl plg prf prg printerexport ps1 ps1xml ps2 ps2xml
psc1 psc2 psd1 psdm1 pssc pst py pyc pyo pyw pyz pyzw reg scf scr sct
settingcontent-ms shb shs sys theme tmp udl url vb vbe vbp vbs vhd vhdx vsmacros vsw
vxd webpnp website ws wsb wsc wsf wsh xbap xll xnk
```

(145 extensiones.)

---

## 3. Descarga: reintentos y política de errores

Aplican cuando el usuario clica un adjunto recibido que aún no está en cache local y hay que ir al proveedor.

| Parámetro | Valor exacto |
|---|---|
| Intentos totales | **3** |
| Espera entre intentos (backoff fijo) | **1 s → 2 s → 4 s** |
| `Retry-After` del proveedor | **Se respeta**: si el proveedor lo envía (típico en throttling de Microsoft Graph), su valor sustituye a la espera por defecto. |
| Códigos **retryables** (se reintenta) | `429`, `500`, `502`, `503`, `504`, y errores de red / `OSError` (timeouts, conexiones cortadas) |
| Códigos **permanentes** (NO se reintenta) | `400`, `401`, `403`, `404`, `410` — se reportan de inmediato |

### 3.1 Mapeo de errores irrecuperables a la UI

| Situación (tras agotar reintentos) | Efecto en la app | Mensaje al usuario |
|---|---|---|
| Adjunto inexistente en el proveedor (`404` / `410`) | Se marca como "no disponible" en la BD; los siguientes clics ya **no** llaman al proveedor (corte de raíz) | "Este adjunto ya no está disponible en el servidor" |
| Sin permisos (`403`) | Se registra el detalle para investigación | "No se pudo acceder al adjunto" |
| Proveedor caído (`5xx` persistente) | NO se marca como inutilizable; botón de reintento | "Inténtalo de nuevo en unos minutos" |

---

## 4. TTL del cache de adjuntos descargados

| Parámetro | Valor exacto |
|---|---|
| Vida útil del binario | **30 días desde el último acceso** (`last_accessed_at`) |
| Cuándo arranca el contador | `last_accessed_at` solo se sella **al completar con éxito una descarga** (vía `BackgroundTask` tras el stream), no al insertar el binario. La purga exige `last_accessed_at IS NOT NULL`, así que un binario que nunca llegó a servirse por completo queda fuera del barrido (no es candidato a purga). |
| Qué se purga | Solo el **binario**; la fila de metadata se conserva (el flag "descargado" vuelve a `false` y el siguiente clic re-descarga) |
| Mecanismo de purga | **Manual**: endpoint admin `POST /admin/attachments/purge`, sin cron en el MVP |
| Autenticación de la purga | Token en variable de entorno `ATTACHMENTS_PURGE_TOKEN`, comparado contra la cabecera `X-Admin-Token`. Tres estados: env var ausente → `503 purge_disabled`; cabecera ausente/incorrecta → `401 invalid_admin_token`; correcta → ejecuta y devuelve `{purged_count, freed_bytes}` |
| Limpieza automática vía FK | Desconectar una cuenta o borrar un correo **sí** limpia en cascada sin intervención manual (independiente del TTL) |

Nota de memoria: el binario se carga completo en RAM del proceso al servirlo (el `bytea` se lee entero en el `SELECT`; el `StreamingResponse` solo trocea la salida hacia el navegador en chunks de **64 KB**, no es streaming desde la BD). El cap de 25 MB/fichero acota ese coste por descarga.

---

## 5. Concurrencia y umbrales de subida

| Parámetro | Valor exacto | Lado |
|---|---|---|
| Descargas concurrentes (cola del visor) | **2 máximo**; el resto en cola ("En cola") | Frontend |
| Concurrencia de Microsoft Graph por buzón | **4 peticiones simultáneas** por par `(app, buzón)` | Límite del proveedor que motiva el tope de 2 descargas y el lazy push |
| Gmail — umbral de envío resumable | MIME total **> 5 MB** (`5 * 1024 * 1024`) usa `uploadType=resumable`; en o por debajo de 5 MB, envío simple | Backend |
| Gmail — tamaño de chunk resumable | **4 MB** (`4 * 1024 * 1024`, múltiplo de 256 KB) | Backend |
| Outlook — umbral de subida por adjunto | **≥ 3 MB** (`3 * 1024 * 1024`) usa `createUploadSession` + PUT por trozos; por debajo de 3 MB, `POST /attachments` directo | Backend (decisión **por adjunto**, no por mensaje) |
| Reintentos del envío de borrador | **3 intentos**, espera base **1 s** | Backend (Gmail reintenta solo en `429`/`5xx`; Outlook reintenta todo `EmailExternalAPIError`) |
| Fetch de borradores por sincronización | **500 borradores** por cuenta (los más recientes) | Backend (ambos proveedores) — ver [borradores.md](./borradores.md) |

### 5.1 Atomicidad del envío con adjuntos

- **Gmail**: envío **atómico** — una sola llamada que reconstruye el MIME (`drafts.send`, o su variante resumable si el MIME total supera 5 MB).
- **Outlook**: **no atómico** — sube cada adjunto (simple o por sesión según el umbral de 3 MB), persiste cada `provider_attachment_id` en cuanto vuelve, y luego dispara el envío. En fallo a mitad, los éxitos parciales quedan persistidos **antes** de re-lanzar el error, de modo que un reintento salta los ya subidos (resume de éxito parcial, D-27).

---

## 6. Recepción: sin límites de filtrado

- **No hay blocklist al recibir**: todos los adjuntos que lleguen se muestran y se pueden descargar, incluidos tipos "peligrosos" (`.exe`, etc.). La postura es la de Gmail web; la protección se delega al sistema operativo / navegador del usuario al abrir el fichero.
- **No hay tope de tamaño de descarga** distinto del que ya impuso el proveedor de origen al recibir el correo.

### 6.1 Resolución del nombre y el tipo del adjunto recibido

El comportamiento está en [../features/adjuntos.md](../features/adjuntos.md) § 2.6 y § 7.1. Aquí van los valores y conjuntos exactos.

**Tipos declarados tratados como "genéricos"** (cuando el proveedor declara uno de estos, el tipo se deduce de la extensión del nombre en lugar de respetarse):

| Valor exacto |
|---|
| `""` (vacío / ausente) |
| `application/octet-stream` |
| `application/binary` |
| `binary/octet-stream` |

La comparación es **insensible a mayúsculas**. Cualquier otro valor cuenta como tipo **específico** y se conserva **tal cual** (preservando mayúsculas/minúsculas: ya no se fuerza minúsculas en Outlook). Si ni el tipo declarado es específico ni la extensión resuelve nada, el fallback final es `application/octet-stream`.

**Extensiones registradas explícitamente** porque la tabla `mimetypes` de la imagen base (Linux, Python 3.12) las devuelve como desconocidas; sin este registro, un `factura.xlsx` declarado genérico seguiría resolviendo a octet-stream:

| Extensión | Tipo asignado |
|---|---|
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| `.rar` | `application/vnd.rar` |
| `.7z` | `application/x-7z-compressed` |
| `.webp` | `image/webp` |

El registro es idempotente: las extensiones que la tabla ya conoce de fábrica (`.pdf`, `.png`, …) no se tocan.

**Recuperación del nombre desde cabeceras (solo Gmail).** Cuando Gmail deja el nombre del adjunto vacío, se rescata en este **orden de precedencia fijo**:

1. `Content-Disposition: …; filename=…`
2. `Content-Type: …; name=…`

Se decodifican nombres RFC 2231 (`filename*=utf-8''…`) y RFC 2047 (`=?utf-8?B?…?=`). Si ninguna cabecera lleva un nombre usable, se mantiene el nombre sintético (`cid` o `attachment`). **Outlook no tiene este rescate**: Graph no expone las cabeceras MIME crudas en el listado de adjuntos, así que su única fuente de nombre es el campo `name` del adjunto.

---

## 7. Lo que NO soporta (limitaciones aceptadas del MVP)

| No soportado | Porqué breve |
|---|---|
| **Antivirus / escaneo de malware** | Fuera de scope MVP; la protección se delega al SO/navegador del usuario al abrir el fichero. |
| **Verificación de "magic bytes"** (tipo real vs. extensión declarada) | Un `.pdf` que sea un ejecutable renombrado pasaría el filtro; detectar esto exige inspección de contenido, fuera del MVP. |
| **Deduplicación de binarios** | El mismo PDF en tres cuentas ocupa tres copias. Simplicidad del MVP; se reevaluará si el cache supera ~50 GB. |
| **Descarga masiva / ZIP** | No existe "descargar todos" ni empaquetado ZIP; hay que clicar uno por uno. |
| **Vista previa inline de no-imágenes** | PDFs y similares se descargan, no se renderizan dentro de la app. |
| **Adjuntos enormes vía enlace a Drive / OneDrive** | La app no genera enlaces para archivos > 25 MB; simplemente los rechaza. |
| **Métricas custom (Prometheus / OpenTelemetry)** | Solo logs estructurados en operaciones críticas (descarga del proveedor, errores). |
| **Pegar imágenes inline en el cuerpo al componer** | El cuerpo es HTML con formato (negrita, cursiva, subrayado, listas, enlaces), pero el editor y el saneador de salida no admiten imágenes incrustadas en el texto (`<img>` ni protocolos `cid` / `data`). Una imagen pegada/arrastrada en el cuerpo se descarta; los ficheros se mandan como **adjuntos**. Fuera del MVP. |
| **Validación de `Origin` / `Referer` en la descarga** | El endpoint usa cookie de sesión y verifica pertenencia, pero no añade defensa extra contra exfiltración cross-site desde una pestaña que ya tenga sesión válida. Mejora futura. |
| **Purga por TTL automática (cron)** | La purga de 30 días se ejecuta manualmente vía endpoint admin con token; se sustituirá por un cron más adelante. |

Si los usuarios reportan necesitar algo de lo anterior, hay un plan de fases futuras para añadirlo.
