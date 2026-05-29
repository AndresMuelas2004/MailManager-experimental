# Composición y envío de correos nuevos — límites y topes (MVP)

Catálogo cuantitativo de **hasta dónde llega** la composición y el envío de un correo nuevo: longitudes, reintentos, validaciones de borde y la lista de "lo que NO soporta". El comportamiento y los flujos están en **[../features/composicion-y-envio.md](../features/composicion-y-envio.md)**.

Los límites de **adjuntos** (tamaño por archivo, total del mensaje, número de adjuntos, blocklist de extensiones, atomicidad de la subida) NO se repiten aquí — son compartidos y viven en **[adjuntos.md](./adjuntos.md)**. Esta página solo enlaza a ellos.

---

## 1. Validaciones del contenido (envío directo)

| Campo | Regla exacta | Dónde se aplica | Si falla |
|---|---|---|---|
| Cuenta de origen (`account_id`) | longitud mínima 1 (obligatoria) | Cliente (botón deshabilitado) + backend (Pydantic) | El envío no se permite / `422` |
| Asunto (`subject`) | longitud mínima **1** (no puede ir totalmente vacío) | Backend (Pydantic) | `422` |
| Cuerpo (`body`) | longitud mínima **1** | Backend (Pydantic) | `422` |
| Destinatarios (`recipients`, campo "Para") | lista con **al menos 1** elemento | Cliente (botón deshabilitado) + backend (Pydantic) | `422`; si llega vacía al proveedor → `400 recipients_missing` |
| Forma de cada dirección de "Para" / "Cc" / "Cco" | debe parecer `local@dominio.tld` (regex permisiva, sin IDN ni partes entrecomilladas) | **Solo cliente** | Botón "Enviar" deshabilitado + aviso "Dirección de correo no válida." |

Notas:

- **No hay tope máximo** de longitud para asunto ni cuerpo en el contrato del envío directo (solo el mínimo de 1).
- **No hay límite explícito de número de destinatarios** en la app: el contrato solo exige ≥ 1 en "Para". El techo real lo impone el proveedor (Gmail/Outlook tienen sus propios límites de destinatarios por mensaje y por día), no MailManager.
- La validación de forma de email es **solo cliente**: el backend no revalida la forma de cada dirección. Un cliente manipulado podría enviar una dirección malformada y recibiría el error del proveedor traducido (ver § 3).

---

## 2. Reintentos del envío — asimetría directo vs. borrador

El comportamiento de reintentos depende de **por qué camino** sale el correo:

| Camino de envío | ¿Reintenta? | Intentos | Espera entre intentos |
|---|---|---|---|
| **Envío directo** de "Nuevo mensaje" sin adjuntos (`POST /emails/send`) | **No** | 1 (un solo disparo) | — |
| **Envío de borrador** (al que se reencamina un "Nuevo mensaje" con adjuntos, vía `POST /drafts/{id}/send`) | **Sí**, ante fallos transitorios | **3** intentos totales (`_SEND_DRAFT_MAX_ATTEMPTS = 3`) | **1 s tras el 1.er intento, 2 s tras el 2.º** (`_SEND_DRAFT_RETRY_DELAY * intento`, con `_SEND_DRAFT_RETRY_DELAY = 1 s`). El 3.er intento no espera: si falla, lanza el error. Igual en Gmail y en Outlook por este camino (ambos van por `send_draft_with_attachments`) |

Detalles:

- **El envío directo no reintenta** porque es un disparo único contra el proveedor. Si falla por causa transitoria, el usuario reintenta a mano (el composer queda abierto).
- **El reencaminamiento siempre pasa por `send_draft_with_attachments`** (lo invoca `send_draft` en el backend tanto si hay adjuntos pendientes como si no), así que la espera escalonada `1 s → 2 s` aplica a un "Nuevo mensaje" con adjuntos en ambos proveedores. Existe además una ruta `send_draft` "a secas" con espera **fija de 1 s** (Gmail y Outlook), pero el envío de un correo nuevo nunca la usa.
- **Códigos retryables en el camino de borrador**: Gmail reintenta solo en `429` / `5xx`; Outlook reintenta cualquier error externo del proveedor (su superficie de error es más estrecha). La razón de las esperas escalonadas y de no normalizarlas entre proveedores está en `backend/core/core_guide.md` (sección "Send retry asymmetry").
- **Excepción no-retryable dentro del retryable**: el `429` de Gmail con motivo "user-rate limit exceeded (mail sending)" es el **tope diario** de envío y NO se reintenta (reintentar dentro de la ventana solo quema cuota).
- Atomicidad del envío con adjuntos (atómico en Gmail; por pasos con reanudación de éxito parcial en Outlook): ver **[adjuntos.md](./adjuntos.md)**.

---

## 3. Códigos de error del envío directo

| Situación | Código / estado HTTP | Mensaje al usuario (cliente) |
|---|---|---|
| Lista de destinatarios vacía al llegar al proveedor | `recipients_missing` / **400** | "Destinatario no encontrado" |
| Fallo del proveedor al mandar (rechazo, caída, error inesperado) | `email_send_error` / **502** | "Destinatario no encontrado" |
| Cuenta de origen no encontrada en el mailbox | `account_not_found` / **404** | Error de cuenta |
| Permisos/sesión de la cuenta caducados | `account_not_connected` / **409** | Error de conexión de cuenta |
| Payload inválido (falta asunto/cuerpo/destinatarios, o campo extra) | validación / **422** | Error de validación |

Nota sobre el mensaje al usuario: en el **envío directo**, el cliente (`useDraftPersistence.sendEmailNow`) colapsa **tanto** `recipients_missing` **como** `email_send_error` al **mismo** texto "Destinatario no encontrado" — no hay sub-ramas. Es decir, un `502` por caída del proveedor o por error inesperado también se muestra como "Destinatario no encontrado", aunque no tenga nada que ver con los destinatarios. Es una simplificación conocida del MVP (el código genérico de envío reutiliza el mensaje de destinatarios).

El detalle técnico de un `502` (clase de excepción, error del proveedor) queda **solo en los logs del servidor**, nunca en pantalla.

---

## 4. Entrelazado con borrador y adjuntos (resumen de topes)

| Aspecto | Tope / regla | Detalle en |
|---|---|---|
| Cuándo se crea el "borrador silencioso" | La **primera vez** que se adjunta un archivo en "Nuevo mensaje" / "Nuevo borrador" | [adjuntos.md](./adjuntos.md) |
| Cuenta de origen tras el bootstrap | **Bloqueada** (no se puede cambiar mientras exista el borrador) | [../features/composicion-y-envio.md](../features/composicion-y-envio.md) § 6 |
| Reencaminamiento de "Enviar" con adjuntos | "Enviar" pasa a **enviar borrador** (vuelca cuerpo + asunto + Para + Cc + Cco, sube adjuntos, manda) | [../features/composicion-y-envio.md](../features/composicion-y-envio.md) § 6.2 |
| Caps de adjuntos (25 MB/archivo, 25 MB total, 25 adjuntos, blocklist de 145 extensiones) | Compartidos con la feature de adjuntos | [adjuntos.md](./adjuntos.md) |

---

## 5. Lo que NO soporta (limitaciones aceptadas del MVP)

| No soportado | Porqué breve |
|---|---|
| **Cc / Cco en el envío directo** (correo nuevo SIN adjuntos) | El envío directo (`POST /emails/send`) solo transporta el campo "Para". Para que Cc/Cco lleguen debe existir un borrador (basta con adjuntar algo o guardar el borrador antes de enviar), porque solo el camino de borrador vuelca todos los destinatarios. Asimetría conocida; documentada como trampa en la feature. |
| **Cuerpo rich-text / formato** | El cuerpo es un `<textarea>` plano y el correo se manda siempre como `text/plain`. Pegar HTML envía el marcado en crudo. Un editor enriquecido introduciría un campo de formato aparte; fuera del MVP. |
| **Pegar / insertar imágenes inline al componer** | Consecuencia de lo anterior (cuerpo plano). "Pegar imagen → inline" requiere editor rich. |
| **Reintento automático del envío directo** | El envío directo es de un solo intento; ante fallo transitorio el usuario reintenta a mano. El reintento automático (3 intentos) solo existe en el camino de borrador, que permanece bajo carga más tiempo. |
| **Validación de forma de email en el backend** | La forma de cada dirección se valida solo en el cliente; el backend confía en esa validación y solo comprueba que la lista no esté vacía. Un cliente manipulado recibiría el error del proveedor traducido. |
| **Tope propio de número de destinatarios** | La app no impone un máximo de destinatarios por mensaje; se delega en los límites del proveedor (Gmail/Outlook por mensaje y por día). |
| **Programar envío (send later)** | No hay envío diferido ni programado; "Enviar" manda de inmediato. |
| **Acuse de recibo / confirmación de lectura** | No se solicitan ni se gestionan cabeceras de read-receipt. |
| **Firmas automáticas, plantillas, autoguardado periódico** | El composer no añade firma ni autoguarda cada N segundos; guardar el borrador es una acción explícita del usuario. |

Si los usuarios reportan necesitar algo de lo anterior, hay un plan de fases futuras para añadirlo.
