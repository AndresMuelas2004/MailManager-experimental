# Vista de conversaciones — límites y alcance

Catálogo de **hasta dónde llega** la vista de conversación: cómo se agregan los campos de la fila-conversación, qué trae cada llamada al proveedor al reconstruir el hilo, las asimetrías Gmail/Outlook, los códigos de error y la lista de "qué NO soporta" con su porqué breve. El comportamiento narrado (la fila agrupada y su selección, la cadena del visor, la carga perezosa, el marcado al abrir) vive en **[../features/conversaciones.md](../features/conversaciones.md)**.

Las cifras de **paginación y tamaño de página** (50 conversaciones por página, el total exacto, la ventana de números) son las del listado y no se repiten aquí: están en **[listado-de-correos.md](listado-de-correos.md)**. La paginación es la misma; lo único que cambia es que el total cuenta **hilos**.

---

## 1. La clave de agrupación

| Concepto | Valor exacto | Notas |
|---|---|---|
| Clave de hilo (thread key) | `COALESCE(NULLIF(thread_id, ''), provider_message_id)` | Un mensaje sin `thread_id` (vacío o nulo) se convierte en un hilo de **un solo elemento** identificado por su propio `provider_message_id`; **nunca** se fusiona con otros mensajes sin hilo. |
| Partición — bandeja real / unificada | Por `(account_id, thread_key)` | Incluir `account_id` mantiene **separadas** dos cuentas distintas aunque (caso patológico) compartieran la misma cadena `thread_id`. Las cuentas **no se fusionan**. |
| Partición — bandeja ficticia | Por `thread_key` (tras deduplicar por `provider_message_id`) | Colapsa la **misma** cuenta de proveedor conectada bajo dos buzones (mismo `provider_message_id`, mismo `thread_id`); las cuentas genuinamente distintas siguen separadas por su `thread_id` de cada espacio de nombres. |
| Fila representante del hilo | El **mensaje más reciente** del hilo en esa bandeja | `received_at` descendente, con desempate por `provider_message_id` (el mismo desempate estable del listado). |

---

## 2. Cómo se agregan los campos de la fila

En modo conversación, los campos de estado de la fila **no** son los de un mensaje: son el agregado de todos los mensajes del hilo **presentes en esa bandeja** (entre los sincronizados localmente).

| Campo de la fila | Regla de agregación | Significado |
|---|---|---|
| No leído (negrita) | `is_read = bool_and(is_read)` | La fila está "leída" **solo si TODOS** los mensajes del hilo en esa bandeja están leídos; si **alguno** está sin leer, la fila va en negrita. |
| Clip de adjunto | `has_attachments = bool_or(has_attachments)` | El clip aparece si **algún** mensaje del hilo tiene adjunto descargable. |
| Estrella de favorito | `is_favorite = bool_or(is_favorite)` | La estrella (de solo lectura) aparece rellena si **algún** mensaje del hilo es favorito. |
| Contador | `thread_message_count = count(*)` sobre el hilo en esa bandeja | Número de mensajes del hilo **en esa bandeja** entre los sincronizados. Se dibuja **solo si > 1**. |

- El **total de la página** (`Z` en "X–Y de Z") cuenta **hilos distintos**, alineado con la partición: `COUNT(DISTINCT (account_id, thread_key))` en bandeja real/unificada y `COUNT(DISTINCT thread_key)` en ficticia. El conteo usa los mismos predicados que el listado, así que el total cuadra exactamente con lo que la página muestra.
- `thread_message_count` vale **1** en los listados que **no** agrupan (Favoritos) y para cada mensaje dentro de la respuesta del visor (allí no se usa).

---

## 3. La reconstrucción del hilo (llamada al proveedor)

El visor pide al proveedor la cadena completa del hilo. Es un flujo de **lectura + completado perezoso de la copia local**, **no** Provider-First (solo lee del proveedor y rellena lo que falta).

| Aspecto | Valor / regla |
|---|---|
| Identificación del hilo en la API | Por `provider_message_id` del mensaje abierto, **no** por `thread_id` | El `conversationId` de Outlook es base64 (lleva `/`, `+`, `=`) y rompería un segmento de URL; el backend deriva el hilo leyendo la fila del mensaje. El `provider_message_id` va **percent-encoded** en la ruta (la ImmutableId de Outlook puede contener `/`). |
| Hilo vacío (`thread_id` ausente) | **0 llamadas** al proveedor | Conversación de un solo mensaje: se construye desde la fila ya leída en la base de datos. |
| Cuerpo de los mensajes | **No** se trae aquí | La respuesta del hilo es solo metadata + estado, sin cuerpos. Cada cuerpo se baja **al expandir** su mensaje, por el camino de caché de [visualizacion-de-correos.md](visualizacion-de-correos.md). |
| Estado de cada mensaje en el visor | El **fresco del proveedor** en esa apertura | Bandeja / leído / favorito se toman de lo que el proveedor devuelve **ahora**, no de una re-lectura de la base de datos: un mensaje movido o leído fuera de la app se refleja al instante. |
| Persistencia del hilo (lazy sync) | **Best-effort** | Se guardan los mensajes del hilo (insertando los nunca sincronizados, refrescando `is_read` / `box` / destinatario de los existentes) y se re-aplica el favorito por mensaje. Si falla, se registra y el visor **se abre igual**. La persistencia **no** altera la respuesta de esta apertura (solo acelera la siguiente y actualiza la fila del listado). |
| Frescura (caché del frontend) | `staleTime: 0` + refetch al montar | Reabrir el visor **siempre** vuelve a pedir el hilo (misma política agresiva que las bandejas ficticias); nunca se sirve una foto de hace 30 s. |
| Efecto secundario en los listados | Invalida `['emails']` y `['virtual-mailbox-emails']` | Cada reconstrucción puede cambiar contadores / orden de las filas, así que tras una apertura con éxito los listados se refrescan. |

### 3.1 Asimetría Gmail vs Outlook al traer el hilo

| Aspecto | Gmail | Outlook |
|---|---|---|
| Llamada | **Una** `users.threads.get(format=metadata)`: el hilo trae todos sus mensajes embebidos (Enviados / Spam / Papelera incluidos — son cambios de etiqueta, no hilos aparte). | `$filter=conversationId eq '<id>'` sobre `/me/messages` (abarca todas las carpetas). El `conversationId` se percent-encodea una vez antes de envolverlo en comillas. |
| Paginación de la API | No aplica (un solo objeto hilo). | Páginas de **`$top=50`** mensajes, siguiendo `@odata.nextLink` hasta agotar el hilo. |
| Ordenación | Cliente, por `internalDate` (el proveedor no garantiza orden). | `$orderby` **omitido a propósito** (combinarlo con `$filter=conversationId` devuelve `400 InefficientFilter`); se ordena en cliente por `receivedDateTime`, con respaldo en `sentDateTime` para los enviados que no traen el primero. |
| Favorito por mensaje | Etiqueta `STARRED`. | `flag.flagStatus == "flagged"`. |
| Orden final de la cadena | Ascendente por `(received_at, provider_message_id)` — más antiguo arriba (igual en ambos proveedores). | Igual. |

---

## 4. Dónde se agrupa y dónde no

| Superficie | ¿Agrupa por conversación? | Cómo |
|---|---|---|
| Bandeja de una cuenta (entrada/enviados/spam/papelera) | **Sí** | El frontend envía `group_by_thread=true`. |
| Bandeja unificada del buzón | **Sí** | `group_by_thread=true`. |
| Bandeja ficticia (virtual) | **Sí, siempre** | El backend fija `group_by_thread=True` + dedup por `provider_message_id`; el frontend no expone forma de apagarlo. |
| Pestaña de Favoritos | **No** | Se envía `group_by_thread=false`: mantiene un mensaje por fila, selección, barra de acciones masivas y estrella clicable. |

El parámetro de API es `group_by_thread` (`GET /mailboxes/{id}/emails`), por defecto `false`; solo se envía cuando es verdadero (su ausencia equivale a "no agrupar").

---

## 5. Interacción de la fila

En las bandejas que agrupan, la fila-conversación informa y **abre** el visor, pero **conserva la selección** en las bandejas estándar (cuenta y unificada). Resumen de controles:

| Control | Bandeja de cuenta / unificada | Bandeja ficticia (virtual) | Dónde actúa |
|---|---|---|---|
| Casilla de selección por fila | **Sí** | **No** | Selecciona el hilo por su mensaje más reciente (representante) |
| Casilla "seleccionar página" en la cabecera | **Sí** (hasta 50, la página) | **No** | — |
| Barra de acciones masivas (papelera / spam / leído en bloque) | **Sí** | **No** | Opera sobre el mensaje representante de cada fila seleccionada ([acciones-sobre-correos.md](acciones-sobre-correos.md)) |
| Estrella clicable | **No** (indicador agregado de solo lectura) | **No** | Botón "Favorito" por mensaje en el visor ([favoritos.md](favoritos.md)) |
| Abrir la fila | **Sí** | **Sí** | Abre el visor de la conversación |

> La bandeja ficticia es **totalmente de solo lectura** (reúne cuentas de varios buzones sin una caja única a la que dirigir el lote — ver [bandejas-ficticias.md](bandejas-ficticias.md)). La pestaña de **Favoritos** no agrupa y conserva además la **estrella clicable**.

Acciones disponibles **dentro** del visor de la conversación: en la cabecera, **Responder / Responder a todos / Reenviar** (sobre el mensaje más reciente); por cada mensaje expandido, **Favorito**, **No leído**, **Spam**, **Papelera**, **Archivar** (solo si el mensaje está en la bandeja de entrada) o **Desarchivar** (solo si está archivado), y la **descarga de sus adjuntos**. No hay botón "marcar leído" (abrir ya marca todo el hilo como leído).

---

## 6. Códigos de error HTTP

Endpoint del visor: `GET /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/conversation`.

| Situación | Status | `code` |
|---|---|---|
| Sesión ausente / inválida | 401 | `unauthorized` |
| Mailbox de la URL inexistente | 404 | `mailbox_not_found` |
| Mailbox existe pero no es del usuario | 403 | `forbidden` |
| La cuenta no vive en ese mailbox | 404 | `account_not_found` |
| El mensaje base no está en la copia local (se borró entre listar y abrir) | 404 | `email_not_found` |
| Fallo del proveedor (genuino) al traer el hilo | 502 | `external_api_error` |
| Fallo inesperado del backend durante la reconstrucción | 502 | `conversation_fetch_error` |

> El listado agrupado (`group_by_thread=true`) **no** añade códigos nuevos: comparte los del listado normal (la consulta lee solo de la copia local, sin llamada al proveedor). Sus errores son los de [listado-de-correos.md](listado-de-correos.md).

---

## 7. Qué NO soporta (limitaciones aceptadas)

| No soporta | Por qué |
|---|---|
| **Acciones sobre el hilo entero de un clic** (marcar toda la conversación, borrarla completa) | Fuera del alcance inicial; las acciones son por mensaje dentro del visor. La única que abarca el hilo es el marcado de "leído" al abrir. |
| **Apagar la vista de conversación** | No hay interruptor de usuario: está siempre activa en las bandejas que agrupan. Quitarla del MVP simplifica la superficie. |
| **Fusionar cuentas distintas en un mismo hilo** | Cada proveedor identifica el hilo por cuenta (`threadId` / `conversationId` son por espacio de nombres de cuenta); fusionar arriesgaría mezclar hilos que el proveedor considera separados. Dos cuentas que participan en el mismo intercambio se ven como dos conversaciones. |
| **Que el contador de la fila coincida con lo que muestra el visor** | El contador cuenta el hilo **en esa bandeja** y **sincronizado**; el visor trae el hilo **completo** del proveedor (otras bandejas, mensajes no sincronizados). La diferencia es esperada (ver [../features/conversaciones.md](../features/conversaciones.md) § 5). |
| **Acciones masivas sobre el hilo entero** | La selección de una fila agrupada toca su **mensaje más reciente** (representante), no todos los mensajes del hilo; el resto se gestiona por mensaje en el visor. La bandeja de cuenta y la unificada sí tienen selección; la **bandeja ficticia no** (no hay una caja única a la que dirigir el lote). |
| **Re-sincronización masiva del histórico para completar hilos** | El hilo completo se trae **bajo demanda** al abrir cada conversación, no de golpe para todo el buzón: descargar todos los hilos completos de antemano gastaría cuota del proveedor sin que el usuario lo pida. |
| **Indicador "este correo fue reenviado" en la fila** | Gmail no expone el estado "Forwarded" por API y Outlook no ofrece un equivalente fiable; queda fuera (ver también [responder-y-reenviar.md](responder-y-reenviar.md)). |
| **Clip por mensaje dentro del visor (cabecera colapsada)** | Dentro de la cadena, el adjunto de cada mensaje se descubre al expandir su cuerpo (estrategia "lazy" de [adjuntos.md](adjuntos.md)); mostrar el clip antes sería un indicador muerto. El clip **agregado** sí aparece en la fila del listado. |

---

## 8. Enlaces

- Comportamiento de la vista de conversación: [../features/conversaciones.md](../features/conversaciones.md)
- Listado en el que se montan las filas-conversación (paginación, orden, columnas, acción al buzón real): [../features/listado-de-correos.md](../features/listado-de-correos.md) · [./listado-de-correos.md](./listado-de-correos.md)
- Render del cuerpo de cada mensaje: [../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md) · [./visualizacion-de-correos.md](./visualizacion-de-correos.md)
- Acciones por mensaje (favorito, papelera, spam, leído): [../features/acciones-sobre-correos.md](../features/acciones-sobre-correos.md) · [./acciones-sobre-correos.md](./acciones-sobre-correos.md) · [../features/favoritos.md](../features/favoritos.md)
- Bandejas ficticias (agrupan siempre, dedup): [../features/bandejas-ficticias.md](../features/bandejas-ficticias.md) · [./bandejas-ficticias.md](./bandejas-ficticias.md)
- Responder / Reenviar sobre el hilo: [../features/responder-y-reenviar.md](../features/responder-y-reenviar.md)
