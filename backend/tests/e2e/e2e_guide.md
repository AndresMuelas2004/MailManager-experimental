> **Regla permanente — léela antes de editar este fichero.**
>
> Este fichero se carga en el contexto en cada sesión de Claude. Una línea aquí solo justifica sus tokens si no puede reconstruirse leyendo el código.
>
> **Antes de escribir o conservar una línea, pregúntate: ¿podría reconstruir esto abriendo el/los fichero(s) relevante(s) durante ~30 segundos?**
> - **SÍ → bórrala.** El código es la fuente de verdad. Catálogos de lo que hacen los módulos / funciones / tests, paráfrasis de nombres o cuerpos, enumeraciones exhaustivas de kwargs / campos / config, tablas de flujo que reflejan nombres de fichero o de símbolo ya existentes, y recetas paso a paso de código que es de por sí legible caen todas aquí. Bórralas en cuanto las veas.
> - **NO → consérvala.** Trampas silenciosas al extender la capa, asimetrías entre ficheros (hermanos que no se comportan igual), reglas de orden / ciclo de vida cuya violación lo rompe todo, invariantes cuya regresión silenciosa se colaría en la revisión, decisiones históricas cuya justificación no está en el código, e identificadores fijos (UUIDs, datos sembrados, constantes mágicas) que no pueden recomputarse — esos se ganan sus tokens.
>
> **Al actualizar este fichero, re-lee cada sección y borra cualquier cosa que desde entonces haya migrado al código.** La obsolescencia es peor que el silencio.

# Guía de Tests E2E

> **Reglas generales**: esta capa de tests DEBE respetar todas las reglas definidas en
> [`CLAUDE.md`](./CLAUDE.md).
> El documento actual contiene detalles específicos del proyecto que complementan esas reglas.

**Regla de autoridad**: el código de esta capa debe respetar lo documentado aquí. Si hay una discrepancia entre esta guía y el código existente, esta guía es la referencia — arregla el código, no la guía. Cuando se añada nueva funcionalidad, actualiza esta guía al final de la tarea para reflejar la nueva realidad.

## Decisiones de Diseño

### Totalmente automatizada — sin pasos interactivos

La suite se ejecuta sin ninguna interacción con el navegador. La autenticación se maneja insertando una sesión directamente en la tabla `sessions` mediante `psycopg2` (separado del pool de la app); `require_session` la valida entonces contra la base de datos real.

Tres endpoints quedan **excluidos** de la suite automatizada porque requieren OAuth interactivo o dependen de él, y se verifican manualmente con scripts:

| Endpoint | Motivo |
|---|---|
| `POST /auth/google` | Inicia el flujo interactivo de OAuth en el navegador |
| `POST /mailboxes/{mid}/accounts/{aid}/connect` | Inicia el flujo interactivo de OAuth por provider |
| `DELETE /auth/me` | No se puede crear un usuario de test sin `POST /auth/google` |

### Configuración de tests en `e2e_config.py`

Los identificadores de las cuentas de test preexistentes están centralizados en `e2e_config.py` con overrides por variable de entorno. Estas cuentas deben existir en la base de datos real con refresh tokens de OAuth válidos antes de ejecutar la suite — la suite nunca las crea ni las borra.

`SEND_RECIPIENT` es la dirección de destino usada por cada test de envío y de draft. Overríbela mediante `E2E_SEND_RECIPIENT` cuando ejecutes la suite donde la dirección por defecto no esté disponible.

### Cuentas de test preexistentes — una por provider

La suite requiere **una cuenta real y autenticada por cada implementación de cliente en `backend/core/email/`**. Cada cuenta está vinculada a un buzón real propiedad del desarrollador y debe tener refresh tokens de OAuth válidos en la tabla `accounts` antes de ejecutar la suite.

Los valores de `display_label` viven solo en la base de datos (no en ningún fichero de código), así que deben documentarse aquí:

| provider | account_id | mailbox_id | display_label |
|---|---|---|---|
| gmail | `9805b672-032b-4d74-9696-4db53a5eb512` | `28a83414-36f5-4115-ab61-977d5a06a8e1` | `pruebaGmail` |
| outlook | `3c55eb17-9d5e-4d31-a3b5-14c6c24279b9` | `b61e15d5-153e-42ee-a4c6-2c943bd13c07` | `pruebaOutlook` |

**Regla de extensión**: cuando se añada un nuevo provider a `backend/core/email/`, crea una cuenta real correspondiente en la base de datos, registra sus identificadores en `e2e_config.py`, añade la fila a esta tabla, y extiende la suite con tests específicos del provider (ver Checklist de Extensión más abajo).

## Trampas y Contratos de Comportamiento

### Los tests del ciclo de vida de auth deben ser los ÚLTIMOS en el flujo

`POST /auth/logout` invalida la cookie de sesión — cualquier test que se ejecute después obtiene un 401. El test de logout y el test de verificación del 401 viven en la sección final de `test_full_flow.py` por este motivo. Al añadir nuevos tests, insértalos siempre **antes** de la sección de logout.

### Los datos de test preexistentes son sagrados

El usuario, los buzones y las cuentas preexistentes definidos en `e2e_config.py` NUNCA deben ser borrados, editados ni mutados de ninguna otra forma por la suite. Los tests de operaciones de provider (sync, send, drafts, spam, trash) usan estas cuentas pero solo con operaciones aditivas/idempotentes, más la limpieza de lo que el propio test creó.

### Tests de drafts — patrón de limpieza

Cada test de draft crea un draft en el provider real, lo verifica, y limpia mediante el endpoint DELETE o SQL crudo `DELETE FROM drafts WHERE ...` en un bloque `finally` (red de seguridad para el caso en que el send/update/delete falló a mitad del test). El paso de verificación y la limpieza viven en la **misma** función de test (según `common_mistakes.md` § 1) — no los separes en tests distintos.

Cuando los drafts se **sincronizan** desde el provider (Sección 5c), el draft se deja intencionadamente en el provider tras el test; solo las filas locales se limpian mediante `_clear_local_drafts`. La limpieza del lado del provider para los drafts se cubre en las secciones explícitas de delete y send.

### Comportamiento específico del provider que conviene conocer

- **Outlook re-envuelve y normaliza los cuerpos HTML del lado del servidor.** Los cuerpos de draft ahora viajan como HTML (`contentType: HTML`; D-31 revertida, el campo de la API es `body`, no `body_html`). En la lectura, Graph devuelve el contenido envuelto en un documento `<html><head><meta …us-ascii></head><body>…</body></html>` completo — `_parse_outlook_draft` lo aplana de vuelta a un fragmento, pero el round-trip NO es byte a byte (Graph además reescribe los espacios en blanco). Los tests del cuerpo de draft de Outlook DEBEN afirmar por contención (`"E2E updated body" in data["body"]`), nunca por igualdad. El `raw` de Gmail hace round-trip más cercano al verbatim pero su `_parse_gmail_draft` elimina un salto de línea final de la parte HTML — afirma por contención ahí también. Los tests del flujo de send/forward deberían confirmar adicionalmente que el cuerpo enviado llega como HTML al provider (Gmail: una parte `text/html` dentro de un `multipart/alternative`; Outlook: `body.contentType == "html"`).
- **Tests e2e de adjuntos (`test_46e..m`).** Cubren el ciclo de vida local-only de los adjuntos de draft (POST → DELETE sin contacto con el provider, una variante Gmail + una Outlook), los caminos de send-con-adjunto contra ambos providers (`test_46h`/`test_46i`), los tres estados del endpoint de purge de admin (`test_46j..l`) — `503 purge_disabled` cuando `ATTACHMENTS_PURGE_TOKEN` no está definido, `401 invalid_admin_token` cuando está definido pero la cabecera es incorrecta, `200 {purged_count, freed_bytes}` en caso contrario — y el endpoint de **descarga** cache-aside (`test_46m`). El test de extensión bloqueada se ejecuta solo contra Gmail porque la blocklist es uniforme (D-04a). **Huecos aceptados**: `test_46l` NO pre-siembra un blob expirado — afirma `purged_count >= 0`/`freed_bytes >= 0`, así que un purge sin efecto contra una base de datos limpia pasa trivialmente. `test_46m` reutiliza `bootstrap_attachment_message` (camino Gmail), así que solo ejercita la descarga real cuando el bootstrap produce una fila descargable real — para Gmail el priming cache-aside de `/content` siempre persiste una (la fila sintética `'bootstrap'` es solo de Outlook, protegida tras `_force_has_attachments`). El cap de multipart de 30 MB sigue sin cobertura E2E (solo unit + integration).

### Los tests de forward sembran estado real del provider

`test_forward_flow_gmail.py` y `test_forward_flow_outlook.py` necesitan una fila de inbox con `has_attachments = TRUE`. Cuando la cuenta no tiene una (máquina limpia, primera ejecución), `bootstrap_attachment_message` en `_forward_helpers.py` envía un email real auto-dirigido con un adjunto PDF desde la cuenta de test a su propia `email_address`, hace polling de `sync-metadata` hasta que el `provider_message_id` de SENT aterriza localmente, prepara el endpoint de contenido cache-aside, y fuerza `has_attachments = TRUE` mediante SQL directo (el workaround `_force_has_attachments` aplica a cualquier provider que vuelva sin el flag tras el priming — típicamente Outlook, cuyo `GET /me/messages/{id}/attachments` devuelve una lista vacía para la copia de la carpeta SENT incluso cuando el draft había subido uno; el binario se preserva en el provider para que el flujo de forward posterior siga funcionando contra Graph en tiempo real). **`_force_has_attachments` TAMBIÉN inserta una fila no-inline de `email_attachments`** junto con el volteo del flag: sin ella el `has_attachments=TRUE` forzado tendría cero filas de adjunto que lo respalden y pondría en rojo el test de integración `test_has_attachments_invariant.py::test_seeded_state_satisfies_invariant` en la BD compartida (escanea cada fila, no solo las semillas de migration-0010 — ver `repository_guide.md`). El efecto secundario es que cada ejecución deja al menos un mensaje auto-dirigido en la cuenta de test real.

### Tests de flujo de Reply / Forward — aserción de threading y asimetrías de provider

`test_reply_flow_gmail.py` / `test_reply_flow_outlook.py` y los dos ficheros de forward-flow ejercitan el camino completo `reply-context → create draft → send` contra el provider real. Contratos que conviene conocer:

- **La fuente del reply debe tener un cuerpo recuperable por el provider, y solo el bootstrap de forward produce uno de forma fiable.** `bootstrap_reply_source` en `_reply_helpers.py` reutiliza `_forward_helpers.bootstrap_attachment_message` (el mensaje auto-dirigido que el flujo de forward ya demuestra que hace round-trip de un `<blockquote>` en ambos providers) y busca su `thread_id`; el flujo de reply ignora el adjunto heredado. Se probaron dos fuentes más baratas y fallan en las cuentas reales: (1) *"la fila ALL_MAIL más reciente"* — un mensaje enviado a un destinatario **externo** deja solo una **copia fantasma** sin cuerpo en el `ALL_MAIL` de la cuenta Outlook (el cuerpo vive en el item `SENT` bajo un id distinto); responder a ella produce solo la línea de atribución (`build_quoted_body_html` omite correctamente la cita sobre un cuerpo fuente vacío). (2) *"auto-envío y responder al id enviado"* — la dirección de la cuenta de test de Outlook es una dirección **Gmail**, así que el envío nunca vuelve al buzón de Outlook; solo quedan las copias `SENT` y Graph devuelve un **cuerpo vacío** para esas mediante la lectura `$select=body` del reply-context. Gmail mantiene un id para la copia enviada+recibida así que ahí funciona, pero la fuente tiene que ser fiable para ambos. El paso de content-fetch al final de cada test de reply/forward pasa `?account_id=...` porque `GET /content` lo requiere.
- **La aserción de threading es un poll acotado, no un skip blando.** El persist de `email_metadata` posterior al send es best-effort, así que el test re-ejecuta `sync-metadata` en un bucle acotado hasta que aparece la fila del `provider_message_id` enviado, y entonces afirma con fuerza que su `thread_id` es igual al original. Una forma anterior `if row is not None: assert …` no verificaba nada cuando el persist fallaba en blando — no regreses a ella.
- **Forward de Outlook: NO edites el subject más allá de `Fwd:`.** Editar el subject de un draft de `createForward` hace que Graph reasigne un nuevo `conversationId` al guardar, desvinculando el mensaje enviado del hilo original. Gmail no exhibe esto (los forwards pueden iniciar un nuevo hilo).
- **Asimetría de `copy-from-email`.** El flujo de forward afirma `copied_count >= 1` para Gmail (descarga + re-subida) pero `copied_count == 0` para Outlook (`createForward` ya heredó los adjuntos del lado del servidor — el endpoint es un no-op).
- Endpoints ejercitados por primera vez de extremo a extremo aquí: `GET .../reply-context` y `POST .../drafts/{pdid}/attachments/copy-from-email`.

### El rate limiting se queda APAGADO en E2E (deliberado, #11e)

La suite nunca define `RATE_LIMIT_ENABLED`, así que el throttling por cliente está inerte aquí. Esto es una decisión, no un descuido: el limitador hace cortocircuito **antes** de cualquier llamada al provider, así que un round trip real a Gmail/Outlook no añade nada que la suite de integración (app real + BD real + `TestClient`) no cubra ya para ello — y activarlo perjudicaría activamente al E2E, ya que los envíos/syncs reales repetidos de la suite contra las cuentas sembradas podrían disparar un 429 a mitad del flujo. La cobertura determinista de rate-limit vive enteramente en los niveles unit + integration.

### Limpieza de red de seguridad en el teardown del fixture

El fixture `created_resources` rastrea los IDs de buzón temporales y los IDs de sesión. En el teardown, `e2e_session` los borra mediante SQL directo, garantizando que no quede ningún dato huérfano incluso si un test falla a mitad del flujo.

### `create_e2e_schema` — con scope de sesión, autouse

Ejecuta las migraciones de Alembic contra la base de datos E2E real una vez por sesión usando `backend/database/alembic.ini`. Si la base de datos existe pero no tiene fila `alembic_version`, sella las tablas existentes como `0001_initial_schema` antes de hacer upgrade a `head`. Esto mantiene la suite idempotente entre arranques en frío y ejecuciones post-migración.

## Cobertura del endpoint de búsqueda — `test_38a`–`test_38x`

`GET /mailboxes/{mailbox_id}/emails?q=…` (texto libre + operadores estilo Gmail) y su envelope de paginación se ejercitan en el bloque `test_38*`. Por qué la mención dedicada aquí — cada test es la especificación ejecutable de un contrato no visible desde la firma del router por sí sola:

- El contrato sobre el texto libre `q` (semántica OR entre `subject`, `from_email`, `from_name`, AND entre tokens, substring literal, sin stemming) está fijado por `test_38a` (un único `account_id`, cada fila hace match con la aguja en ≥1 columna buscable) y `test_38b` (vista unificada, sin `account_id`: cada fila pertenece a **alguna** cuenta dentro del buzón solicitado — el camino unificado no debe filtrar filas de buzones ajenos).
- `test_38c` fija el envelope `EmailPageOut` contra la cuenta real: `total` es el conjunto filtrado completo (no la longitud de la página), y dos páginas adyacentes no comparten ningún `provider_message_id` — demostrando que la paginación por OFFSET es estable gracias al tie-break del orden total. Se omite cuando la cuenta tiene < 3 emails.
- `test_38d` es la especificación de los operadores `is:read` / `is:unread` (cada fila devuelta debe satisfacer el predicado de estado de lectura). Ambas mitades se quedan en un único test — son el mismo contrato lógico.
- `test_38e` fija la **asimetría** de `in:`: con `box=ALL_MAIL&q=in:sent` cada fila está en `SENT`, y `total` es igual a una consulta directa `box=SENT` — así que el override alcanza el predicado del COUNT, no solo el listado. Una regresión que aplicara `in:` a la página pero no al count pasaría una comprobación de estado a secas.
- `test_38f` fija dos cosas sobre los operadores en AND: los contradictorios (`is:read is:unread`) resuelven a **cero filas en SQL** (`items == []`, `total == 0`), nunca a un 422 (la contraparte ejecutable de la nota de `repository_guide.md` "las contradicciones resuelven en SQL, no vía código"); Y, contra un baseline solo-`is:read` medido primero en el mismo test, que combinar operadores **estrecha, nunca ensancha** (`total <= base_total`).
- `test_38g` es el único lugar que demuestra que el runtime desplegado lleva la base de datos de zonas horarias IANA (`tzdata`): `before:`/`after:` parsean la fecha como medianoche en `Europe/Madrid`, así que un `tzdata` ausente daría un 500 en estas peticiones — un fallo que ni unit ni integration pueden captar (comparten el intérprete; solo E2E ejercita el runtime real). Fronteras fijas de pasado-lejano/futuro-lejano lo mantienen determinista contra un inbox vivo.
- `test_38x` extiende el bloque al eje de **sort + chip de filtro rápido** del mismo endpoint `GET /emails`: `sort`/`sort_dir` (la página es no-decreciente en el orden de subject con acentos plegados del backend — el test pliega los acentos de la misma manera para que un subject acentuado no lo ponga en rojo falsamente) y los chips `unread` / `favorite_only` (cada fila devuelta satisface el chip). Las aserciones son sobre propiedades invariantes, no sobre conjuntos de ids fijos, como `test_38a`; el chip `has_attachment` deliberadamente no se ejercita, con la justificación viviendo en el propio docstring del test para que no pueda derivar.

## Cobertura cache-aside del contenido de email — `test_46a`–`test_46d`

Par MISS/HIT por provider (`46a`/`46b` Gmail, `46c`/`46d` Outlook) sobre `GET .../emails/{id}/content`. Dos acoplamientos invisibles desde las aserciones:

- El `_delete_email_content` entre `sync-metadata` y el GET es **portante**: `sync-metadata` programa un prefetch de contenido en segundo plano de correo reciente no leído que puede pre-cachear el objetivo, así que sin el delete el GET "MISS" resuelve silenciosamente como un HIT y la rama de fetch-al-provider queda sin testear (el assert `is None` justo después fija el MISS restaurado).
- Los tests de HIT afirman `fetched_at` **inalterado** a lo largo de la lectura — la garantía ejecutable del invariante del TTL deslizante (un hit bumpea solo `last_accessed_at`, nunca `fetched_at`). Una regresión que re-sellara `fetched_at` en un hit los pone en rojo.

## Cobertura de autocompletado de destinatarios — `test_46n`

`GET /contacts/suggestions?q=…` es solo-BD (sin llamada al provider). La aguja son los primeros 5 caracteres de la parte local de `SEND_RECIPIENT`, así que las filas SENT que los tests de send/draft dejan la llevan en `to_email` y hacen un match probable sin hacerlo obligatorio — la aserción es **solo-contención** (cada item `{email, name}` lleva el fragmento) y tolera una lista vacía, exactamente como `test_38a`/`38b` contra un inbox vivo. La frontera `q` < 2 → 422 viaja en el **mismo** test (`common_mistakes.md` § 1).

## Cobertura del badge de recuento de no leídos — `test_46o` (Gmail) / `test_46p` (Outlook)

`GET .../emails/unread-count?box=ALL_MAIL|SPAM` es solo-BD (sin llamada al provider). Contratos no visibles desde el router:

- El recuento de control tiene su PROPIO helper `_count_unread_by_box` (`is_read = FALSE`), distinto del hermano `_count_by_box` que ignora el estado de lectura — reutilizar este último haría la aserción vacuamente verdadera. El endpoint cuenta **mensajes individuales, no hilos**, así que su `total` diverge deliberadamente del `total` de hilos de un listado agrupado; el `COUNT(*)` de control por fila es lo que fija esa distinción.
- `test_46o` también viaja en la frontera `box` inválido → 422 en el mismo test (`common_mistakes.md` § 1, como `test_46n`) y una mutación read→restore que demuestra que el badge rastrea un cambio de estado real (restaurado en un `finally`, la regla de "datos sembrados sagrados").
- `test_46p` existe porque cada buzón sembrado tiene una **única cuenta**, así que `test_46o` no puede ejercitar el invariante de back-fill por cuenta — el desglose debe llevar una entrada por CADA cuenta del buzón (rellenada a 0 cuando el GROUP BY la omite), nunca un subconjunto — ni el camino de provider de Outlook. Afirma `total == sum(breakdown)` y que el conjunto de cuentas del desglose es igual a `GET /accounts`. Una suite solo-Gmail pasaría incluso si un buzón multi-cuenta descartara silenciosamente sus cuentas con cero no leídos.

## Cobertura de favoritos — `test_favorites_flow_gmail.py` / `test_favorites_flow_outlook.py` (`test_58`–`test_63`)

Las dos suites ejercitan `PATCH .../favorite`, `POST /favorites/sync` y `GET /emails?favorite=true` contra el provider real, un fichero por implementación en `backend/core/email/`. El contrato portante es **la restauración de estado, no las aserciones**: el toggle de favorito muta estado real del provider (Gmail `STARRED` / Outlook `flag`), así que cada test de toggle captura el `is_favorite` original de la fila elegida y lo restaura en un bloque `finally` — la cuenta de test sembrada debe verse idéntica antes y después de la ejecución (la regla de "los datos de test preexistentes son sagrados" aplica también a la marca de favorito, no solo a las filas/cuentas). El toggle es Provider-First e idempotente, así que la restauración siempre es segura. La aserción de sync es intencionadamente laxa (`favorites_synced <= total_synced`, ambos `>= 0`) porque el recuento de favoritos de la cuenta viva no es fijo; no la aprietes a un número exacto contra un inbox mutable. El camino de retry de Outlook (`set_favorite` / `list_favorite_ids` ahora reintentan el throttling transitorio honrando `Retry-After`) **no** es directamente afirmable aquí — solo se activa ante un hipo real del provider; su cobertura determinista vive en la suite unit. Los tests de listado fijan `group_by_thread=false` deliberadamente: Favoritos está **siempre desagrupado** por decisión de producto (el backend no aplica ningún override de agrupación acoplado a `favorite`, así que `favorite=True`+`group_by_thread=True` es SQL válido de "agrupa los favoritos" que simplemente no es una superficie de producto). El `false` hardcodeado es la especificación ejecutable de esa decisión — no lo "arregles" al valor por defecto del router ni lo voltees para ejercitar la agrupación, lo que cambiaría silenciosamente lo que el test fija.
