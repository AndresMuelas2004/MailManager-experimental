> **Regla permanente — léela antes de editar este fichero.**
>
> Este fichero se carga en el contexto en cada sesión de Claude. Una línea aquí solo justifica sus tokens si no puede reconstruirse leyendo el código.
>
> **Antes de escribir o conservar una línea, pregúntate: ¿podría reconstruir esto abriendo el/los fichero(s) relevante(s) durante ~30 segundos?**
> - **SÍ → bórrala.** El código es la fuente de verdad. Los catálogos de lo que hacen módulos / funciones / tests, las paráfrasis de nombres o cuerpos, las enumeraciones exhaustivas de kwargs / campos / config, las tablas de flujo que reflejan nombres de fichero o símbolo ya existentes, y las recetas paso a paso de código que es legible por sí mismo caen todas aquí. Bórralas en cuanto las veas.
> - **NO → consérvala.** Las trampas silenciosas al extender la capa, las asimetrías entre ficheros (hermanos que no se comportan igual), las reglas de orden / ciclo de vida cuya violación rompe todo, los invariantes cuya regresión silenciosa se colaría en la revisión, las decisiones históricas cuya razón no está en el código, y los identificadores fijos (UUIDs, datos sembrados, constantes mágicas) que no pueden recalcularse — esos sí ganan sus tokens.
>
> **Cuando actualices este fichero, relee cada sección y borra cualquier cosa que desde entonces haya migrado al código.** La obsolescencia es peor que el silencio.

# Guía de Tests de Integración

> **Reglas generales**: esta capa de tests DEBE respetar todas las reglas definidas en
> [`CLAUDE.md`](./CLAUDE.md).
> El presente documento contiene detalles específicos del proyecto que complementan esas reglas.

**Regla de autoridad**: el código de esta capa debe respetar lo documentado aquí. Si hay una discrepancia entre esta guía y el código existente, esta guía es la referencia — corrige el código, no la guía. Cuando se añada nueva funcionalidad, actualiza esta guía al final de la tarea para reflejar la nueva realidad.

## Notas Específicas del Proyecto

### Los tests de endpoints se dividen por recurso (`test_endpoints_<recurso>.py`)

El antiguo `test_endpoints.py` monolítico ya no existe: los tests de endpoints se reparten en ficheros `test_endpoints_<recurso>.py` (uno por recurso — el árbol del directorio es el índice). Un endpoint nuevo va al fichero de su recurso, o a uno nuevo de la familia si estrena recurso; no reintroduzcas un `test_endpoints.py` único.

### Trampa 1 — un nuevo módulo de repositorio debe parchearse en `isolated_db`

Al añadir un nuevo módulo de repositorio, su `get_connection` debe monkeypatchearse en la fixture `isolated_db` (`conftest.py`). Sin esto, el repositorio usa el pool de conexiones real en lugar de la transacción por test, rompiendo el aislamiento y causando tests inestables (flaky). Síntoma: fugas de datos entre tests y el rollback deja de funcionar. El conjunto actual de módulos parcheados es lo que lista `conftest.py::isolated_db` — mantén esa lista y esta sección sincronizadas al añadir un nuevo repositorio.

Asimetría que conviene conocer: los adjuntos requieren **dos** módulos de repositorio en la lista de parcheo — `email_attachment_repo_module` (lado de correo recibido) y `draft_attachment_repo_module` (lado del compositor). La mayoría de capas tienen un repositorio por dominio; los adjuntos son la excepción, y olvidar cualquiera de los dos causa una fuga silenciosa en su mitad de la superficie.

### Trampa 2 — un nuevo módulo de servicio debe parchearse en `_apply_test_monkeypatches`

`_apply_test_monkeypatches` parchea `build_manager_for_accounts`, `load_wrapped_app_credentials`, `load_wrapped_account_tokens` y `account_store.upsert_tokens` **de forma independiente en cada módulo de servicio que los importa** (actualmente `services_helpers`, `accounts_service`, `emails_service`, `drafts_service`, `attachments_service`). Cada módulo importa a su propio nivel de módulo, así que cada uno debe parchearse por separado. Cuando se añada un nuevo módulo de servicio, extiende esta lista — de lo contrario sus tests de integración golpearán las APIs reales del proveedor.

### Trampa — el worker de backfill se apaga en la suite (`BACKFILL_WORKER_ENABLED=false`)

El worker de backfill arranca un hilo daemon en el lifespan de la app (que el `TestClient` de scope de sesión entra). `conftest.py` fija `BACKFILL_WORKER_ENABLED=false` vía `os.environ.setdefault` **en el import del módulo** (antes de que el lifespan corra) para que ningún hilo de fondo sondee la BD real durante la suite. La consecuencia portante: la guarda del sync que excluye las cuentas con backfill activo es **independiente del flag**, así que sus tests (job `running` sembrado → la cuenta no se sincroniza; job `completed` sembrado → un `is_full_sync` NO dispara la reconciliación de fantasmas para ella) la ejercitan por completo SIN lanzar el hilo. Los tests del **encolado-en-connect** son la excepción: hacen `monkeypatch.setenv('BACKFILL_WORKER_ENABLED', 'true')` por caso para probar que un connect con `sync_cursor` NULL crea el job `pending` (y que con no-NULL, o con el flag off, no lo crea). Un test que asuma el flag ON por defecto, o que dependa de que el hilo procese jobs, es no determinista. (El nuevo `account_backfill_repo_module` ya está en la lista de monkeypatch de `isolated_db` — Trampa 1 arriba; sin él las lecturas/escrituras del job se escapan de la transacción por test.)

### Trampa — backfill: migración 0041 y "sin job" ≠ "completado" en el status (`test_backfill.py`)

Complementa la trampa del worker-apagado de arriba (la guarda del sync — cuenta activa excluida, cuenta completada sin reconciliación — ya está cubierta ahí):

- **`test_account_id_column_is_uuid` es una guarda de regresión de la migración 0041**: `account_backfill_jobs.account_id` DEBE seguir siendo `uuid` o la FK a `accounts(account_id)` no se crea (una columna `TEXT` la rompería en silencio). La FK es `ON DELETE CASCADE` — borrar la cuenta borra el job.
- **`enqueue` revive SOLO un job `failed`**: el `ON CONFLICT ... WHERE status='failed'` reactiva un `failed` a `pending` y deja `running`/`completed` intactos (reconexión idempotente); el test lo fija reencolando sobre un `failed` (→`pending`) y luego sobre un `running` (sigue `running`).
- **En `GET .../backfill-status`, "sin job" y "completado" son estados de respuesta distintos**: una cuenta sin job se OMITE de `accounts[]` (respuesta `{"accounts": [], "active": false}`); un job `completed` SÍ aparece con `done=true`/`active=false`. Ambos usan datos EFÍMEROS vía `_insert_backfill_job` (la tabla de jobs está vacía en el seed 0010 — la tercera excepción a "usa datos sembrados", ver Reglas de GET más abajo).

### Trampa — el `RuntimeError` de `sync_drafts` aflora como `draft_sync_error`, NO como `external_api_error`

Los tests que inyectan `fetch_drafts_exc=RuntimeError(...)` y esperan el envoltorio estándar `external_api_error` 502 fallarán. `raise_on_silent_auth_errors` recibe `DraftSyncError` como su clase de reserva, a diferencia de cualquier otra ruta de `RuntimeError` traducido que aflora como `external_api_error`. Al escribir un nuevo endpoint estilo sync, verifica qué clase de reserva recibe su llamada a `raise_on_silent_auth_errors` — la elección cambia silenciosamente el código de error.

### Favoritos — cinco trampas no cubiertas en otro lugar (`test_favorites.py`)

- **El test de carrera-a-404 parchea el método del store directamente, no el constructor del manager.** `test_set_favorite_race_zero_rows_returns_404` fuerza la carrera perdida (toggle en proveedor OK, fila local desaparecida → `update_favorite` devuelve 0 filas → 404) vía `monkeypatch.setattr(emails_service.email_metadata_store, "update_favorite", lambda *_: False)` — un eje de inyección distinto al de cualquier otro test de favoritos (que parchea `build_manager_for_accounts`). Parchea el alias equivocado y se ejecuta el update real: el test pasa por la razón equivocada (misma clase de no-op silencioso que la trampa de clave mal escrita de `configurable_test_client`).
- **`favorite=true` convierte `box=ALL_MAIL` en el ancla "excluir TRASH/SPAM"; cualquier otro valor de box es literal.** `test_listing_with_favorite_and_explicit_sent_box_returns_only_sent_favorites` es la regresión nombrada que protege el bug histórico donde cualquier box distinto de {TRASH,SPAM} colapsaba al ancla y filtraba favoritos de ALL_MAIL hacia la vista SENT. Sus hermanos SENT/TRASH/SPAM (y la variante de operador `q=in:sent`, que alcanza el mismo estado a través de `q`) deben mantenerse emparejados — una aserción de un solo box no detecta la fuga.
- **El fallo de la lista del proveedor en `sync_favorites` aflora como `external_api_error` (502), NO como `favorite_sync_error`** — el inverso de la trampa `sync_drafts`/`draft_sync_error` de arriba. Un fallo por cuenta de `list_favorite_ids` se captura en `manager._last_errors` y se relanza a través de `translate_core_error` (`EmailExternalAPIError → external_api_error`); la reserva `FavoriteSyncError` solo se dispara ante un fallo de BD/interno tras un éxito del proveedor. No esperes `favorite_sync_error` en la ruta de fallo del proveedor.
- **`total_synced` (filas tocadas) es deliberadamente ≠ `accounts[i].favorites_synced` (tamaño de la lista del proveedor).** `total_synced` es el rowcount del único UPDATE de reemplazo completo sobre toda la cuenta; `favorites_synced` es `len(favorite_ids)` del proveedor. `test_sync_favorites_full_replace_for_account` fija `total_synced=4` frente a `favorites_synced=2` — esperar igualdad es incorrecto. Un favorito del proveedor ausente del `email_metadata` local se cuenta en `favorites_synced` pero no crea ninguna fila (Opción A — `test_sync_favorites_ignores_unknown_provider_id_no_new_row`).
- **La pre-comprobación de existencia se dispara antes de construir el manager.** `set_favorite` llama a `email_metadata_store.exists()` y hace 404 con `email_not_found` ANTES de `build_manager_for_accounts`, así que una fila ausente no gasta ningún ida y vuelta al proveedor. `test_set_favorite_missing_email_does_not_call_provider` lo demuestra haciendo que el constructor (stub) lance y comprobando que su contador de llamadas se queda en 0 — una aserción de 404 solo por status no distinguiría un 404 de pre-comprobación de uno posterior a la construcción.

### Trampa — `test_auth_endpoints.py`: los tests de Microsoft necesitan AMBAS variables de entorno de client-id; el seed se indexa por `(auth_provider, provider_sub)`

- **`_set_ms_env` fija AMBAS `GOOGLE_CLIENT_ID` y `MICROSOFT_CLIENT_ID`.** `_load_auth_settings` exige `GOOGLE_CLIENT_ID` con independencia de qué proveedor esté bajo prueba, así que un test de Microsoft que fija solo `MICROSOFT_CLIENT_ID` hace 500 con `env_var_error` apuntando a la variable ausente *equivocada*. `test_microsoft_login_not_configured` verifica lo inverso (GOOGLE fijado, MICROSOFT ausente → 500) Y que `verify_microsoft_token` nunca se alcanza (el guard en el punto de uso se dispara primero).
- **`_seed_test_user` hace upsert sobre `(auth_provider, provider_sub)` (migración 0037); `TEST_USER_GOOGLE_SUB` es el `provider_sub` portante.** Un insert directo de un segundo usuario con el MISMO email pero distinto provider/sub tiene éxito y crea una fila DISTINTA — el modelo "sin vinculación de cuentas" previsto, pero una sorpresa para cualquier test de propiedad que asuma un usuario por email. El contrato de identidad de 0037 queda fijado de extremo a extremo por `test_login_idempotent_same_provider_sub_returns_same_user` (mismo provider+sub → mismo `user_id`, una fila) y `test_same_email_two_providers_creates_distinct_users` (Google + Microsoft, mismo email → dos `user_id`s).

### Trampa — `test_contacts_suggestions.py`: no `seeded_test_client`, y la inyección de error es en dos etapas

- **El seed de la migración 0010 no puede ejercitar este endpoint** — es anterior a la migración 0031, así que cada fila sembrada tiene `to_email`/`to_name` vacíos y un `from_email` igual a la propia dirección de la cuenta (descartado por la exclusión de la dirección propia). Los tests preparan sus propias filas y usan `test_client_base`: el servicio NO hace ninguna llamada al proveedor, así que no se necesita ningún manager fake.
- **Las dos llamadas al store fallan ante precondiciones distintas.** El fallo de `account_store.list_account_ids_by_user` se dispara incluso con cero cuentas; el fallo de `list_recipient_suggestions` requiere al menos una cuenta propia para superar la etapa de búsqueda de cuentas. Un test de error del store de suggestions que omita el setup de cuentas hace cortocircuito en la etapa de búsqueda y verifica la ruta de error equivocada. Un `q` solo con espacios en blanco (pasa `min_length=2` pero tokeniza a nada) devuelve 200 `[]`, no 422.

### `configurable_test_client` — dict `config` mutable

Devuelve `(client, config)`. `config` es un dict mutable que `FakeEmailClient` lee por referencia, así que los tests pueden cambiar el comportamiento del proveedor entre llamadas a la API dentro de un mismo test. Las claves soportadas incluyen `metadata`, `deletes`, `label_updates`, `is_full_sync`, `existing_message_ids`, `sync_cursor_return`, y los overrides explícitos `*_return`: `delete_return`, `restore_return`, `move_to_trash_return`, `fetch_messages_metadata_return`. Escribir mal cualquiera de estas produce silenciosamente un fake que no hace nada (no un error), así que el test pasa por la razón equivocada. Hermanos: `test_client` da un fake estático; `failing_test_client` inyecta un fallo vía parametrize.

### `seeded_test_client` — para tests GET contra los datos de la migración 0010

Fixture por test que sobreescribe la dependencia de auth para devolver el ID de usuario **sembrado** (no el `TEST_USER_ID` por defecto) y restaura el override previo tras el yield. Como `app.dependency_overrides[require_session]` es global, un test que use esta fixture no debe intercalarse con otras fixtures de override de auth en el mismo fichero.

### DDL dentro de una transacción de test — solo sentencias transaccionales en PostgreSQL

Los tests que prueban "el API prohíbe `owner_user_id` NULL" (`test_null_owner_mailbox_*`) emiten un `ALTER TABLE ... DROP NOT NULL` dentro de la transacción por test para poder preparar el estado no permitido. El DDL de PostgreSQL es transaccional y hace rollback limpiamente con el test, así que esto es seguro. **No** copies este patrón con DDL no transaccional como `CREATE INDEX CONCURRENTLY`, `VACUUM` o `REINDEX CONCURRENTLY` — esos no pueden ejecutarse dentro de una transacción y filtrarían cambios de esquema entre tests.

### `_insert_draft` y la trampa de invariancia de `now()`

`test_drafts.py::_insert_draft` acepta un string ISO opcional `created_at`. Este parámetro es esencial para cualquier test que verifique un resultado concreto de `ORDER BY created_at DESC`: el `now()` de PostgreSQL devuelve el **mismo valor para cada sentencia dentro de una sola transacción**, y la fixture isolated-db envuelve cada test en una transacción. Sin timestamps explícitos, las filas insertadas una tras otra comparten idéntico `created_at`, y el orden se vuelve no determinista.

### Trampa — los tests de bandejas ficticias deben reparentar los mailboxes sembrados a `TEST_USER_ID`

La migración `0010` siembra los mailboxes de Gmail y Outlook bajo `SEEDED_USER_ID`, no bajo el `TEST_USER_ID` por test. Cualquier test que cree una bandeja ficticia referenciando `SEEDED_GMAIL_ACCOUNT_ID` o `SEEDED_OUTLOOK_ACCOUNT_ID` debe primero llamar a `_reparent_seeded_user(isolated_db, TEST_USER_ID)` (definido al principio de `test_virtual_mailboxes.py`). Sin ello `POST /virtual-mailboxes` devuelve 404 `account_not_found` y el fallo no da ninguna pista de que falta un paso de reparentado de propiedad. Único de los tests de vmb — los tests de drafts / emails / attachments o poseen sus seeds a través de `TEST_USER_ID` o crean datos al vuelo.

### Trampa — los inserts SQL directos en `virtual_mailboxes` deben poblar `scope_payload`

La migración `0032` eliminó `scope_kind` del contrato del API pero **mantuvo** `scope_payload` como columna `NOT NULL` en la tabla (renombrarla habría roto demasiadas migraciones en curso). Los tests que puentean el router para preparar una fila de `virtual_mailboxes` — tests de propiedad contra un registro ajeno, montajes de condición de carrera, etc. — deben incluir `scope_payload` con al menos `'{"account_ids":[]}'::jsonb`. Omitirlo falla con una violación de constraint cuyo mensaje no insinúa la divergencia contrato / esquema.

### Trampa — ruta poblada de `GET /virtual-mailboxes`: verifica el scoping por propietario, no solo la lista vacía

`test_list_empty_returns_empty_array` solo ejercita alguna vez la ruta `[]`. Una fuga entre usuarios en el `WHERE owner_user_id = %s` de `LIST_VIRTUAL_MAILBOXES_BY_OWNER` la pasaría. `test_list_returns_only_owned_vmboxes` crea dos vmboxes propias más una insertada bajo un propietario ajeno (insert directo, `scope_payload='{"account_ids":[]}'::jsonb` según la trampa de arriba) y verifica que la respuesta contiene EXACTAMENTE los dos ids propios — el único test que detecta una regresión de scoping por propietario.

### Trampa — los valores de filtro cadena vacía deben ser 422, no "sin filtro"

`subject_contains=""` / `from_email=""` llegarían al repositorio como `ILIKE '%%'` y coincidirían con cada fila — un volcado silencioso de toda la bandeja indistinguible de "sin filtro". `VirtualMailboxFilterPayload` lleva `min_length=1` para rechazarlos en la frontera del esquema; el test fija el 422. Una regresión que elimine `min_length` convierte "filtrar por nada" en "coincidir con todo" sin error. Limpia un filtro OMITIENDO la clave, nunca enviando `""`.

### Trampa — `box` + `box_not_in` juntos devuelve silenciosamente cero filas

`_validate_box_exclusivity` rechaza con 422 un payload que lleve ambos. Sin guard, el repositorio emitiría `box = X AND NOT (box = ANY(Y))` a la vez → una bandeja perpetuamente vacía sin señal de error. El test verifica el 422; una regresión aflora como un listado siempre vacío, no como una excepción.

### Trampa — `box_not_in=[]` significa "incluir TRASH/SPAM", no "usar el defecto"

Una lista vacía explícita es un opt-in válido para ver TRASH/SPAM (el guard del repositorio es `is not None`, no truthiness — ver `database_guide.md`). El test siembra filas TRASH/SPAM y verifica que aparecen. Una regresión de comprobación por truthiness colapsa `[]` a la exclusión por defecto y las filas desaparecen — este test es lo único que lo detecta. El opt-in NO es "mostrar todo", eso sí: el mismo test también siembra una fila `box='DELETED'` y verifica que sigue oculta, porque `_build_filter_args` inyecta `DELETED` en cada rama `box_not_in` (no es un valor de `FilterBox`, así que nunca puede solicitarse — ver `repository_guide.md`). Así que bajo `box_not_in: []` el servicio entrega al repositorio `["DELETED"]`, no `[]`; un autor de test que "extienda" esto para esperar que la fila DELETED aflore está equivocado. Un test compañero (`test_default_listing_excludes_deleted_rows`) fija la misma exclusión en la rama por defecto (sin `box`), aislando su aserción con un `subject_contains` único para que `total == 0` sea exacto.

### Trampa — `ARCHIVE` en una vmbox: excluido por defecto, rescatado por `in:archive`, nunca un filtro guardado (tres tests al unísono)

`ARCHIVE` se une a TRASH/SPAM en la exclusión POR DEFECTO de una bandeja ficticia, pero es el ÚNICO box excluido por defecto que un `in:archive` explícito puede RESCATAR (TRASH/SPAM/DELETED colapsan a vacío en su lugar). Y deliberadamente NO es un miembro de `FilterBox`, así que `box:"ARCHIVE"` / `box_not_in:["ARCHIVE"]` guardados en un filtro de vmbox son 422 en la frontera del esquema — el correo archivado es alcanzable dentro de una vmbox SOLO vía `in:archive`. Tres tests protegen la terna y deben moverse juntos: `test_emails_for_vmbox_default_excludes_archive` (una fila ARCHIVE sembrada sigue oculta por defecto, `total == 0` vía un `subject_contains` único), `test_emails_for_vmbox_in_archive_rescues_archived_rows` (`in:archive` la aflora), y `test_create_rejects_box_archive_filter` + `test_create_rejects_box_not_in_archive_filter` (ambos campos → 422). El rescate está acotado únicamente a la rama de exclusión por defecto: `test_emails_for_vmbox_in_archive_on_pinned_box_returns_empty` fija que una vmbox anclada a un `box` distinto (p. ej. SENT) toma la rama `box is not None` donde `in:archive` es incompatible y colapsa a una página vacía — un refactor que sacara el rescate `ov == "ARCHIVE"` fuera de la rama `box_not_in` filtraría filas archivadas hacia una vmbox anclada a un box sin que ningún otro test lo detecte.

### Trampa — el ganador de la deduplicación entre cuentas se decide por la completitud de `to_email`

Cuando el mismo `provider_message_id` existe bajo dos `account_id`s (una cuenta OAuth conectada dos veces), `LIST_FILTERED_DISTINCT` las colapsa y gana la fila con `to_email` no vacío con independencia de `received_at`; `total` es `COUNT(DISTINCT provider_message_id)` (no debe ser 2). Fija AMBOS el `to_email` superviviente y `total == 1` — verificar solo el conteo se pierde una regresión de selección de ganador.

### Trampa — el determinismo de paginación necesita el orden secundario `(account_id, provider_message_id)`

`ORDER BY received_at DESC` por sí solo deja las filas del mismo segundo (newsletters masivas) en orden arbitrario, así que la paginación por OFFSET puede repetir o saltarse una fila. El test de determinismo siembra empates de timestamp y verifica un barrido completo estable a lo largo de las páginas. Una aserción de una sola página nunca detecta esto — la regresión solo se muestra al paginar.

### Trampa — PATCH/DELETE sobre una vmbox desaparecida debe ser 404, no 500 / 200 silencioso

Ventana de carrera entre la pre-comprobación de propiedad y el SQL mutante: en UPDATE el repositorio devuelve `None` → el servicio lanza `VirtualMailboxNotFound` (404); en DELETE comprueba el conteo de filas afectadas → 404. El bug histórico de UPDATE era que el repo lanzaba `QueryError("not found")` → `VirtualMailboxOperationError` (500). Los tests fijan PATCH-tras-delete → 404 y DELETE-tras-delete → 404; un refactor que relance ante no-match (UPDATE) o se salte la comprobación de rowcount (DELETE) restaura silenciosamente el 500 / un 200 engañoso.

### Trampa — el listado virtual SIEMPRE agrupa por hilo; `total` cuenta hilos

`list_emails_for_virtual_mailbox` hardcodea `group_by_thread=True`, y los filtros (`is_favorite`, etc.) aplican a nivel de mensaje ANTES de agrupar — dos mensajes favoritos que comparten un `thread_id` colapsan en una sola fila de hilo. Los tests que verifican el conteo de items / `total` bajo un filtro deben esperar conteos de hilos, no conteos de mensajes (p. ej. dos favoritos sembrados en un hilo → una fila, `total == 1`).

### Trampa — el listado agrupado regular indexa hilos por `(account_id, thread_key)`; el listado virtual indexa por `thread_key` a secas

`GET /emails?group_by_thread=true` (el listado regular) particiona hilos por `(account_id, thread_key)`, así que dos cuentas DISTINTAS que casualmente compartan un string `thread_id` permanecen como filas de hilo SEPARADAS — nunca fusionadas. El listado virtual, en cambio, particiona por `thread_key` a secas (tras la deduplicación de `provider_message_id` entre cuentas) y SÍ las colapsa. `test_grouped_listing_does_not_merge_distinct_accounts_sharing_thread_id` fija el lado regular; una regresión que eliminara `account_id` de la clave de agrupación regular fusionaría silenciosamente las conversaciones no relacionadas de dos cuentas que casualmente comparten un string de hilo del proveedor.

### Trampa — `test_dev_login.py`: el host del TestClient es `testclient`

El `TestClient` de Starlette presenta el client host `testclient`, no `127.0.0.1`/`localhost`. Para ejercitar el camino feliz del dev-login el test fija `DEV_LOGIN_TRUSTED_HOSTS=testclient`; de lo contrario cada llamada hace 403 `dev_login_not_localhost`. El camino feliz también **elimina** el override estándar de auth `require_session` (que inyecta un usuario fijo) y se apoya en la cookie que el propio endpoint fija, restaurando el override en un `finally` — el dev-login es uno de los pocos endpoints cuyo propósito entero es acuñar la sesión que el override falsea en otro caso.

### Trampa — `test_admin_purge.py`: el camino feliz debe sembrar un triplete envejecido

El purge solo borra blobs cuyo `email_attachments.last_accessed_at < now() - 30 days`. `_seed_expired_blob` prepara el triplete completo `email_metadata` + `email_attachments` + `email_attachment_blobs` con `last_accessed_at = now() - 45 days` dentro de la transacción `isolated_db`. Sembrar solo el blob sin una fila `email_attachments` envejecida no purgaría nada y el test verificaría `purged_count=0` por la razón equivocada. Los tres estados (`purge_disabled` 503 / `invalid_admin_token` 401 / ejecutado) dependen de la combinación de la variable de entorno `ATTACHMENTS_PURGE_TOKEN` + la cabecera `X-Admin-Token`.

### Trampa — `test_reply_context_endpoint.py`: 422 (no 502) ante acción inválida, y AMBOS constructores se parchean

`action` es un `Literal` a nivel de router, así que un valor inválido lo rechaza FastAPI con **422** antes de alcanzar la ruta 502 del servicio. El test llamado `test_invalid_action_returns_502` en realidad verifica 422 — el nombre es histórico; no lo "arregles" para esperar 502. El endpoint resuelve un manager a través de AMBOS `emails_service.build_manager_for_accounts` Y `drafts_service.build_manager_for_accounts`, así que el fake debe parchearse en ambos módulos. `_seed_email_metadata` es obligatorio (la pre-comprobación de `exists()`) y la deduplicación de CC de Reply-All solo se ejercita cuando se inyecta `email_address` en la fila de la cuenta.

La misma sutileza de envoltorio aplica al tope de tamaño del cuerpo compuesto (`max_length=1_000_000` en `DraftCreate` / `DraftUpdate` / `EmailSendRequest`): un cuerpo por encima del tope es un **422** emitido por el handler por defecto de `RequestValidationError` de FastAPI con la forma `{"detail": [...]}`, NO el envoltorio `{"error": {code, message, detail}}` del proyecto (no hay ningún handler de `RequestValidationError` registrado). Los tests que verifican "body too large" deben leer `detail[]`, nunca `error.code` — el patrón estándar del proyecto `assert error.code == …` no aplica a los 422 de validación.

Dos casos límite de resolución de reply que la fixture por defecto no ejercita: un email base en el box **SENT** cuyo `from_email` es la propia dirección del usuario resuelve el `To` de la respuesta desde los `to_recipients` originales (auto-respuesta), no desde `from_email`; y un `reply_to` no vacío gana sobre `from_email` para el `To` de la respuesta (R-10). Un test para cualquiera de los dos debe poblar el campo correspondiente de `ReplyContext` — ninguno es alcanzable desde el seed estándar.

### Trampa — `test_copy_attachments_from_email.py`: parchea solo `drafts_service.build_manager_for_accounts`

El endpoint de copia construye sus clientes de proveedor desde `drafts_service`, así que el `_patch_fake_manager` local parchea solo ese módulo. Parchear `emails_service` en su lugar deja el constructor real en su sitio y el test se cae hacia una llamada real al proveedor. Los caminos no-felices que cubre (no-op de Outlook `copied_count=0`, R-12 `already_copied`, cortocircuito por `unavailable_at` sin llamada al proveedor, filtrado inline) dependen todos del array estructurado `skipped[]` más que del status de la respuesta.

### `test_drafts_reply_metadata.py` NO es redundante con `test_drafts.py`

Cubre dos invariantes que los tests de drafts a secas no cubren: el guard `COALESCE(EXCLUDED.col, drafts.col)` en `UPSERT_DRAFTS_BATCH` (una sincronización de drafts no debe pisar la metadata de reply persistida localmente con los NULLs que carga la ruta de lectura del proveedor), y que `send_draft` lee el threading desde la **fila local**, no desde el cuerpo de la petición (un cliente manipulado no puede re-enhebrar en tiempo de envío). Verifica contra el tracker `send_draft_with_attachments_reply_kwargs` del fake.

### Trampa — badge `unread-count`: conteos de seed fijos, pero el multi-cuenta necesita filas efímeras

Las aserciones del camino feliz sembrado fijan conteos exactos de no leídos del seed de Gmail de la migración 0010 (una sola cuenta) — un cambio en el seed los desplaza y se rompen sin otra señal. `box` es un `Literal["ALL_MAIL","SPAM"]` de router, así que TRASH/SENT colapsan a 422 (FastAPI) sin alcanzar el servicio. El invariante portante — el GROUP BY de `COUNT_UNREAD_BY_ACCOUNT` no emite fila para una cuenta con cero no leídos, y el servicio la rellena como `unread:0` mientras suma `total` en Python — NO puede ejercitarse con el seed de una sola cuenta, así que los dos tests multi-cuenta son la excepción documentada a "usa datos sembrados, no efímeros" de abajo: crean una segunda cuenta vía el API e insertan `email_metadata` controlada a través de `isolated_db` (`_insert_unread_rows`). Una suite solo-una-cuenta se queda en verde aunque el relleno o la suma en Python regresionen.

### Trampa — los tests de operadores de la lupa siembran filas efímeras (la SEGUNDA excepción a "usa datos sembrados"), y `in:` sobrescribe el `box` de la ruta

La familia `test_list_emails_operator_*` + `_insert_operator_fixture_rows` son la segunda excepción documentada a "usa datos sembrados, no efímeros" (tras `unread-count` de arriba): el seed de la migración 0010 no puede ejercitar operadores — es anterior a la migración 0031 (`to_email` vacío, así que `to:` nunca coincide), nunca escribe `has_attachments`/`is_favorite` (la ruta de sync B.lazy los deja en false, así que `has:attachment` / `is:favorite` necesitan filas por SQL directo), y sus fechas están años en el pasado (así que `before:`/`after:` son insondeables). La fixture inserta tres filas controladas a través de `isolated_db` y fija esos flags vía SQL directo.

`in:` **sobrescribe** el `box` de la ruta: `test_list_emails_operator_in_overrides_box_to_sent` envía `box=ALL_MAIL` con `q="in:sent"` y verifica SOLO la fila SENT (`total == 1`). Un nuevo test de operador que pase `box=ALL_MAIL` esperando semántica ALL_MAIL bajo una cláusula `in:` verifica el conjunto equivocado — la asimetría es invisible desde el cuerpo del test.

### Trampa — PATCH de `signature_html`: `""` la limpia, el 422 del tope tiene forma de FastAPI, y un PATCH no relacionado no debe borrarla

- **`signature_html=""` es un PATCH válido que LIMPIA la firma (200), no un 422** — asimétrico con `display_label` en el MISMO endpoint, que lleva `min_length=1` y rechaza `""`. Limpia una firma con `""`; déjala intacta OMITIENDO la clave (el guard `is not None` del servicio).
- **El 422 por exceso de tope usa el envoltorio `{"detail": [...]}` de FastAPI, NO el `{"error": {...}}` del proyecto** — la misma sutileza de `RequestValidationError` ya señalada para el tope de tamaño del cuerpo compuesto arriba (`max_length=10_000` en `AccountUpdate.signature_html`). Verifica `detail`, nunca `error.code`.
- **`test_update_account_signature_html_survives_unrelated_update` es la regresión portante para el unísono de cuatro cláusulas de `UPSERT_ACCOUNT`** (`database_guide.md`): siembra una firma, haz PATCH solo de `display_label`, verifica que sobrevive. Elimínalo y una regresión que quite `signature_html` del INSERT/VALUES de la query borra cada firma en cualquier update de cuenta no relacionado mientras el resto de la suite de firma se queda en verde.

## Reglas de Testing de Endpoints GET (obligatorias)

Los endpoints GET que leen exclusivamente de la base de datos (sin llamadas al proveedor) se cubren con tests de integración con la misma fidelidad que E2E. Los GET con dependencias externas (p. ej. cache-aside con reserva al proveedor) necesitan su propia estrategia documentada por endpoint.

1. **Usa datos sembrados, no datos efímeros.** Los tests GET usan los datos fake sembrados de la migración `0010` vía `seeded_test_client`. No crees filas desechables vía POST solo para testear un GET — los datos sembrados son deterministas y proveen valores esperados conocidos. Tres excepciones documentadas usan datos efímeros porque el seed 0010 no puede ejercitar su superficie — cada una justificada en su propia trampa arriba: el badge `unread-count` multi-cuenta (el seed tiene una sola cuenta por bandeja), los operadores de la lupa (anterior a la migración 0031), y `backfill-status` (la tabla `account_backfill_jobs` no tiene filas en el seed).
2. **Verifica contenido exacto, no solo códigos de estado.** Verifica el cuerpo real de la respuesta contra valores sembrados conocidos (conteos exactos, valores concretos de campos), no `len >= 1` o comprobaciones de 200 a secas.
3. **Cubre todas las variantes de parámetro.** Usa `@pytest.mark.parametrize` para cada combinación válida de parámetros de filtro.
4. **Los nuevos endpoints GET solo-BD siguen estas reglas.** Si un nuevo GET lee datos no cubiertos por el seed actual, extiende el seed (nueva migración + actualiza la sección de datos de abajo) antes de escribir los tests.

### Trampa — `test_email_content.py`: el endpoint de contenido es cache-aside, así que el timing de prefetch/purge dirige sus tests

`GET .../emails/{id}/content` es la excepción "GET con dependencia externa" — cache-aside + un prefetch en tiempo de sync + un purge de TTL en tiempo de sync — así que tres invariantes de setup deciden silenciosamente si cada test ejercita lo que dice ejercitar:

- **`sample_metadata` fechado en 2024 es portante**: cae FUERA de la ventana de prefetch de 48h, así que `sync-metadata` NO precachea el cuerpo y el GET posterior es un MISS genuino. Refechar el seed a `now()` convierte cada test de "cache miss" en un falso verde. (El test de contenido de silent-auth esquiva esto sembrando `email_metadata` vía SQL directo y saltándose `sync-metadata` — el `auth_silent_exc` inyectado haría 409 al sync antes de que persista ninguna fila.)
- **Las `BackgroundTasks` corren sincrónicamente bajo el `TestClient` de Starlette**: el prefetch/purge programado a partir de la respuesta se ejecuta antes de que la llamada retorne, así que sus efectos secundarios en BD son observables en la misma petición. Un efecto secundario hecho solo-async dejaría de ser visible sin señal de fallo.
- **El test de purge debe mantener su fila sembrada `is_read=TRUE`**: el purge corre ANTES del prefetch en un mismo sync, y el objetivo del purge es una fila `ALL_MAIL` reciente que ES TAMBIÉN un objetivo de prefetch elegible — dejada como no leída, el prefetch recachea el cuerpo que el purge acaba de borrar y `assert synced_row is None` se pone en rojo.
- **Tanto los adjuntos inline como los descargables afloran en la respuesta** (`LIST_EMAIL_ATTACHMENTS_BY_MESSAGE` no filtra `is_inline` — ocultar las partes inline es asunto del frontend). Un test de adjuntos de cache-hit que verifique solo la fila descargable está equivocado.
- **El test de TTL de cache-HIT debe sembrar `fetched_at` en el pasado** (p. ej. `now() - 60 days`) para que la aserción de que el touch sube `last_accessed_at` pero NO `fetched_at` sea medible — con ambos en `now()` el test pasa aunque se subiera `fetched_at`.

## Adjuntos — invariantes que valen sus tokens

### `send_draft_with_attachments_exc` es el punto de inyección correcto para fallos de envío

La ruta de envío unificada pasa por `EmailManager.send_draft_with_attachments`, así que una inyección `FakeEmailClient(send_draft_exc=…)` es silenciosamente inerte frente a `drafts_service.send_draft`. Usa `send_draft_with_attachments_exc=EmailAttachmentSendFailed(detail={...})` para ejercitar la ruta de persistencia de éxito parcial (D-27) de extremo a extremo.

### `fetch_content_exc` (no `list_message_attachments_exc`) es el punto de inyección de fallo del visor de contenido

El visor de contenido en cache-miss hace UNA sola llamada al proveedor tras la unificación D4 — `fetch_content_with_attachments` (cuerpo + adjuntos fusionados) — así que `list_message_attachments_exc` es **silenciosamente inerte** aquí (verifica un falso-verde 200 en vez del 502); `list_message_attachments` sobrevive solo para la ruta de Forward de Outlook. Inyecta `fetch_content_exc` para cualquier test de fallo-del-proveedor del endpoint de contenido, aunque el mismo endpoint también descubra y persista adjuntos en ese miss.

### Los endpoints de adjuntos de draft son solo-local — sin llamada al proveedor

`POST /drafts/{id}/attachments` y `DELETE .../attachments/{aid}` escriben solo en `draft_attachments`. Los tests que monkeypatchean `EmailManager.send_draft_with_attachments` NO necesitan extender ese mock para los tests de attach/remove; el servicio nunca alcanza el proveedor en estos endpoints. El DELETE deliberadamente **no es idempotente a nivel de fila**: un attachment_id ausente aflora 404, nunca 204. (El flujo de UI optimista del compositor tolera el 404 ignorándolo — eso es una elección del frontend, no un contrato.)

`position` se auto-asigna en el servidor por draft como `MAX(position)+1` (o `0` para la primera fila) y la respuesta lleva el valor resuelto. Los tests que suban varios adjuntos uno tras otro deben verificar incremento monótono (`0, 1, 2, …`) — cualquier cambio en la lógica de posición del repositorio que rompa este contrato regresiona el orden de los chips del compositor.

### `test_has_attachments_invariant.py` — el contrato B.lazy

Dos clases de test cubren preocupaciones separadas: `TestHasAttachmentsInvariant` ejecuta directamente la query de invariante SQL (y verifica que los datos sembrados de la migración 0010 ya la satisfacen desde el arranque); `TestRecomputeHasAttachments` llama al helper `recompute_has_attachments` directamente contra la BD viva para verificar el flip del flag ante un insert no-inline, el no-op ante solo-inline, y la idempotencia. Ambas clases deben actualizarse cuando el helper o el invariante SQL cambien — no son redundantes.

Los cuatro estados de invariante que la query SQL impone: `(false, 0 filas no-inline) ✅`, `(false, ≥1 filas no-inline) ❌`, `(true, 0 filas no-inline) ❌`, y los adjuntos solo-inline **NO deben** poner el flag a true (la subconsulta COUNT filtra `is_inline=false`).

### `test_blocked_extensions_parity.py` — paridad entre fronteras, sin paso de build compartido

La constante backend `BLOCKED_EXTENSIONS` y el `blocked_extensions.json` del frontend deben listar las MISMAS extensiones. No hay ningún paso de build compartido que derive uno del otro — ambos se mantienen a mano. El test verifica: igualdad de conjuntos (misma pertenencia), igualdad de longitud (detecta duplicados), que la lista del frontend está ordenada alfabéticamente, y que las entradas del frontend están en minúsculas sin puntos iniciales. La igualdad de orden mutuo **no** se verifica en esta capa — el orden de la constante backend lo gobierna su propio test unitario.

### Tests de rate-limit (`test_rate_limit.py`) — colapso de IP del TestClient + buckets compartidos

Bajo `TestClient` cada petición reporta `request.client.host == "testclient"` sin `X-Forwarded-For`, así que `client_ip()` devuelve UNA clave para todas las peticiones de un test (misma causa raíz que la trampa del host de `test_dev_login.py` de arriba). Para el limitador esto significa que los POST de setup y las llamadas de create-draft también cargan el bucket `global` por-IP, así que un test que apunte a un bucket ESPECÍFICO mantiene `global` generoso (p. ej. `(50,60)`) para evitar un 429 espurio, mientras que el test del propio `global` usa un valor pequeño. Los contadores son estado a nivel de módulo limpiado por `_enable` y la fixture autouse `_isolate_counters`; el test default-OFF NO debe llamar a `_enable` — prueba el no-op por la AUSENCIA de la variable de entorno.

Las aserciones portantes son las de bucket compartido: `provider_sync` (sync-metadata + favorites/sync + drafts/sync) y `email_send` (send directo + send de draft) se verifican cada una a través de TODOS sus miembros agotando el bucket en un miembro y verificando que otro también hace 429 bajo el mismo `scope`. Un test por endpoint pasaría mientras un split ensanchara silenciosamente el techo real — solo la aserción entre endpoints lo detecta. Las exenciones del callback de OAuth y de `/health` se prueban cada una de forma conductual martilleando el endpoint más allá de un `global` pequeño y verificando que nunca hace 429.
