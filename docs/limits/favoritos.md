# Límites de favoritos

Catálogo de topes, cuotas y comportamientos cuantitativos de la feature de favoritos, más la lista de "qué NO soporta". El comportamiento, los flujos y el porqué de las decisiones de diseño están en [`../features/favoritos.md`](../features/favoritos.md).

---

## 1. Topes y parámetros cuantitativos

| Parámetro | Valor exacto | Notas |
|---|---|---|
| Marca por correo (granularidad de la API) | **1 correo por llamada** | El endpoint de toggle actúa sobre un único correo. No existe endpoint de marca en bloque. |
| Marca multi-selección | **No existe** | No hay acción de favorito en la barra de selección múltiple ni endpoint por lotes; la marca se alterna correo a correo (estrella clicable de la fila en Favoritos, o botón por mensaje en el visor de la conversación en el resto de bandejas). Ver § 4. |
| Reintentos de la marca (lado Gmail) | **5 intentos** (1 inicial + 4 reintentos) | La marca Gmail pasa por la maquinaria de modificación de etiquetas por lotes, con 4 reintentos sobre errores transitorios. |
| Espera entre reintentos (Gmail) | **1 s** fija entre intentos | No es backoff exponencial en esta ruta. |
| Códigos transitorios que reintenta (Gmail) | `429`, `500`, `502`, `503`, `504` | Errores permanentes (`400`/`401`/`403`/`404`) no se reintentan. Mismo conjunto en ambos proveedores. |
| Reintentos de la marca (lado Outlook) | **3 intentos** (1 inicial + 2 reintentos) | La marca Outlook (`PATCH` de la bandera) reintenta los transitorios `429`/`500`/`502`/`503`/`504` y los fallos de red, **honrando `Retry-After`** del proveedor cuando lo envía; en su ausencia usa esperas `1/2/4 s`. Tras agotarlos propaga el fallo. |
| Espera entre reintentos (Outlook) | **`Retry-After` si está presente; si no, `1/2/4 s`** | A diferencia de Gmail, Outlook respeta la cabecera `Retry-After` (en segundos). `509` y `409` de concurrencia quedan fuera del conjunto transitorio en MVP. |
| Reintentos del listado de favoritos (Gmail) | **5 intentos** (1 inicial + 4 reintentos), **por página** | Cada página del listado `STARRED` se reintenta de forma independiente con la misma política que la marca Gmail (1 s fija, transitorios `429`/`500`/`502`/`503`/`504`). Un hipo transitorio en una página ya no aborta la reconciliación. |
| Reintentos del listado de favoritos (Outlook) | **3 intentos** (1 inicial + 2 reintentos), **por página** | Cada página (la inicial y cada `@odata.nextLink`) se reintenta con la misma política que la marca Outlook (`Retry-After`, fallback `1/2/4 s`). |
| Paginación al listar favoritos del proveedor (Gmail) | **500 ids por página** | Pagina hasta agotar; incluye spam y papelera en el conteo del proveedor. |
| Paginación al listar favoritos del proveedor (Outlook) | **100 ids por página** | Pagina por `nextLink` hasta agotar. |
| Filas reconciliadas por la sincronización | **Toda la cuenta** (1 sentencia SQL) | Marca verdadero/falso cada fila de la cuenta en una sola transacción. |
| Cabecera de estabilidad de IDs (Outlook) | `Prefer: IdType="ImmutableId"` en **todas** las llamadas | Sin ella, los ids dejan de casar con los de la base de datos local tras mover el mensaje. |
| Correos de la pestaña de Favoritos por **página** | **50** (máximo técnico 500) | Hereda la paginación numerada del listado general (páginas con "X–Y de Z"); sin scroll infinito. Ver [`listado-de-correos.md`](listado-de-correos.md). |
| Tokens de búsqueda dentro de Favoritos | **10 máximo** | La lupa silenciosamente recorta a 10 tokens (ver [`lupa.md`](../features/lupa.md)). |
| Mínimo de caracteres de búsqueda | **2** | Por debajo de 2, no filtra. |

---

## 2. Exclusiones por defecto

| Regla | Comportamiento |
|---|---|
| Spam | Excluido de la vista de Favoritos salvo que se seleccione la caja `SPAM` explícitamente. |
| Papelera | Excluida de la vista de Favoritos salvo que se seleccione la caja `TRASH` explícitamente. |
| Archivados | **NO se excluyen**: un favorito archivado (`box = ARCHIVE`) sigue apareciendo en Favoritos. El ancla por defecto solo descarta `TRASH`/`SPAM`, no `ARCHIVE` (asimetría deliberada con las bandejas ficticias). |
| Ancla por defecto | "Todo menos spam y papelera" (`box_not_in = ["TRASH","SPAM"]`, sin `ARCHIVE`). Cualquier otra caja explícita (`SENT`) se respeta tal cual, sin colar favoritos de otras cajas. |
| Bandejas ficticias | Exclusión por defecto **más amplia**: spam, papelera **y archivados** cuando su filtro no especifica caja (los archivados rescatables con `in:archive`). Es un superconjunto de la exclusión de Favoritos. Ver [bandejas-ficticias.md](bandejas-ficticias.md). |

---

## 3. Códigos de error y HTTP

| Operación | Situación | HTTP | `code` |
|---|---|---|---|
| Toggle (`PATCH .../favorite`) | El correo no existe en local (pre-check antes de llamar al proveedor) | **404** | `email_not_found` |
| Toggle | La fila desaparece entre el pre-check y el `UPDATE` (carrera, 0 filas afectadas) | **404** | `email_not_found` |
| Toggle | La cuenta no existe / no pertenece al mailbox | **404** | `account_not_found` |
| Toggle | La cuenta no está conectada / auth silenciosa falla (fallo de refresh de token antes de la llamada) | **409** | `account_not_connected` |
| Toggle | El proveedor rechaza o falla la marca tras agotar reintentos (Provider-First; no se persiste en local) | **502** | `external_api_error` |
| Toggle | Fallo interno/DB **tras** un éxito del proveedor, o error Python inesperado | **502** | `favorite_update_error` |
| Sync (`POST /favorites/sync`) | La cuenta indicada no existe / no pertenece al mailbox | **404** | `account_not_found` |
| Sync | Una cuenta falla auth silenciosa (refresh de token) | **409** | `account_not_connected` |
| Sync | Una cuenta falla el listado en el proveedor tras agotar reintentos → se aborta toda la sincronización | **502** | `external_api_error` |
| Sync | Fallo interno/DB inesperado durante la reconciliación | **502** | `favorite_sync_error` |

Nota: un fallo del proveedor en el toggle o la sync se reporta como **`external_api_error` (502)**, igual que en el resto de la app — `translate_core_error` mapea el `EmailExternalAPIError` del proveedor antes que el fallback. Los códigos `favorite_update_error` / `favorite_sync_error` son el **fallback interno/DB** (un fallo de base de datos posterior a un éxito del proveedor, o un error Python inesperado), **no** el código de un fallo del proveedor. El toggle es Provider-First, así que un fallo del proveedor nunca llega a tocar la base de datos local; el frontend revierte la estrella optimista al recibirlo. (Esto es una excepción consciente a la jerarquía doc↔código de la raíz §9: se alinea el doc al código por coherencia global de la app, decisión Q1.)

---

## 4. Lo que NO soporta (limitaciones aceptadas para el MVP)

- **No hay marca de favoritos en bloque ni acción multi-selección.** La marca se alterna correo a correo (un único punto de entrada: `setFavorite` / el endpoint `PATCH .../favorite`), ya sea desde la estrella clicable de la fila en Favoritos o desde el botón por mensaje del visor de la conversación. La barra de acciones en bloque (`useEmailBulkActions`, presente solo en Favoritos) cubre papelera, leído/no leído y spam, **pero no incluye favoritos**, y no existe ningún endpoint de marca por lotes. Las APIs de ambos proveedores ofrecen modificación por lotes, pero añadirla obligaría a diseñar un contrato de "éxito parcial" (qué correos se marcaron y cuáles fallaron) que no aporta valor al volumen del MVP. Mantiene la superficie de la API y el modelo de errores simples.

- **La sincronización no importa correos nuevos (Opción A).** Un correo marcado como favorito en el proveedor que MailManager todavía no tiene en su base de datos local se ignora en silencio durante la sincronización; no se crea fila nueva. La razón: la llamada de listado solo devuelve identificadores, e importar forzaría una segunda ronda de llamadas por id (una sincronización de metadata encubierta) que ya es responsabilidad de la sincronización general de la bandeja. El favorito aparece tras la siguiente sincronización de metadata.

- **Ya no hay un botón "Sincronizar favoritos" en la UI.** La sincronización general captura el favorito en **todos** los casos —correo nuevo y des/marcado fuera de banda sobre correo ya sincronizado, en ambos proveedores— así que el botón se retiró de la página de Favoritos (mailbox y cuenta). En Gmail el hueco que quedaba (un cambio de estrella sobre correo existente llega por la ruta de etiqueta-solo `_batch_fetch_label_updates`) se **cerró**: esa ruta ahora arrastra `STARRED` y `UPDATE_LABELS_BATCH` lo aplica vía `COALESCE`. El endpoint `POST /favorites/sync` **sigue existiendo en el backend** pero ninguna pantalla lo invoca (reconciliación vestigial — ver [`../features/favoritos.md`](../features/favoritos.md) § 5); sus códigos de error (§ 3) siguen siendo válidos si se llama directamente.

- **El botón de favorito está en el visor de la conversación (por mensaje), no en el visor de un solo correo.** En las bandejas que agrupan por conversación, cada mensaje expandido del visor trae su botón "Favorito"; en Favoritos (que no agrupa) la marca sigue siendo la estrella clicable de la fila, y su visor de un solo correo no expone botón de favorito. No hay, por tanto, un toggle de favorito en la cabecera del visor mono-mensaje. (Ver [`../features/conversaciones.md`](../features/conversaciones.md).)

- **No hay reconciliación global multi-mailbox de un tirón.** La reconciliación (hoy vestigial, sin botón en la UI) opera sobre el mailbox actual, o sobre una sola cuenta de él, según el alcance con que se invoque el endpoint — ver [`../features/favoritos.md`](../features/favoritos.md) § 5.1. Reconciliar favoritos de cuentas repartidas entre varios mailboxes reales (caso de una bandeja ficticia que abarca varios) exigiría disparar una reconciliación por cada mailbox implicado.

- **No hay carpeta/colección de favoritos en el proveedor.** El favorito es solo la etiqueta `STARRED` (Gmail) o la bandera de seguimiento (Outlook); no se crea ninguna carpeta dedicada ni se ordena por prioridad de bandera.

- **No hay ordenación por relevancia ni por prioridad de bandera.** La pestaña de Favoritos ordena estrictamente por fecha de recepción descendente, igual que el resto de listados.

- **Un `401`/`403` del proveedor *durante* la llamada de favorito se reporta como `external_api_error` (502), no como `account_not_connected` (409).** La auth silenciosa previa solo cubre el fallo de **refresh de token** *antes* de la llamada; si el token se revoca o el scope resulta insuficiente justo en el momento del `PATCH`/`GET`, el proveedor devuelve `401`/`403` y se mapea a `external_api_error` (502) en vez del más preciso "reconecta tu cuenta" (409). Es una limitación **transversal a casi todas las operaciones con el proveedor**, no exclusiva de favoritos; arreglarla bien exigiría un cambio en la clasificación de errores de toda la app (el `_graph_request` global de Outlook), fuera del alcance de esta revisión. Estos `401`/`403` son **permanentes**: no se reintentan.

- **La sincronización reescribe TODAS las filas de la cuenta en cada ejecución (no solo las que cambian).** La reconciliación marca verdadero/falso cada fila de la cuenta en una única sentencia (ver § 1, "Filas reconciliadas"). Es deuda de eficiencia conocida —solo perceptible internamente en cuentas muy grandes, nunca por el usuario— y se decidió **dejarla como está** en esta revisión para no alterar un contrato observable: el número de "filas reconciliadas" que la sincronización informa (§ 5.3 del gemelo) es precisamente el recuento de filas tocadas, y optimizar a "tocar solo las que cambian" cambiaría ese número por una mejora que el usuario no nota.

---

## 5. Notas de asimetría Gmail vs Outlook

| Aspecto | Gmail | Outlook |
|---|---|---|
| Representación del favorito | Etiqueta `STARRED` | Bandera de seguimiento (`flag.flagStatus = "flagged"`) |
| Captura del favorito en el sync general | Se lee de `labelIds` en **ambas** rutas del incremental: alta completa (`_parse_metadata_response`) y cambio de etiqueta (`_batch_fetch_label_updates`, que ahora arrastra `STARRED`) → un des/marcado sobre correo existente se refleja | Se lee `flag.flagStatus` del mensaje re-parseado completo; una **delta parcial sin `flag`** deja el favorito previo intacto (`COALESCE`), pero un des/marcado normalmente llega como upsert completo |
| Marca (set) | Añade/quita la etiqueta vía modificación por lotes (1 elemento) | `PATCH` del estado de la bandera |
| Reintentos de la marca | **5 intentos** (1 + 4), 1 s fija entre intentos, ignora `Retry-After` | **3 intentos** (1 + 2), honra `Retry-After` y si no `1/2/4 s` |
| Reintentos del listado | **5 intentos** (1 + 4) por página, 1 s fija | **3 intentos** (1 + 2) por página, honra `Retry-After` y si no `1/2/4 s` |
| Listado de favoritos | `messages.list` filtrando por `STARRED`, incluye spam/papelera | `$filter=flag/flagStatus eq 'flagged'` |
| Página de listado | 500 ids | 100 ids |
| Estabilidad de IDs | Estable de por sí | Requiere `Prefer: IdType="ImmutableId"` en cada llamada |
| Idempotencia | Sí (re-aplicar/re-quitar etiqueta es no-op) | Sí (re-poner el mismo estado es no-op) |
