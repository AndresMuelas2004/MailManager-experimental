# Límites de favoritos

Catálogo de topes, cuotas y comportamientos cuantitativos de la feature de favoritos, más la lista de "qué NO soporta". El comportamiento, los flujos y el porqué de las decisiones de diseño están en [`../features/favoritos.md`](../features/favoritos.md).

---

## 1. Topes y parámetros cuantitativos

| Parámetro | Valor exacto | Notas |
|---|---|---|
| Marca por correo (granularidad de la API) | **1 correo por llamada** | El endpoint de toggle actúa sobre un único correo. No existe endpoint de marca en bloque. |
| Marca multi-selección | **No existe** | No hay acción de favorito en la barra de selección múltiple ni endpoint por lotes; la estrella se alterna fila a fila. Ver § 4. |
| Reintentos de la marca (lado Gmail) | **5 intentos** (1 inicial + 4 reintentos) | La marca Gmail pasa por la maquinaria de modificación de etiquetas por lotes, con 4 reintentos sobre errores transitorios. |
| Espera entre reintentos (Gmail) | **1 s** fija entre intentos | No es backoff exponencial en esta ruta. |
| Códigos transitorios que reintenta (Gmail) | `429`, `500`, `502`, `503`, `504` | Errores permanentes (`400`/`401`/`403`/`404`) no se reintentan. |
| Reintentos de la marca (lado Outlook) | **0 reintentos** (1 único intento) | La marca Outlook es un único `PATCH` del estado de la bandera que NO pasa por ningún bucle de reintentos: el primer fallo de red/HTTP aborta la operación. Asimetría real con Gmail (ver § 5). |
| Paginación al listar favoritos del proveedor (Gmail) | **500 ids por página** | Pagina hasta agotar; incluye spam y papelera en el conteo del proveedor. |
| Paginación al listar favoritos del proveedor (Outlook) | **100 ids por página** | Pagina por `nextLink` hasta agotar. |
| Filas reconciliadas por la sincronización | **Toda la cuenta** (1 sentencia SQL) | Marca verdadero/falso cada fila de la cuenta en una sola transacción. |
| Cabecera de estabilidad de IDs (Outlook) | `Prefer: IdType="ImmutableId"` en **todas** las llamadas | Sin ella, los ids dejan de casar con los de la base de datos local tras mover el mensaje. |
| Resultados de la pestaña de Favoritos por carga | **200 por defecto** (máximo 500) | Hereda la paginación del listado general; sin scroll infinito en el MVP. |
| Tokens de búsqueda dentro de Favoritos | **10 máximo** | La lupa silenciosamente recorta a 10 tokens (ver [`lupa.md`](../features/lupa.md)). |
| Mínimo de caracteres de búsqueda | **2** | Por debajo de 2, no filtra. |

---

## 2. Exclusiones por defecto

| Regla | Comportamiento |
|---|---|
| Spam | Excluido de la vista de Favoritos salvo que se seleccione la caja `SPAM` explícitamente. |
| Papelera | Excluida de la vista de Favoritos salvo que se seleccione la caja `TRASH` explícitamente. |
| Ancla por defecto | "Todo menos spam y papelera". Cualquier otra caja explícita (`SENT`) se respeta tal cual, sin colar favoritos de otras cajas. |
| Bandejas ficticias | Misma exclusión de spam/papelera cuando su filtro no especifica caja. |

---

## 3. Códigos de error y HTTP

| Operación | Situación | HTTP | `code` |
|---|---|---|---|
| Toggle (`PATCH .../favorite`) | El correo no existe en local (pre-check antes de llamar al proveedor) | **404** | `email_not_found` |
| Toggle | La fila desaparece entre el pre-check y el `UPDATE` (carrera, 0 filas afectadas) | **404** | `email_not_found` |
| Toggle | La cuenta no existe / no pertenece al mailbox | **404** | `account_not_found` |
| Toggle | La cuenta no está conectada / auth silenciosa falla | **409** | `account_not_connected` |
| Toggle | El proveedor rechaza o falla la marca (Provider-First; no se persiste en local) | **502** | `favorite_update_error` |
| Sync (`POST /favorites/sync`) | La cuenta indicada no existe / no pertenece al mailbox | **404** | `account_not_found` |
| Sync | Una cuenta falla auth o el proveedor responde con error → se aborta toda la sincronización | **502** | `favorite_sync_error` |

Nota: el toggle es Provider-First, así que un fallo del proveedor (502 `favorite_update_error`) nunca llega a tocar la base de datos local; el frontend revierte la estrella optimista al recibirlo.

---

## 4. Lo que NO soporta (limitaciones aceptadas para el MVP)

- **No hay marca de favoritos en bloque ni acción multi-selección.** La estrella solo se alterna correo a correo desde su fila (el único punto de entrada es `setFavorite` / el endpoint `PATCH .../favorite`). La barra de acciones en bloque (`useEmailBulkActions`) cubre papelera, leído/no leído y spam, **pero no incluye favoritos**, y no existe ningún endpoint de marca por lotes. Las APIs de ambos proveedores ofrecen modificación por lotes, pero añadirla obligaría a diseñar un contrato de "éxito parcial" (qué correos se marcaron y cuáles fallaron) que no aporta valor al volumen del MVP. Mantiene la superficie de la API y el modelo de errores simples.

- **La sincronización no importa correos nuevos (Opción A).** Un correo marcado como favorito en el proveedor que MailManager todavía no tiene en su base de datos local se ignora en silencio durante la sincronización; no se crea fila nueva. La razón: la llamada de listado solo devuelve identificadores, e importar forzaría una segunda ronda de llamadas por id (una sincronización de metadata encubierta) que ya es responsabilidad de la sincronización general de la bandeja. El favorito aparece tras la siguiente sincronización de metadata.

- **No hay botón de favorito en el visor del correo abierto.** La estrella solo está disponible en las filas de los listados. Marcar/desmarcar desde el correo abierto requeriría exponer el botón también ahí; fuera de scope del MVP.

- **No hay sincronización global multi-mailbox de un tirón.** La sincronización opera sobre el mailbox actual. Reconciliar favoritos de cuentas repartidas entre varios mailboxes reales (caso de una bandeja ficticia que abarca varios) exige disparar una sincronización por cada mailbox implicado.

- **No hay carpeta/colección de favoritos en el proveedor.** El favorito es solo la etiqueta `STARRED` (Gmail) o la bandera de seguimiento (Outlook); no se crea ninguna carpeta dedicada ni se ordena por prioridad de bandera.

- **No hay ordenación por relevancia ni por prioridad de bandera.** La pestaña de Favoritos ordena estrictamente por fecha de recepción descendente, igual que el resto de listados.

---

## 5. Notas de asimetría Gmail vs Outlook

| Aspecto | Gmail | Outlook |
|---|---|---|
| Representación del favorito | Etiqueta `STARRED` | Bandera de seguimiento (`flag.flagStatus = "flagged"`) |
| Marca (set) | Añade/quita la etiqueta vía modificación por lotes (1 elemento) | `PATCH` del estado de la bandera |
| Reintentos de la marca | **5 intentos** (1 + 4), 1 s fija entre intentos | **0 reintentos** (1 único intento; el `PATCH` no tiene bucle de reintentos) |
| Listado de favoritos | `messages.list` filtrando por `STARRED`, incluye spam/papelera | `$filter=flag/flagStatus eq 'flagged'` |
| Página de listado | 500 ids | 100 ids |
| Estabilidad de IDs | Estable de por sí | Requiere `Prefer: IdType="ImmutableId"` en cada llamada |
| Idempotencia | Sí (re-aplicar/re-quitar etiqueta es no-op) | Sí (re-poner el mismo estado es no-op) |
