# Firma de correo — límites y topes (MVP)

Catálogo cuantitativo de **hasta dónde llega** la firma de correo: tope de tamaño, dónde se almacena, las reglas exactas de inserción y la lista de "lo que NO soporta". El comportamiento y los flujos están en **[../features/firma.md](../features/firma.md)**.

El **vocabulario de formato admitido** y el saneador que lo recorta son los **mismos** que el del cuerpo redactado y no se repiten aquí en detalle: la allowlist exacta (etiquetas, atributos, propiedades CSS, protocolos de enlace) vive en **[composicion-y-envio.md](./composicion-y-envio.md)** § 5. Esta página solo recoge lo **propio** de la firma y enlaza al resto.

---

## 1. Topes numéricos propios

| Límite | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Longitud máxima de la firma | **10 000 caracteres** | Servidor (`AccountUpdate.signature_html`, `max_length=10_000`, **autoritativo**) + cliente (`SIGNATURE_MAX_CHARS = 10_000` en `AccountSignatureRow`, aviso "demasiado larga" y botón "Guardar" deshabilitado) | Es longitud de **HTML**, no de texto visible. Pasarse en el servidor es un `422`. |
| Longitud mínima de la firma | **Sin mínimo** | — | Una firma vacía (`""`) es válida y significa **borrar la firma**. No confundir con la **ausencia** del campo (`null`), que la deja intacta (ver § 2). |
| Firma por cuenta | **Una** firma por cuenta conectada | Columna por cuenta (ver § 3) | No hay varias firmas seleccionables por cuenta (ver § 5). |

No hay TTL, reintentos, concurrencia ni timeouts propios de la firma. Guardarla es una operación de base de datos local de un solo paso (sin llamada al proveedor), montada sobre el endpoint de actualización de cuenta ya existente (ver § 4).

> **Nota sobre Zod (cliente).** El esquema de *request* del cliente declara `signature_html` con `.max(10_000)`, pero **solo documenta el contrato y refina el tipo**: no se ejecuta en runtime (la validación del cliente valida respuestas, nunca cuerpos de petición). La guarda real del cliente es la del editor (`SIGNATURE_MAX_CHARS`); la autoritativa es la del servidor.

---

## 2. Semántica de los tres estados del campo

La firma se transporta en el campo `signature_html` del payload de actualización de cuenta. Tiene **tres estados** y la distinción es load-bearing:

| Valor enviado | Significado | Efecto |
|---|---|---|
| **Ausente** (`null` / clave omitida) | "No tocar la firma" | La firma guardada se queda como estaba. |
| **`""`** (cadena vacía) | "Borrar la firma" | La firma se borra (la cuenta pasa a no tener firma). `""` se sanea a `""`. |
| **Texto HTML** | "Esta es la firma" | Se persiste **ya saneada** con el saneador de salida (ver § 5). |

- Por eso `signature_html` **no tiene `min_length`** en el esquema: un `""` debe ser un valor válido (borrar), no un error de validación. Es la inversa del patrón de otros campos de filtro donde `""` se prohíbe.
- En la **respuesta** de cuenta (`AccountOut`), `signature_html` **siempre se serializa** (emite `null` cuando la cuenta no tiene firma); **nunca** se omite la clave. Por eso el cliente la declara *nullable*, no *opcional*.

---

## 3. Almacenamiento

| Aspecto | Valor exacto |
|---|---|
| Dónde vive | Columna **`accounts.signature_html`** (`TEXT`, *nullable*, `DEFAULT NULL`) |
| Migración | **0038** (`0038_add_signature_html_to_accounts`) — `ADD COLUMN IF NOT EXISTS`, puramente aditiva |
| Invalidación de caché | **Ninguna.** A diferencia de otras migraciones de la zona de correo, 0038 no trunca ni invalida ningún caché (no afecta a cuerpos ni adjuntos cacheados) |
| Cifrado | **No.** Se guarda como texto plano, igual que la etiqueta o la dirección de la cuenta (no es un secreto) |
| Cuentas heredadas | Las cuentas creadas antes de 0038 arrancan con `signature_html = NULL` (sin firma); ningún correo se ve afectado hasta que el usuario configure una |

---

## 4. Endpoint y alcance

La firma **no estrena endpoint propio**: se guarda a través del endpoint de **actualización de cuenta** ya existente (`PATCH` sobre la cuenta), al que se le añadió el campo `signature_html`. Reglas de alcance heredadas de ese endpoint:

| Aspecto | Comportamiento |
|---|---|
| Saneamiento al persistir | El servidor sanea `signature_html` con el saneador de salida (`sanitize_outbound_html`) **antes** de guardarlo (defensa en profundidad: la firma se compone en el mismo editor restringido y se vuelve a sanear al enviar dentro del cuerpo). |
| Propiedad | La cuenta debe pertenecer al usuario de la sesión / al buzón; el alcance es el del endpoint de cuenta existente. |
| Proveedor | **Ninguna llamada al proveedor.** Es escritura local pura (a diferencia de las operaciones de correo Provider-First). |
| Nuevo permiso OAuth | **Ninguno.** La firma no toca Gmail/Outlook, así que no añade scopes. |

---

## 5. Formato admitido y saneamiento (igual que el cuerpo)

La firma usa **exactamente** el vocabulario del editor de salida del cuerpo, porque se escribe en el mismo editor y se inserta en el cuerpo. La allowlist exacta (etiquetas, atributos, CSS de `<blockquote>`, protocolos de enlace, endurecimiento de enlaces, comportamiento ante error) está catalogada una sola vez en **[composicion-y-envio.md](./composicion-y-envio.md)** § 5 — no se duplica aquí. Resumen de lo relevante para la firma:

- **Sí admite**: negrita / cursiva / subrayado, listas con viñetas y numeradas, y enlaces con esquema `http` / `https` / `mailto`.
- **No admite** (se limpia en silencio al guardar y de nuevo al enviar): imágenes/logos (`<img>`, protocolos `cid` / `data`), colores y tamaños de letra, tipografías personalizadas y tablas (`<table>`). Es la misma allowlist estricta del cuerpo saliente.

---

## 6. Reglas exactas de inserción al redactar

La firma se inyecta como texto del cuerpo según el modo. La regla de composición es determinista:

| Modo | Qué se inserta | Posición |
|---|---|---|
| Correo nuevo / borrador nuevo | Línea vacía + firma | `<p></p>` (línea editable arriba) **seguido de** la firma. Cuerpo de partida vacío. |
| Responder / Responder a todos / Reenviar | Línea vacía + firma + cita | La firma queda **entre** la línea editable de arriba y el bloque de cita del original. |
| Editar borrador existente | **Nada** | No se reinserta; el borrador se muestra con el cuerpo que tuviera. |

Condiciones exactas:

- **Inserción solo si la firma es no vacía.** Una firma `null`, `""` o **visualmente vacía** (solo un `<p></p>` o un `<br>` residual, detectado igual que el "cuerpo vacío" del composer) **no inserta nada**: el cuerpo de partida se deja tal cual.
- **No hay interruptor global** "usar / no usar firma": el único criterio es "firma no vacía ⇒ se inserta".
- **Cambio de cuenta**: la firma se **reemplaza** por la de la nueva cuenta **solo si** el modo es correo nuevo / borrador nuevo, **no** hay todavía borrador en el proveedor, **y** el cuerpo está "sin tocar" respecto a lo que se sembró al abrir. En cualquier otro caso (cuerpo ya editado, borrador existente, selector bloqueado) el cambio de cuenta **no toca el cuerpo**.
- **Editable y por mensaje**: la firma insertada es una copia editable; modificarla o borrarla en un mensaje concreto **no** altera la plantilla guardada en Ajustes.
- **No dispara el diálogo de cierre**: una firma auto-insertada **sin tocar** se considera "no sucia", así que cerrar un correo nuevo sin escribir nada cierra directamente, sin el diálogo Guardar/Descartar (ver [composicion-y-envio.md](./composicion-y-envio.md) § 5).

---

## 7. Lo que NO soporta (limitaciones aceptadas del MVP)

| No soportado | Porqué breve |
|---|---|
| **Imágenes / logos en la firma** | El saneador de salida no admite `<img>` ni los protocolos `cid` / `data`; una firma con logo se limpia. Es la misma limitación del cuerpo saliente (ver [composicion-y-envio.md](./composicion-y-envio.md) § 6). Fuera del MVP. |
| **Colores, tamaños y tipografías personalizadas** | El editor no los expone y el saneador descarta `style` salvo en `<blockquote>`. Igual que el cuerpo. |
| **Tablas en la firma** | Ni el editor las crea ni el saneador admite `<table>`. |
| **Varias firmas por cuenta** | Solo **una** firma por cuenta; no hay firmas seleccionables ni por contexto (responder vs. nuevo). Fuera del MVP. |
| **Sincronizar con la firma del proveedor** | La firma se define y guarda solo en MailManager; no se importa la firma existente de Gmail/Outlook ni se escribe en ellos. No se pide ningún scope OAuth nuevo. |
| **Interruptor global "usar / no usar firma"** | El criterio es simple: firma no vacía ⇒ se inserta; vacía ⇒ no. Para "no firmar este mensaje" el usuario borra la firma sembrada en ese correo. |
| **Reinsertar la firma al editar un borrador existente** | Deliberado: reinsertarla cada vez que se reabre un borrador la duplicaría. Lo que se guardó es lo que se muestra. |
| **Posición configurable de la firma** | La firma va siempre **debajo** de la línea editable (y, en respuesta/reenvío, **encima** de la cita). No hay opción de ponerla en otro sitio. |

---

## 8. Resumen

> La firma se guarda en la columna `accounts.signature_html` (`TEXT` *nullable*, migración 0038 puramente aditiva, sin invalidar caché), con tope **10 000 caracteres** de HTML (autoritativo en el servidor `AccountUpdate.signature_html`, replicado en el editor del cliente), sin mínimo —`""` borra la firma, ausencia/`null` la deja intacta, y la respuesta siempre serializa la clave (`null` si no hay)—. No estrena endpoint (reutiliza el `PATCH` de cuenta), no llama al proveedor ni pide scopes nuevos, y se sanea con el saneador de salida estricto del cuerpo (misma allowlist que [composicion-y-envio.md](./composicion-y-envio.md) § 5: sin imágenes, colores, tipografías ni tablas). Se inserta como texto del cuerpo (`<p></p>` + firma en un correo nuevo; entre el texto y la cita al responder/reenviar; nada al editar un borrador existente), solo si es no vacía, reemplazándose al cambiar de cuenta únicamente si el cuerpo está sin tocar y no hay borrador aún. Una firma por cuenta, sin sincronización con el proveedor y sin interruptor global. El comportamiento completo está en [../features/firma.md](../features/firma.md).
