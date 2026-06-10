# Responder, Responder a todos y Reenviar — límites y topes (MVP)

Catálogo cuantitativo de **hasta dónde llega** Responder / Responder a todos / Reenviar: requisitos de enhebrado, razones de error, reglas por proveedor, scopes OAuth, idempotencia, y la lista de "lo que NO soporta". El comportamiento y los flujos están en **[../features/responder-y-reenviar.md](../features/responder-y-reenviar.md)**.

Los topes de **tamaño / cantidad de adjuntos** (25 MB por archivo, 25 MB totales, 25 adjuntos, reintentos de envío, atomicidad Gmail vs. Outlook) son los compartidos con la gestión de adjuntos y **no se repiten aquí**: están en **[adjuntos.md](adjuntos.md)**. Esta página recoge solo lo propio de responder/reenviar.

---

## 1. Enhebrado (threading): requisito por proveedor

| Proveedor | Cómo se enhebra | Quién garantiza la coherencia |
|---|---|---|
| **Gmail** | **Triple requisito simultáneo**: (1) `threadId` igual al del original, (2) `In-Reply-To` / `References` referencian el `Message-ID` original (RFC 5322), (3) asunto coincide tras normalizar prefijos `Re:` / `Fwd:`. | La **app**, validando en local sobre el original ya leído y **antes de crear el borrador** en el proveedor (sin ninguna llamada mutante de por medio). |
| **Outlook** | Endpoints dedicados `createReply` / `createReplyAll` / `createForward` en **una sola llamada** (subject + cuerpo + destinatarios juntos). Fija `conversationId` server-side. | El **proveedor**. La app no valida threading en Outlook. |

- En Outlook, `thread_id` / `in_reply_to` / `references` se aceptan en el contrato por simetría con Gmail pero **se descartan en el envío** (Graph no ofrece inyección de cabeceras ni en `createReply*` ni en `/send`).
- La validación del **asunto** (requisito 3 de Gmail) se aplica **solo a `reply` / `reply_all`**, NUNCA a `forward` (un reenvío cambia el asunto a `Fwd:` legítimamente y Gmail no exige coincidencia).
- El guard de Gmail se ejecuta **solo si se cumplen las tres condiciones a la vez**: proveedor Gmail, acción `reply` / `reply_all`, **y** el original trae un `threadId` no vacío. Si el original no tiene `threadId` (mensaje importado / malformado, caso raro) el guard se **omite** por completo y la respuesta se crea como borrador suelto, sin enhebrar — no hay error.

### 1.1 Razones de error del guard de Gmail

Cuando la validación local de Gmail falla, el usuario recibe un error determinista con una de estas razones exactas:

| Razón (`detail.reason`) | Cuándo |
|---|---|
| `thread_id_mismatch` | El `threadId` de la respuesta no coincide con el del original (o falta alguno de los dos). |
| `message_id_not_referenced` | El `Message-ID` original no aparece en `In-Reply-To` ni en `References` (o el original no tiene `Message-ID`). |
| `subject_mismatch` | El asunto de la respuesta no coincide con el original tras quitar prefijos `Re:` / `Fwd:` y normalizar (minúsculas). |

---

## 2. Destinatarios calculados (regla R-10 incluida)

| Acción | Para (To) | CC |
|---|---|---|
| **Responder** | `Reply-To` del original si existe; si no, el remitente (`From`). | Vacío. |
| **Responder a todos** | Igual que Responder. | (`To` ∪ `CC` del original) − cuenta propia − destinatario primario, sin duplicados (case-insensitive). |
| **Reenviar** | Vacío (los teclea el usuario). | Vacío. |
| **Responder a un correo propio** (original en `SENT`) | Los `To` originales (no el remitente, que eres tú). | Igual regla de Reply All si aplica. |

- **Reply-To vacío o solo espacios** colapsa al `From` del remitente.
- **Filtro "quítame del CC"**: si la dirección de la cuenta de origen no se capturó al conectar la cuenta (puede ser `NULL`), el filtro se **desactiva silenciosamente** y el usuario puede aparecer en su propio CC. Mitigación: calentar la caché de la dirección; **nunca** se elimina el CC entero por compensar.

---

## 3. Asunto

| Acción | Prefijo | Prefijos ya reconocidos (no se duplican) |
|---|---|---|
| Responder / Responder a todos | `Re: ` | `Re:`, `AW:` (alemán), `SV:` (sueco), tolerando `Re :` con espacio. |
| Reenviar | `Fwd: ` | `Fw:`, `Fwd:`, `RV:`, `Reenv:`. |

- El prefijo original se **preserva** (mayúsculas + espaciado), no se reescribe a forma canónica.
- **No soportado**: variantes numeradas tipo `Re[2]:` — se tratan como parte del asunto (fallo benigno: el hilo se forma igualmente por `threadId` / `conversationId`).

---

## 4. Cuerpo citado

| Parámetro | Valor exacto |
|---|---|
| Formato de la cita | **HTML** (`build_quoted_body_html`): línea(s) de atribución en `<p>` + el original dentro de un `<blockquote>`. El original **se degrada primero a texto** (mismo degradador `_html_to_text`) y luego se re-envuelve; la cita **no** ingiere el HTML del remitente. |
| Estilo del recuadro de cita (`<blockquote style>`) | `margin:0 0 0 .8ex; border-left:2px solid #ccc; padding-left:1ex; color:#555;` — barra lateral gris + sangría. Sus propiedades CSS deben estar en la allowlist del saneador de salida (ver [composicion-y-envio.md](./composicion-y-envio.md)) para sobrevivir al saneado en persistir/enviar. |
| Idioma de la atribución de cita | **Español fijo** (p. ej. *"El 23 de mayo de 2026 a las 14:32, … escribió:"*). i18n fuera del MVP. |
| Zona horaria de la fecha | **UTC** (sin conversión a hora local). Fuera del MVP. |
| Estilo Responder / Resp. a todos | `<p>` de atribución (fecha + remitente) seguido del `<blockquote>` con el original. **Ya no** se prefija cada línea con `> ` (el recuadro de cita sustituye al prefijo del antiguo camino en texto plano). |
| Estilo Reenviar | `<p>` con `---------- Mensaje reenviado ----------` + `De / Fecha / Asunto / Para / Cc` (separadas por `<br>`), seguido del `<blockquote>` con el original. |
| Posición del cursor | El usuario escribe **encima** del bloque de cita (el composer siembra el HTML con la atribución + cita y el cursor queda arriba). |
| Recorte del original citado (solo en degradación HTML→texto) | **50 000 caracteres** máximo; el exceso se corta con un marcador `[...truncado...]`. |
| Saltos en blanco consecutivos (solo en degradación HTML→texto) | Se colapsan a un máximo de **2** (mantiene párrafos sin inflar la cita). |
| Original que ya trae parte en texto plano | Se cita **tal cual** (solo se recorta el espacio sobrante de los extremos): **no** se le aplica el recorte de 50 000 caracteres ni el colapso de saltos. Esas dos reglas son exclusivas del camino HTML→texto. |

---

## 5. Herencia de adjuntos al Reenviar

| Proveedor | Mecanismo | ¿Descarga binarios al heredar? | ¿Re-sube al enviar? |
|---|---|---|---|
| **Outlook** | Copia **server-side** en la creación del borrador de reenvío. | No (las bytes viven en el borrador del proveedor). | No (ya están allí). |
| **Gmail** | Endpoint de copia que **descarga + re-adjunta** cada adjunto (reusa cache local si lo hay). | Sí (en fallo de cache; si está cacheado, no). | Sí, en el envío atómico normal. |

- **Responder / Responder a todos NO heredan adjuntos** (semántica esperada). Solo Reenviar.
- El frontend llama al endpoint de copia **siempre** tras crear un reenvío; para Outlook es **no-op** (`copied_count = 0`, devuelve los chips ya heredados).
- Topes de tamaño/cantidad de los adjuntos copiados (25 MB/archivo, 25 MB totales, 25 adjuntos): ver **[adjuntos.md](adjuntos.md)**. Si la copia rebasaría un cap, el adjunto se reporta como "saltado".

### 5.1 Idempotencia y tolerancia a fallos del endpoint de copia (R-12)

- **Siempre responde 200**, incluso con fallos parciales. Cada adjunto no copiado va en un array `skipped[]` con `{filename, reason}` — nunca aborta el reenvío entero.
- **Idempotente**: una copia ya realizada se salta en el reintento (se recuerda el adjunto-origen de cada copia). Un corte de red + reintento no duplica chips.
- **Fallo total de la llamada**: el composer se abre igualmente (fallo blando); el usuario re-adjunta a mano.

Motivos de "saltado" (`reason`) posibles:

| `reason` | Significado |
|---|---|
| `already_copied` | Ya estaba copiado en este borrador (idempotencia R-12). |
| `unavailable_at_source` | El adjunto origen está marcado no disponible, o el proveedor devolvió 404/410 al descargarlo. |
| `attachment_limit_exceeded` | Añadirlo superaría el tope de 25 adjuntos del borrador. |
| `message_size_exceeded` | Añadirlo superaría el tope de 25 MB acumulados del borrador. |
| `provider_unavailable` | El proveedor falló al servir el binario (no 404/410). |
| `blob_lookup_failed` | Error leyendo el binario cacheado en la BD local. |

---

## 6. Scopes OAuth requeridos

| Proveedor | Crear borrador (reply/replyAll/forward) | Enviar | Nota |
|---|---|---|---|
| **Outlook** | `https://graph.microsoft.com/Mail.ReadWrite` | `https://graph.microsoft.com/Mail.Send` | El flujo completo necesita **ambos** en el mismo token. Si falta uno, el 403 aparece en momentos distintos (creación vs. envío). |
| **Gmail** | `https://www.googleapis.com/auth/gmail.modify` (cubre crear y enviar borradores) | mismo scope | Un único scope cubre todo el ciclo. |

- En Outlook, **toda** llamada a `createReply` / `createReplyAll` / `createForward` (y la posterior de envío) repite la cabecera `Prefer: IdType="ImmutableId"`; Graph no recuerda esa preferencia entre llamadas y, sin ella, el id se reinterpreta como transitorio y devuelve 404.

---

## 7. Reglas del wire de Outlook (createReply*)

- El cuerpo de la petición es **XOR-estricto**: solo se acepta `{"message": {...}}`. Añadir un `comment` hermano o un `toRecipients` a nivel raíz produce un **400 garantizado** por Graph.
- **`reply` / `reply_all` pre-rechazan destinatarios vacíos** en el cliente (Graph exige al menos un `toRecipients`): error local determinista en vez de pagar cuota por un 400 seguro.
- **`forward` admite destinatarios vacíos** al crear (el composer los rellena después; Graph deja el borrador en Borradores sin destinatario).

---

## 8. Persistencia de los metadatos de respuesta (drafts, migración 0029)

- El borrador guarda seis columnas de respuesta: `reply_kind`, `reply_to_message_id`, `reply_to_account_id`, `thread_id`, `in_reply_to`, `references_header`. Todas **nullable**.
- Son la **única fuente de verdad** en el envío: el backend las lee de la fila local; el frontend **no** las reenvía en el cuerpo del envío (evita que un cliente manipulado altere el enhebrado).
- La sincronización de borradores (`sync_drafts`) trae los borradores del proveedor **sin** estos campos (ni Gmail ni Outlook los exponen como propiedades de borrador), así que quedan `NULL` en borradores sincronizados; un upsert preserva los valores locales en lugar de pisarlos.
- **Sin FK** de `reply_to_message_id` / `reply_to_account_id` a `email_metadata`: si el original se borra, el borrador debe seguir válido (referencia blanda).
- Reenvío bloqueado a la **misma cuenta** en el MVP (las columnas son account-scoped por si un futuro reenvío cross-account se quiere añadir).

---

## 9. Lo que NO soporta (limitaciones aceptadas del MVP)

| No soportado | Porqué breve |
|---|---|
| **Aviso al editar el asunto de un reenvío de Outlook** | Graph reasigna `conversationId` al guardar y el envío se desengancha del hilo en Outlook web. Aceptado; la app no avisa. Gmail no lo sufre (un reenvío puede iniciar hilo nuevo). |
| **Distintivo "este correo fue reenviado"** | El "Forwarded" de Gmail web es solo UI, no lo expone su API; Outlook tampoco da un equivalente fiable. Requeriría heurística sobre la cadena `References`. |
| **Reenvío entre cuentas distintas (cross-account)** | Bloqueado a la cuenta del original en el MVP; el esquema ya está preparado (columnas account-scoped) para añadirlo. |
| **Variantes de prefijo numeradas (`Re[2]:`)** | No se reconocen como prefijo; se tratan como parte del asunto. Fallo benigno (el hilo se forma por `threadId`/`conversationId`). |
| **i18n y zona horaria local en la cita** | La cabecera de cita es español fijo y la fecha va en UTC. Fuera del MVP (R-05). |
| **Preservar el formato HTML del original en la cita** | El cuerpo es HTML (editor enriquecido) y la cita va dentro de un `<blockquote>`, pero el **contenido citado** se degrada a texto antes de envolverlo: no se conserva el formato original del remitente (negritas, tablas, imágenes del original). Es deliberado para no arrastrar HTML arbitrario al editor restringido. |
| **Deduplicar bytes en herencia Outlook con nombres repetidos** | Dos adjuntos del original con el mismo nombre pueden provocar una re-subida redundante en el envío (caso vanishingly raro, sin pérdida de datos). |
| **Edición del threading desde el cliente al enviar** | Los seis campos de respuesta se leen de la fila local, no del cuerpo del envío; un cliente no puede sobrescribir el enhebrado (es una protección, no una carencia funcional). |

Si los usuarios reportan necesitar algo de lo anterior, hay un plan de fases futuras para añadirlo.
