# Límites de carpetas y reglas

Catálogo cuantitativo de **hasta dónde llegan** las carpetas propias y las reglas de organización: topes con cifras exactas, endpoints, códigos de error, permisos reutilizados y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones) está en **[../features/carpetas-y-reglas.md](../features/carpetas-y-reglas.md)**. Aquí solo van los números y los límites.

Todos estos valores están **hardcodeados** y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook), salvo donde se indique una asimetría. **No se requiere ningún scope de OAuth nuevo**: las carpetas son etiquetas de usuario de Gmail / categorías de Outlook, cubiertas por los `gmail.modify` / `Mail.ReadWrite` ya concedidos.

---

## 1. Topes de una carpeta

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Nombre — longitud mínima | **1 carácter** (tras recortar espacios) | Frontend y backend | Un nombre vacío o solo de espacios se rechaza con error de validación (422). |
| Nombre — longitud máxima | **120 caracteres** | Frontend y backend | El campo limita la entrada y el backend revalida. |
| Nombre — unicidad | **Único por usuario, sin distinguir mayúsculas/minúsculas** | Backend (índice único + pre-chequeo) | "universidad" vs "Universidad" colisionan → 409 `folder_name_conflict`. La defensa dura es un índice único `(owner_user_id, lower(name))`; el pre-chequeo da el 409 amable. |
| Color — longitud máxima | **32 caracteres** | Frontend y backend | Opcional. Es una **pista visual solo de la app** (ver § 8), no se envía al proveedor. |

---

## 2. Topes de una regla

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Nombre — longitud máxima | **120 caracteres** | Backend | Opcional (una regla puede no tener nombre). |
| Condición "remitente exacto" (`match_from_email`) — longitud | **hasta 320 caracteres** | Backend | Opcional. Se normaliza a minúsculas y sin espacios extremos, y debe tener **forma de email** (un `@`, dominio con punto); si no, 422. La coincidencia es **igualdad exacta** de la dirección completa, insensible a mayúsculas — no subcadena. |
| Condición "asunto contiene" (`match_subject_contains`) — longitud | **1 a 500 caracteres** | Backend | Opcional (mínimo 1 si se indica: la cadena vacía se rechaza). Subcadena insensible a mayúsculas **y tildes**. |
| Condiciones — mínimo | **al menos 1** (remitente y/o asunto) | Backend | Una regla sin ninguna condición se rechaza. Si se indican las dos, se exigen **ambas** (AND). |
| Carpeta destino (`target_folder_id`) | **obligatoria**, debe ser del usuario | Backend | Una carpeta ajena o inexistente → 404 `folder_not_found`. |
| Estado | `is_enabled` (por defecto **activada**) | Backend | Solo las reglas activas clasifican. Se activa/desactiva sin borrar. |

> **Un valor por condición.** Cada regla admite **una sola** dirección exacta y **un solo** texto de asunto. No hay listas de remitentes ni de palabras, ni `OR` entre condiciones dentro de una regla.

---

## 3. Topes del listado de una carpeta

Comparte el mismo motor de listado y la misma lupa que los buzones normales y las bandejas ficticias; por eso estos topes coinciden con los de [lupa.md](../features/lupa.md) y [listado-de-correos.md](listado-de-correos.md).

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Mínimo de caracteres para la lupa | **2** | Frontend y backend | El backend rechaza un `q` de 1 carácter. |
| Longitud máxima del término de lupa | **200 caracteres** | Backend | Rechazado en el borde de la API. |
| Conversaciones por **página** | **50** (`limit` por defecto) | Backend = tamaño de página del frontend | La vista **agrupa por conversación siempre** y **deduplica** el mismo mensaje llegado por dos cuentas; el total exacto es de **hilos distintos**. |
| Tope técnico del parámetro `limit` | **1 a 500** | Backend | El endpoint acepta `limit` hasta 500; el frontend usa 50. |
| Alcance de bandeja por defecto | **todas las bandejas reales salvo "DELETED"** | Backend | Asimetría deliberada con las bandejas ficticias: la carpeta muestra sus miembros estén en Recibidos, Enviados, Spam, Papelera o Archivados; solo el borrado definitivo (DELETED) queda fuera **siempre**. Un `in:<box>` en la lupa acota a esa única bandeja. |

---

## 4. Topes de "aplicar a los correos existentes"

Trabajo en segundo plano que recorre todo el correo ya sincronizado del usuario y clasifica el que cumple la condición de una regla.

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Tamaño de página del recorrido | **200 mensajes** | Backend (`_RULE_APPLY_PAGE_SIZE`) | Recorrido por keyset (checkpoint reanudable), no por offset. |
| Pausa entre páginas | **0,3 s** | Backend (`_RULE_APPLY_PAGE_DELAY_S`) | Ritmo fijo conservador, misma filosofía que la descarga inicial. |
| Frecuencia de sondeo del progreso | **2 s** | Frontend (`useRuleApplyStatus`) | Sondea mientras el trabajo está activo; para al terminar. |
| Reintento automático ante fallo | **igual que la descarga inicial** | Backend (worker compartido) | Comparte el worker, el reaper y la cadencia de reintento (número de intentos + cool-off) de la descarga masiva inicial — ver [sincronizacion.md](sincronizacion.md) para las cifras exactas. |
| Dependencia del worker | **sí, sin plan B** | Backend | A diferencia de la clasificación del correo **nuevo** al sincronizar (que corre como tarea posterior a la respuesta, independiente del worker), "aplicar a los existentes" **necesita el worker de segundo plano**; si está apagado, el trabajo se queda en espera (`pending`) hasta que arranque. |

**Estado del trabajo** (`GET /rules/{rule_id}/apply-status`): el trabajo está en uno de cinco estados — **sin iniciar** (la regla nunca se aplicó), **en cola**, **en curso**, **completado** o **fallido** — y reporta el número de correos ya procesados y un indicador de "activo" (verdadero mientras está en cola o en curso). Es una lectura local y barata, apta para sondear.

---

## 5. Endpoints, formas de respuesta y códigos de error

### 5.1 Endpoints

Carpetas y reglas son **a nivel de usuario** (sin prefijo `/mailboxes/{id}`; agregan sobre todas las cuentas del usuario), salvo el meter/sacar por correo, que necesita el contexto de cuenta del mensaje en la ruta.

| Método y ruta | Qué hace | Qué devuelve |
|---------------|----------|--------------|
| `GET /folders` | Lista las carpetas del usuario | Las carpetas |
| `POST /folders` | Crea una carpeta | La carpeta creada (201) |
| `GET /folders/{folder_id}` | Lee una carpeta | La carpeta |
| `PATCH /folders/{folder_id}` | Renombra y/o recolorea | La carpeta actualizada |
| `DELETE /folders/{folder_id}` | Borra la carpeta (refleja al proveedor) | Confirmación de borrado |
| `GET /folders/{folder_id}/emails` | Lista los correos de la carpeta (paginado, con lupa) | Una página de correos |
| `POST /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/folders` | Mete el correo en una carpeta (indica la carpeta en el cuerpo) | Las carpetas del correo tras la acción |
| `DELETE /mailboxes/{mailbox_id}/accounts/{account_id}/emails/{provider_message_id}/folders/{folder_id}` | Saca el correo de una carpeta | Las carpetas del correo tras la acción |
| `GET /rules` | Lista las reglas del usuario | Las reglas |
| `POST /rules` | Crea una regla (opcional "aplicar a los existentes") | La regla creada (201) |
| `GET /rules/{rule_id}` | Lee una regla | La regla |
| `PATCH /rules/{rule_id}` | Edita una regla (opcional "aplicar a los existentes") | La regla actualizada |
| `DELETE /rules/{rule_id}` | Borra la regla | Confirmación de borrado |
| `POST /rules/{rule_id}/apply` | Lanza (o relanza) "aplicar a los existentes" | El estado del trabajo (§ 4) |
| `GET /rules/{rule_id}/apply-status` | Progreso del "aplicar a los existentes" | El estado del trabajo (§ 4) |

### 5.2 Códigos de error

| Código | HTTP | Cuándo |
|--------|------|--------|
| `folder_not_found` | **404** | Carpeta inexistente o de otro usuario (una ajena es indistinguible de una inexistente, para no filtrar qué existe). |
| `folder_name_conflict` | **409** | Ya existe una carpeta con ese nombre (insensible a mayúsculas). |
| `folder_operation_error` | **500** | Fallo interno (BD/lógica) del lado de la app durante una operación de carpeta. |
| `rule_not_found` | **404** | Regla inexistente o de otro usuario. |
| `rule_validation_error` | **422** | Una edición dejaría la regla sin ninguna condición (re-validación de la regla ya fusionada en el servicio; sobre el sobre `{"error":{...}}` del proyecto, distinto del 422 de esquema de FastAPI `{"detail":[...]}`). |
| `rule_operation_error` | **500** | Fallo interno durante una operación de regla (CRUD o encolado del "aplicar"). |
| `rule_apply_status_error` | **500** | Fallo (no de BD) al leer el estado del "aplicar". |
| *(escalado)* `external_api_error` | **502** | Fallo del **proveedor** al aplicar/quitar/renombrar/borrar la etiqueta o categoría. No se confunde con los 500 internos de arriba. |
| *(reutilizados)* `account_not_found` / `email_not_found` | **404** | En meter/sacar por correo: cuenta ajena o mensaje inexistente (misma política anti-fuga que los adjuntos/favoritos). |
| *(reutilizado)* `account_not_connected` | **409** | Token de cuenta caducado/revocado durante una operación de carpeta. |

Toda clave desconocida en el cuerpo de crear/editar carpeta o regla, o del meter-en-carpeta, se rechaza con 422 (`extra="forbid"` en todos esos esquemas).

---

## 6. Reflejo en el proveedor (mapeo exacto y adopción)

| Aspecto | Gmail | Outlook |
|---------|-------|---------|
| Materialización | **Etiqueta de usuario** (`users.labels`); `provider_ref` = id opaco de la etiqueta | **Categoría** (`message.categories`); `provider_ref` = **el nombre** de la categoría |
| Scope | `gmail.modify` (existente, sin scope nuevo) | `Mail.ReadWrite` (existente, sin scope nuevo) |
| Cuándo se crea | Perezosa: al **primer** correo metido en esa cuenta | Perezosa: la categoría "existe" al ponerla al primer correo |
| Adopción de una homónima | Reutiliza una etiqueta `type='user'` con el mismo nombre (insensible a mayúsculas) | El mismo nombre **es** la misma categoría (adopción automática) |
| Aplicar / quitar a un correo | `messages.modify` por lotes (de 100) | **Lectura-modificación-escritura** por mensaje: lee la lista **fresca** de categorías, añade/quita el nombre y reescribe (nunca desde una copia local — evitaría borrar categorías ajenas del usuario) |
| Renombrar | `labels.patch` en el sitio (una llamada) | **Sin objeto etiqueta**: se re-etiqueta cada correo miembro (nombre de categoría inmutable) y se re-apunta la referencia guardada |
| Borrar | `labels.delete` (la quita de todos sus correos; un 404 se trata como éxito idempotente) | **Sin objeto etiqueta**: se quita la categoría de cada correo miembro |

> **Migración `0045`**: crea las cinco tablas del sistema (`folders`, `folder_account_links`, `email_folder_members`, `rules`, `rule_apply_jobs`). No añade ningún scope.

> **Trampa de infraestructura.** La condición "asunto contiene" (tanto en la evaluación al sincronizar como en el recorrido de "aplicar a los existentes") depende de la extensión `unaccent` de PostgreSQL (migración `0020_create_extension_unaccent`), igual que la lupa. En una base de datos sin esa migración, la comparación de asunto falla en tiempo de consulta. No es un límite de producto, sino un requisito de despliegue.

---

## 7. Validaciones que devuelven error en vez de "tragar" el fallo

| Entrada rechazada | Resultado | Por qué |
|-------------------|-----------|---------|
| Nombre de carpeta vacío o solo espacios | 422 | Se recortaría a vacío y persistiría un nombre en blanco. |
| Nombre de carpeta duplicado (insensible a mayúsculas) | 409 `folder_name_conflict` | El índice único es la defensa; el 409 es la superficie amable. |
| Regla sin ninguna condición (al crear o al editar) | 422 (esquema al crear; `rule_validation_error` al editar sobre la regla fusionada) | Una regla sin condición casaría con todo o con nada; ambos son un error del usuario. |
| `match_from_email` sin forma de email | 422 | Se compara por igualdad exacta con la dirección normalizada; un valor no-email nunca casaría. |
| `match_subject_contains: ""` | 422 (mínimo 1) | Una cadena vacía equivaldría a "contiene cualquier cosa". Para quitar la condición se omite la clave. |
| Carpeta destino de una regla ajena/inexistente | 404 `folder_not_found` | No se puede apuntar una regla a una carpeta que no es tuya. |
| Carpeta / regla ajena (leer/editar/borrar) | 404, nunca 403 | No confirmar la existencia probando identificadores. |
| En meter/sacar por correo: cuenta ajena o mensaje inexistente | 404 (`account_not_found` / `email_not_found`) | Misma política anti-fuga que adjuntos/favoritos. |
| Clave desconocida en el cuerpo | 422 (`extra="forbid"`) | Un campo mal escrito se descartaría en silencio. |

---

## 8. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Ejemplo | Por qué |
|------------|---------|---------|
| **Clasificación en tiempo real** | Un correo que llega ahora no cae en su carpeta hasta la siguiente sincronización | Las reglas se evalúan al sincronizar (al abrir, refrescar o sincronizar automáticamente), no en el instante de entrega del proveedor. |
| **Condiciones más allá de remitente y asunto** | No hay regla por "el cuerpo contiene…", ni por fecha, adjuntos o destinatario | Solo se miran los datos básicos del correo (remitente exacto y/o asunto-contiene). Es una ampliación prevista, fuera del MVP. |
| **Acciones distintas de "meter en carpeta"** | No hay reglas que marquen como leído, muevan a papelera, reenvíen, etc. | La única acción de esta versión es asignar a una carpeta. |
| **Reflejar el color de la carpeta en el proveedor** | El color elegido no viaja ni a Gmail ni a Outlook | El color es una pista visual **solo dentro de la app** (punto en la barra lateral, chips junto al correo). ⚠️ **Discrepancia conocida**: tanto la descripción funcional como el texto de ayuda del formulario de carpeta ("Se muestra en MISSELA y en Gmail…") afirman que el color se ve en Gmail; el backend **no** lo envía (la etiqueta de Gmail se crea sin color). Es una inconsistencia de la ayuda de UI, no del comportamiento documentado aquí. En Outlook, además, la propia categoría se aplica **sin color** porque no se toca la lista maestra de categorías (necesitaría el scope prohibido `MailboxSettings.ReadWrite`). |
| **Importar las etiquetas/categorías previas del usuario como carpetas** | Tus 30 etiquetas de Gmail no aparecen solas como 30 carpetas | La app gestiona las carpetas que se crean en ella; solo **reutiliza** una etiqueta/categoría existente si coincide el nombre al materializar. La reconciliación al sincronizar afecta únicamente a las carpetas gestionadas por la app; las etiquetas del sistema y las ajenas no se convierten en carpetas. |
| **Clasificar correo aún no sincronizado** | El correo viejo que nunca se ha traído no se etiqueta hasta sincronizarse | Las reglas trabajan sobre la copia local ya sincronizada. |
| **Prioridad u orden entre reglas** | No se puede decir "aplica antes esta regla" | Todas las reglas activas se evalúan; como la pertenencia es múltiple, varias coincidencias meten el correo en **varias** carpetas a la vez, sin conflicto que resolver. |
| **Combinar condiciones con `OR` o con listas** | No "de A o de B", ni "asunto contiene X o Y" | Cada regla es un `AND` de como mucho una dirección exacta y un texto de asunto. Para varios remitentes o palabras, se crean varias reglas apuntando a la misma carpeta. |
| **"Aplicar a los existentes" sin el worker** | Con el worker de segundo plano apagado, el trabajo no progresa | No hay ruta alternativa: el trabajo espera `pending` hasta que el worker arranque. (La clasificación del correo **nuevo** al sincronizar sí es independiente del worker.) |

---

> Las carpetas y las reglas llegan hasta: **carpetas** de nombre 1–120 caracteres (único por usuario sin mayúsculas), color opcional de hasta 32 caracteres solo-app, materializadas de forma perezosa como etiqueta de Gmail / categoría de Outlook (adoptando una homónima, sin scope nuevo) y reflejadas al proveedor en los dos sentidos; su listado se pagina de a 50 hilos y muestra los miembros en cualquier bandeja salvo los borrados definitivos. **Reglas** con condición de remitente exacto (≤320) y/o asunto-contiene (1–500), al menos una, acción única "meter en carpeta", evaluadas al sincronizar sobre el correo nuevo, con "aplicar a los existentes" en segundo plano (páginas de 200, pausa 0,3 s, sondeo 2 s, reanudable e idempotente, dependiente del worker). Y deliberadamente no soporta: clasificación en tiempo real, condiciones fuera de remitente/asunto, acciones distintas de asignar a carpeta, reflejar el color al proveedor, importar etiquetas previas, prioridad entre reglas ni `OR`. El comportamiento completo está en [../features/carpetas-y-reglas.md](../features/carpetas-y-reglas.md).
