# Composición y envío de correos nuevos — límites y topes (MVP)

Catálogo cuantitativo de **hasta dónde llega** la composición y el envío de un correo nuevo: longitudes, reintentos, validaciones de borde y la lista de "lo que NO soporta". El comportamiento y los flujos están en **[../features/composicion-y-envio.md](../features/composicion-y-envio.md)**.

Los límites de **adjuntos** (tamaño por archivo, total del mensaje, número de adjuntos, blocklist de extensiones, atomicidad de la subida) NO se repiten aquí — son compartidos y viven en **[adjuntos.md](./adjuntos.md)**. Esta página solo enlaza a ellos.

---

## 1. Validaciones del contenido (envío directo)

| Campo | Regla exacta | Dónde se aplica | Si falla |
|---|---|---|---|
| Cuenta de origen (`account_id`) | longitud mínima 1 (obligatoria) | Cliente (botón deshabilitado) + backend (Pydantic) | El envío no se permite / `422` |
| Asunto (`subject`) | longitud mínima **1** (no puede ir totalmente vacío) | Backend (Pydantic) | `422` |
| Cuerpo (`body`) | longitud mínima **1**, máxima **1 000 000** caracteres; es **HTML** del editor enriquecido | Cliente (aviso + botones bloqueados) + backend (Pydantic) | `422` |
| Destinatarios (`recipients`, campo "Para") | lista con **al menos 1** elemento | Cliente (botón deshabilitado) + backend (Pydantic) | `422`; si llega vacía al proveedor → `400 recipients_missing` |
| Forma de cada dirección de "Para" / "Cc" / "Cco" | debe parecer `local@dominio.tld` (regex permisiva, sin IDN ni partes entrecomilladas) | **Solo cliente** | Botón "Enviar" deshabilitado + aviso "Dirección de correo no válida." |

Notas:

- **Sí hay tope máximo del cuerpo**: **1 000 000** caracteres (`max_length=1_000_000` en `EmailSendRequest.body`, idéntico en `DraftCreate.body` / `DraftUpdate.body`). El asunto, en cambio, **no tiene máximo** (solo mínimo de 1). El detalle de cómo se hace cumplir el tope del cuerpo está en § 1.1.
- **No hay límite explícito de número de destinatarios** en la app: el contrato solo exige ≥ 1 en "Para". El techo real lo impone el proveedor (Gmail/Outlook tienen sus propios límites de destinatarios por mensaje y por día), no MailManager.
- La validación de forma de email es **solo cliente**: el backend no revalida la forma de cada dirección. Un cliente manipulado podría enviar una dirección malformada y recibiría el error del proveedor traducido (ver § 3).

### 1.1 Tope de tamaño del cuerpo enriquecido (~1 MB) — quién lo hace cumplir

| Capa | Qué hace | Mensaje / efecto |
|---|---|---|
| **Cliente** (`bodyError` en `useDraftComposer`) | Compara la longitud del HTML del editor contra **1 000 000**; es la **defensa real** en el navegador. | Aviso en línea "El mensaje es demasiado grande. Reduce su tamaño." (código `body_too_large`) y **bloquea las tres acciones**: "Enviar", "Guardar borrador" y "Enviar borrador". |
| **Backend** (Pydantic `max_length=1_000_000`) | Enforcement **autoritativo**: rechaza el payload con `422` si el cuerpo supera el tope. | `422`. Si el cliente fuera manipulado y se saltara el aviso, el frontend reescribe ese `422` al mismo texto en español "El mensaje es demasiado grande. Reduce su tamaño." (`useDraftPersistence.toComposerError`). |
| **Zod** (`.max(1_000_000)` en los esquemas de *request*) | **Solo documenta** el contrato y refina el tipo. | **No** se ejecuta en runtime — `request<T>()` valida respuestas, nunca cuerpos de petición. |

- El gate en el cliente cubre las **tres** acciones (no solo "Enviar") a propósito: cuando un "Nuevo mensaje" con adjuntos reencamina "Enviar" al envío de borrador, el bloqueo de tamaño debe seguir vigente por el camino de borrador.
- La cifra **1 000 000** es la misma que el tope del derivado en texto plano (`html_to_plain_text_alternative`, `max_chars=1_000_000`), pero en la práctica ese recorte nunca se alcanza porque el cuerpo ya viene acotado en el límite de esquema.

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
| Cuerpo por encima del tope de **1 000 000** caracteres | validación (Pydantic `max_length`) / **422** | "El mensaje es demasiado grande. Reduce su tamaño." (reescrito desde el `422` por `toComposerError`) |
| Payload inválido (falta asunto/cuerpo/destinatarios) | validación / **422** | Error de validación |

Nota sobre el mensaje al usuario: en el **envío directo**, el cliente (`useDraftPersistence.sendEmailNow`) colapsa **tanto** `recipients_missing` **como** `email_send_error` al **mismo** texto "Destinatario no encontrado" — no hay sub-ramas. Es decir, un `502` por caída del proveedor o por error inesperado también se muestra como "Destinatario no encontrado", aunque no tenga nada que ver con los destinatarios. Es una simplificación conocida del MVP (el código genérico de envío reutiliza el mensaje de destinatarios).

Nota sobre el `422` por tamaño: a diferencia del resto de validaciones (que el usuario ve como un "Error de validación" genérico), el `422` por cuerpo demasiado grande **se traduce** a un mensaje legible en español porque su forma de error es la de FastAPI (`{detail: [...]}`, no el sobre `{error:{code,message}}`); sin esa traducción se mostraría una cadena opaca en inglés. En la práctica este `422` casi nunca se alcanza: el aviso en línea del cliente (§ 1.1) bloquea el envío antes.

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

## 5. Editor de texto enriquecido: formato admitido y saneamiento

El cuerpo es **HTML** producido por el editor (TipTap con esquema restringido). Se sanea en el servidor con una allowlist **deliberadamente estricta** —distinta y mucho más cerrada que la del visor de correo entrante— en el límite de confianza (`api.services.outbound_html_pipeline.sanitize_outbound_html`), aplicada en crear borrador, editar borrador y envío directo. El borrador persiste **ya saneado**: el mismo valor limpio se usa para la llamada al proveedor y para la fila local (nunca divergen), así que el envío posterior de un borrador no vuelve a sanear.

| Aspecto | Valor exacto |
|---|---|
| Etiquetas permitidas | `p`, `br`, `strong`, `b`, `em`, `i`, `u`, `ul`, `ol`, `li`, `a`, `blockquote` (12). Cualquier otra se elimina **conservando su texto** (`strip=True`). |
| Atributos permitidos | `<a>`: `href`, `title`, `target`, `rel`. `<blockquote>`: `style`. **Ningún otro** atributo ni tag puede llevar `style` (los estilos pegados en párrafos / spans se caen). |
| Propiedades CSS permitidas (solo en `<blockquote>`) | `margin`, `margin-left`, `padding`, `padding-left`, `border-left`, `color` (6). Es justo el vocabulario del recuadro de cita; debe mantenerse en lockstep con el estilo que genera `build_quoted_body_html`. |
| Protocolos de enlace permitidos | `http`, `https`, `mailto`. **No** `cid` ni `data` (las imágenes inline en el cuerpo saliente están fuera de alcance). |
| Endurecimiento de enlaces | Todo `<a>` que sobreviva se fuerza a `target="_blank"` y `rel="noopener noreferrer nofollow"`. |
| Comportamiento ante error del saneador | **Fail-soft**: ante cualquier fallo inesperado devuelve la entrada sin tocar (nunca lanza un error de dominio); el editor del cliente ya restringió el esquema. |
| Validación de URL del enlace en el cliente | El popover de enlace exige un esquema explícito de la lista `http`/`https`/`mailto` antes de crear el enlace; un esquema relativo o sin `//` se rechaza con "Introduce una URL http(s) o mailto válida.". |

> El saneador de **salida** (`sanitize_outbound_html`, allowlist estricta) NO es el mismo que el del **visor entrante** (`sanitize_email_html` / pipeline de `email_html_pipeline`, permisivo para newsletters de terceros). No son intercambiables; las etiquetas/atributos/CSS del visor entrante están en [visualizacion-de-correos.md](./visualizacion-de-correos.md).

---

## 6. Lo que NO soporta (limitaciones aceptadas del MVP)

| No soportado | Porqué breve |
|---|---|
| **Cc / Cco en el envío directo** (correo nuevo SIN adjuntos) | El envío directo (`POST /emails/send`) solo transporta el campo "Para". Para que Cc/Cco lleguen debe existir un borrador (basta con adjuntar algo o guardar el borrador antes de enviar), porque solo el camino de borrador vuelca todos los destinatarios. Asimetría conocida; documentada como trampa en la feature. |
| **Imágenes incrustadas en el cuerpo** (pegar / arrastrar una imagen dentro del texto) | El saneador de salida no admite `<img>` ni los protocolos `cid` / `data` en el cuerpo. Soportarlas exigiría subir y referenciar las bytes inline; fuera del MVP (esta primera versión solo da formato de texto). |
| **Colores y tamaños de letra** | El editor no expone color ni tamaño y el saneador descarta `style` en todo lo que no sea `<blockquote>`; cualquier color/tamaño pegado desde otra app se limpia. Fuera del MVP. |
| **Tablas** | Ni el editor las crea ni el saneador admite `<table>`; una tabla pegada se aplana a su texto. Fuera del MVP. |
| **Pegar estilos complejos de Word / Excel** | Al pegar contenido con formato rico solo sobrevive el formato soportado (negrita, cursiva, subrayado, listas, enlaces); el resto se limpia en silencio (no es un error). |
| **Reintento automático del envío directo** | El envío directo es de un solo intento; ante fallo transitorio el usuario reintenta a mano. El reintento automático (3 intentos) solo existe en el camino de borrador, que permanece bajo carga más tiempo. |
| **Validación de forma de email en el backend** | La forma de cada dirección se valida solo en el cliente; el backend confía en esa validación y solo comprueba que la lista no esté vacía. Un cliente manipulado recibiría el error del proveedor traducido. |
| **Tope propio de número de destinatarios** | La app no impone un máximo de destinatarios por mensaje; se delega en los límites del proveedor (Gmail/Outlook por mensaje y por día). |
| **Programar envío (send later)** | No hay envío diferido ni programado; "Enviar" manda de inmediato. |
| **Acuse de recibo / confirmación de lectura** | No se solicitan ni se gestionan cabeceras de read-receipt. |
| **Plantillas y autoguardado periódico** | El composer no ofrece plantillas reutilizables ni autoguarda cada N segundos; guardar el borrador es una acción explícita del usuario. (La **firma por cuenta** sí existe y se inserta sola — es funcionalidad propia, ver abajo.) |

> **Firmas: ahora SÍ.** El composer **sí inserta una firma por cuenta** al redactar/responder/reenviar (con el mismo vocabulario de formato y saneador de esta página). Es una funcionalidad propia, documentada aparte en [../features/firma.md](../features/firma.md) / [firma.md](./firma.md); este catálogo solo describe el editor y el envío del cuerpo.

Si los usuarios reportan necesitar algo de lo anterior, hay un plan de fases futuras para añadirlo.
