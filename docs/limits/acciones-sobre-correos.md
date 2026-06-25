# Acciones sobre correos — límites y alcance

Catálogo de los topes, asimetrías y limitaciones deliberadas de las acciones sobre correos (leído·no leído, papelera, spam, **archivar/desarchivar**, borrado y acciones masivas). El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones) vive en **[../features/acciones-sobre-correos.md](../features/acciones-sobre-correos.md)**; aquí solo están las cifras y los "hasta dónde llega".

El toggle de favorito tiene su propio catálogo: ver [favoritos.md](favoritos.md).

> **Nota — modo conversación.** Las cifras de **selección** y la **barra de acciones masivas** de este documento aplican hoy **solo a la pestaña de Favoritos** (la única que no agrupa por conversación). En las bandejas reales, la unificada y las ficticias, las acciones se disparan **por mensaje** desde el visor de la conversación, sin selección múltiple ni barra masiva (ver [conversaciones.md](conversaciones.md)). El resto de cifras (troceo, reintentos, asimetrías de proveedor, alcance del borrado, códigos de error) **no cambian**: la acción subyacente es la misma venga de la barra masiva o del botón por mensaje.

---

## 1. Topes cuantitativos

| Concepto | Valor exacto | Dónde aplica |
|---|---|---|
| Selección "todos" desde la casilla de cabecera | **50** correos (los de la **página actual**) | Tabla de correos; la casilla de cabecera marca como mucho la página visible (50 = tamaño de página) |
| Selección acumulada entre páginas | **Sin tope fijo** (suma de lo marcado en cada página) | La selección **persiste** al cambiar de página; el usuario puede acumular más de 50 marcando en varias páginas |
| Mínimo de correos por petición | **1** (lista no vacía) | Toda petición de papelera / spam / leído rechaza una lista vacía con 422 (la lista `items` exige `min_length=1`) |
| Tope de items por petición HTTP | **Sin límite explícito** | El backend no impone un máximo de correos por llamada; el único límite práctico es la selección de 50 de la UI |
| Troceo interno (Gmail) | Lotes de **100** mensajes por operación batch | Gmail agrupa internamente las modificaciones de etiqueta / papelera en chunks de 100 |
| Reintentos por lote (Gmail, solo etiquetas) | Hasta **5 intentos** (1 inicial + **4** reintentos), con **1 s** de espera entre ellos | Solo las operaciones de etiqueta (leído, spam, restaurar de spam); ver § 2 |
| Llamadas por acción masiva | **Una por mailbox real** presente en la selección | El frontend agrupa por `mailbox_id` y abre las llamadas en paralelo |

> Nota: las acciones operan sobre la **selección**, no sobre "lo que está listado". El listado se navega por **páginas numeradas** (ver [listado-de-correos.md](listado-de-correos.md), y [lupa.md](lupa.md) para la búsqueda), y la selección **sobrevive al cambio de página**: una acción masiva puede afectar a correos marcados en páginas distintas. Lo que cada pulsación de "seleccionar todo" abarca es la página actual (50), no todas las páginas.

---

## 2. Reintentos y tolerancia a fallos

Asimetría clave dentro de Gmail: las operaciones que solo cambian etiquetas (leído, spam, restaurar de spam) reintentan los lotes; las que usan las llamadas de papelera por mensaje (mover a papelera, restaurar de papelera) **no** tienen reintento.

| Operación | Política de reintento |
|---|---|
| Marcar leído / no leído (Gmail) | **Reintenta** hasta 5 intentos por lote (modificación de etiqueta idempotente: reaplicar una etiqueta ya puesta es un no-op) |
| Marcar spam / restaurar de spam (Gmail) | **Reintenta** hasta 5 intentos por lote (mismo motivo: cambio de etiqueta idempotente) |
| Archivar / desarchivar (Gmail) | **Reintenta** hasta 5 intentos por lote (es un cambio de etiqueta idempotente —quitar/poner la etiqueta de bandeja de entrada—, mismo helper de etiquetas que spam) |
| Mover a papelera (Gmail) | **NO reintenta** (helper sin bucle de reintento; un fallo por mensaje se registra y se salta) |
| Restaurar de papelera (Gmail) | **NO reintenta** (mismo helper que "mover a papelera"; un fallo por mensaje se registra y se salta) |
| Operaciones por mensaje (Outlook) | Best-effort por correo: un identificador inválido (borrado en el servidor, etc.) se registra y se salta, sin abortar el resto del lote |
| Archivar / desarchivar (Outlook) | Best-effort por correo (mismo helper de movimiento de carpeta que spam/papelera): un fallo de movimiento se registra y ese correo se omite |
| Restaurar de papelera (Outlook) | Best-effort por correo: un fallo de movimiento se registra y ese correo se omite |

El conteo exacto de reintentos de los lotes de etiqueta (Gmail) está en § 1. En todos los casos, un fallo en un correo concreto **no aborta** los demás de la misma operación: el resultado puede ser **parcial** y la respuesta indica cuántos correos se vieron afectados de verdad.

---

## 3. Asimetrías Gmail vs Outlook

| Aspecto | Gmail | Outlook |
|---|---|---|
| Mover a papelera / spam | Cambio de etiqueta; el **ID del correo no cambia** | Movimiento de carpeta; **Graph reescribe el ID** y la app debe capturar y propagar el nuevo |
| Archivar / desarchivar | Cambio de etiqueta (quitar/poner la etiqueta de bandeja de entrada); el **ID no cambia** | Movimiento a/desde la carpeta "Archivo"; **Graph reescribe el ID** (misma regla que papelera/spam) |
| Idempotencia de "papelera" | Idempotente: re-papelerizar un correo ya en papelera es un no-op (la UPDATE local solo toca filas con `box NOT IN ('TRASH','DELETED')`) | Idempotente vía movimiento de carpeta (best-effort por correo) |
| Scope OAuth en uso | `gmail.modify` (no incluye borrado permanente) | `Mail.ReadWrite` (mover entre carpetas) |
| Borrado permanente real | Imposible con el permiso actual (`gmail.modify`); requeriría el scope restringido `mail.google.com` | Técnicamente posible, pero **no se usa** por uniformidad |
| Granularidad de fallo | Por mensaje vía callback de lote; con reintento solo en operaciones de etiqueta (§ 2) | Por mensaje (un correo malo no tumba el lote) |

---

## 4. El borrado definitivo: alcance exacto

| Propiedad | Valor |
|---|---|
| ¿Llama al proveedor? | **No** — no-op uniforme en Gmail y Outlook |
| ¿Borra del buzón real del proveedor? | **No** — el correo permanece en la papelera del proveedor |
| Efecto en MailManager | El correo se marca como eliminado y desaparece de **todas** las vistas (no hay ninguna vista que muestre correos eliminados) |
| Retención en el proveedor | Gmail purga la papelera a los **~30 días**; Outlook según la política del tenant |
| ¿Reaparece? | **Sí**, si el usuario lo saca de la papelera en el cliente original (Gmail/Outlook web) y MailManager re-sincroniza |
| Resistencia a la sincronización | Un correo eliminado queda "pegajoso": un sync que sigue viéndolo en la papelera del proveedor **no lo resucita**; solo lo trae de vuelta si el proveedor lo reporta **fuera** de la papelera |
| Confirmación | **Obligatoria** (diálogo del navegador). Es la única acción de la feature que confirma |
| ¿Recuperable desde MailManager? | **No** — desde la app es irreversible; la "recuperación" solo es posible vía el cliente original del proveedor |

---

## 5. Confirmaciones y reversibilidad

| Acción | ¿Pide confirmación? | ¿Recuperable desde la app? |
|---|---|---|
| Marcar leído / no leído | No | Sí (volver a marcar) |
| Mover a papelera | No | Sí (Restaurar) |
| Marcar spam | No | Sí (Restaurar de spam) |
| Restaurar (papelera / spam) | No | Sí (volver a mover) |
| **Eliminar definitivamente** | **Sí** | **No** (solo vía el proveedor) |

---

## 6. Códigos de error HTTP

Endpoints implicados: `POST .../emails/move-to-trash`, `POST .../emails/trash` (delete / restore), `PATCH .../emails/read-status`, `POST .../emails/spam`, `POST .../emails/restore-from-spam`, `POST .../emails/archive`, `POST .../emails/restore-from-archive`.

| Situación | Status | `code` |
|---|---|---|
| Sesión ausente / inválida | 401 | `unauthorized` |
| Mailbox de la URL inexistente | 404 | `mailbox_not_found` |
| Mailbox existe pero no es del usuario | 403 | `forbidden` |
| Una cuenta de la selección no vive en el mailbox de la URL (incluye el caso de la selección cruzada de § 8.2 del comportamiento si no se agrupa por mailbox real) | 404 | `account_not_found` |
| `Eliminar` / `Restaurar` un correo que ya no está en papelera | 409 | `email_not_in_trash` |
| Lista `items` vacía u otro fallo de validación del cuerpo | 422 | (validación Pydantic, sin `code` propio) |
| Fallo del proveedor al mover a papelera | 502 | `move_to_trash_error` |
| Fallo del proveedor al marcar leído/no leído | 502 | `read_status_update_error` |
| Fallo del proveedor al marcar spam | 502 | `spam_move_error` |
| Fallo del proveedor al restaurar de spam | 502 | `spam_restore_error` |
| Fallo del proveedor al archivar | 502 | `archive_move_error` |
| Fallo del proveedor al desarchivar | 502 | `archive_restore_error` |
| Fallo del proveedor durante restaurar de papelera | 500 | `trash_operation_error` |

> **Archivar / desarchivar comparten el mismo motor que spam** (agrupar por cuenta → validar `cuenta ∈ mailbox` → Provider-First → persistir el movimiento de buzón). Cada `items[]` exige `min_length=1` (lista vacía → 422). **Desarchivar siempre devuelve el correo a la bandeja de entrada (`ALL_MAIL`)**, nunca a la carpeta original concreta — igual que "restaurar de spam".

> El borrado definitivo (`action=delete`) **no** llama al proveedor (§ 4), así que su único fallo posible de proveedor sería en la fase de autenticación previa; la operación de marcado local no produce un 502 propio.

---

## 7. Lo que NO soporta (y por qué)

- **No hay borrado real en el proveedor.** Por diseño: el scope de Gmail no lo permite y se opta por un no-op uniforme para no crear asimetría entre proveedores. El correo "borrado" sobrevive en la papelera del proveedor.
- **No hay "deshacer" para el borrado definitivo dentro de la app.** La recuperación solo es posible restaurando el correo en el cliente original y resincronizando.
- **Restaurar de spam no devuelve a la carpeta original concreta**, siempre a la bandeja principal. Solo la papelera recuerda el origen exacto.
- **No hay papelera "real" propia de MailManager ni purga manual de correos.** La papelera de la app es un reflejo de la del proveedor; la limpieza definitiva la hace el proveedor por retención. (La purga manual existe solo para los **binarios de adjuntos** cacheados — ver [../limits/adjuntos.md](../limits/adjuntos.md) — no para correos.)
- **No hay un "seleccionar toda la bandeja" de un clic.** La casilla de cabecera abarca como mucho la **página actual** (50 correos); no existe un "seleccionar los 5.000 correos del buzón" en una sola acción. Sí se puede acumular una selección mayor marcando correos a mano en varias páginas (la selección persiste entre páginas), pero no con una única pulsación de "seleccionar todo".
- **No hay acción por fila independiente en el listado** para papelera / spam / archivar / borrado: todo pasa por la **barra de selección** (una selección de un solo correo usa la misma barra). Aplica a Favoritos y, desde la restauración de la selección, también a las bandejas de cuenta y a la unificada agrupadas (la acción toca el mensaje más reciente del hilo). Las **bandejas ficticias** no tienen selección: allí esas acciones se ofrecen **por mensaje dentro del visor de la conversación** (Spam / Papelera / No leído / Favorito / Archivar o Desarchivar — ver [conversaciones.md](conversaciones.md)). El **borrado definitivo** no se ofrece en el visor (sigue siendo exclusivo de la barra de la papelera). La única acción automática al abrir es marcar como leído (toda la conversación de golpe).
- **No hay etiquetas/categorías personalizadas ni mover a carpetas arbitrarias.** El conjunto de destinos se limita a las bandejas modeladas (principal, archivados, enviados, spam, papelera). Crear etiquetas propias o mover a una carpeta cualquiera del proveedor queda fuera del MVP.
- **Desarchivar no recuerda la carpeta de origen.** Siempre devuelve el correo a la bandeja de entrada (`ALL_MAIL`), no "a la carpeta exacta de la que vino" — *por qué:* archivar solo se hace desde la bandeja de entrada, así que el origen siempre es ese (misma simplificación que "restaurar de spam"; solo la papelera recuerda el origen exacto).
- **No hay reversión atómica de operaciones parciales.** Si una acción masiva falla a medias, los correos que sí se procesaron quedan procesados; no se revierten. La respuesta reporta el recuento real de afectados.

---

## Resumen de topes en una frase

> Cada "seleccionar todo" abarca **50** correos (la página actual), pero la selección **persiste entre páginas** y puede acumular más marcando a mano; Gmail trocea en lotes de **100** y reintenta lo idempotente (leído, spam, **archivar/desarchivar** — todos cambios de etiqueta) pero **no** la papelera; Outlook va best-effort por correo (archivar es un movimiento de carpeta que reescribe el ID, como spam); **archivar/desarchivar** comparten el motor de spam (Provider-First, parcial, `archive_move_error`/`archive_restore_error` 502) y **desarchivar siempre vuelve a la bandeja de entrada**; las acciones masivas hacen **una llamada por cada mailbox real** de la selección; y el "borrado definitivo" es un **no-op uniforme** que nunca toca el proveedor —el correo sobrevive en su papelera—, irreversible desde la app y recuperable solo desde el cliente original.

Volver al comportamiento: **[../features/acciones-sobre-correos.md](../features/acciones-sobre-correos.md)**.
