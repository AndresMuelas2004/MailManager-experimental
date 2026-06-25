# Ordenar y filtrar rápido el listado — límites y alcance

Catálogo de **hasta dónde llega** la ordenación y el filtrado rápido del listado: valores exactos admitidos, cómo se serializan, y la lista de "qué NO soporta" con su porqué. El comportamiento narrado (flujos, UX, combinaciones) vive en **[../features/ordenar-y-filtrar-listado.md](../features/ordenar-y-filtrar-listado.md)**.

Es una extensión del listado base: los topes de **paginación, tamaño de página y total** no cambian y siguen en **[listado-de-correos.md](listado-de-correos.md)**.

---

## 1. Opciones de orden (cerradas)

| Concepto | Valores exactos | Por defecto | Dónde se aplica | Notas |
|---|---|---|---|---|
| Criterio de orden (`sort`) | **`date`**, **`sender`**, **`subject`** | `date` | Parámetro `sort` del backend (validado como `Literal`) | Lista cerrada. Cualquier otro valor sobre HTTP → **422**; en llamadas directas al repositorio (tests), un valor desconocido cae a `date`. |
| Dirección (`sort_dir`) | **`asc`**, **`desc`** | `desc` | Parámetro `sort_dir` del backend (`Literal`) | `desc` = más reciente / Z→A (el de siempre); `asc` = más antiguo / A→Z. |
| Columna real de "Fecha" | `received_at` | — | Backend | Es el criterio histórico del listado. |
| Columna real de "Remitente" | `lower(unaccent(coalesce(nullif(from_name, ''), from_email)))` | — | Backend (`_SORT_EXPRESSIONS`) | Nombre visible y, **si está vacío/NULL, la dirección de correo**; insensible a mayúsculas y tildes. |
| Columna real de "Asunto" | `lower(unaccent(coalesce(subject, '')))` | — | Backend (`_SORT_EXPRESSIONS`) | Insensible a mayúsculas y tildes; un asunto NULL se trata como cadena vacía. |
| Desempate (orden total) | `account_id, provider_message_id` (siempre añadido al final) | — | Backend (`_build_order_by`) | Hace el orden determinista y la paginación por posición segura, igual que en el orden por fecha base. |
| Orden secundario en `sender` / `subject` | `received_at DESC` (antes del desempate) | — | Backend | Agrupa los correos del mismo remitente / asunto del más reciente al más antiguo, **sea cual sea** la dirección elegida. |
| Conteo total con orden | El total **no** recibe `sort` / `sort_dir` | — | Backend (`count_filtered` no acepta parámetros de orden) | Reordenar **no** cambia el total; solo el `SELECT` lleva `ORDER BY`. |

> El criterio de orden se resuelve **siempre** contra la lista blanca cerrada `_SORT_EXPRESSIONS` (clave pública → fragmento SQL fijo y de confianza); el valor crudo de `sort` **nunca** se interpola en la consulta. Misma disciplina anti-inyección que los filtros guardados y los operadores de la lupa.

---

## 2. Chips de filtro rápido (cerrados)

| Chip (es) | Parámetro backend | Filtro real | Operador equivalente de la lupa | Por defecto |
|---|---|---|---|---|
| **No leídos** | `unread` (bool) | `is_read = FALSE` | `is:unread` | `false` (apagado) |
| **Con adjuntos** | `has_attachment` (bool) | `has_attachments = TRUE` | `has:attachment` | `false` (apagado) |
| **Destacados** | `favorite_only` (bool) | `is_favorite = TRUE` | `is:favorite` | `false` (apagado) |

- Los tres chips se **combinan con Y lógico** entre sí, con cualquier operador de la búsqueda `q` y con el texto libre.
- Bajo el capó, cada chip se traduce a la **misma** cláusula de operador que usa la lupa (`is_read_op` / `has_attachments` / `is_favorite_op` en el registro `_OPERATOR_CLAUSE_BUILDERS`); **no añaden superficie SQL nueva**.
- Los parámetros de chip son **keyword-only** en la capa de servicio con valor por defecto "apagado", de modo que cualquier llamador existente que no los pase obtiene exactamente el comportamiento previo a la feature.

> **`favorite_only` ≠ `favorite`.** Son dos parámetros distintos del mismo endpoint: `favorite_only` (chip "Destacados") es un filtro `AND` normal sobre la bandeja actual; `favorite` (anclaje de la pestaña de Favoritos) excluye `TRASH`/`SPAM` y alimenta la vista dedicada. No deben confundirse — ver [favoritos.md](favoritos.md).

---

## 3. Estado en la URL (frontend)

| Control | Clave en la URL (navegador) | Cuándo se escribe | Clave en el cable (HTTP) |
|---|---|---|---|
| Criterio de orden | `sort` (`date` / `sender` / `subject`) | Solo si **no** es `date` (URL limpia) | `sort` |
| Dirección | `dir` (`asc`) | Solo si es `asc` | `sort_dir` |
| Chip "No leídos" | `unread=1` | Solo si está activo | `unread=true` |
| Chip "Con adjuntos" | `attachment=1` | Solo si está activo | `has_attachment=true` |
| Chip "Destacados" | `favorite=1` | Solo si está activo | `favorite_only=true` |

- **Asimetría nombre-de-URL vs nombre-de-cable** (deliberada): en la URL del navegador los chips se llaman `attachment` y `favorite`, pero al backend viajan como `has_attachment` y `favorite_only`. El criterio por defecto (`date` / `desc`) y los chips apagados **no** se escriben (URL y petición limpias, idénticas a las de antes de la feature).
- **Cualquier cambio de control borra `page`** de la URL (reinicio a la página 1, en la misma actualización). Se usa reemplazo de historial (no se apila una entrada por cada filtro, para que "Atrás" no deshaga filtro a filtro).
- Un valor de URL desconocido o ausente (tecleado a mano, enlace antiguo) cae al valor por defecto sin romper el listado.
- Cada combinación de orden/filtro se **cachea por separado** en el frontend (sus campos forman parte de la clave de caché), igual que la página.

---

## 4. Alcance: dónde aplica y dónde no

| Superficie | ¿Lleva orden + chips? | Notas |
|---|---|---|
| Bandeja **unificada** (4 bandejas) | **Sí** | `UnifiedInboxPage`. |
| Vista de **una cuenta** (4 bandejas) | **Sí** | `AccountInboxPage`. |
| Pestaña de **Favoritos** (unificada y por cuenta) | **No** | No pasa los controles al listado; la petición al backend queda **byte a byte idéntica** a la de antes de la feature. |
| **Bandejas ficticias** | **No** | Tienen filtros guardados propios ([bandejas-ficticias.md](bandejas-ficticias.md)). |

---

## 5. Lo que NO soporta (y por qué)

- **No se ordena por tamaño, por número de adjuntos ni por relevancia.** Solo fecha, remitente o asunto. *Por qué:* son los tres criterios que pidió la ficha; el tamaño no se almacena en metadata y un ranking por relevancia es terreno descartado también en la lupa ([lupa.md](lupa.md)).
- **No hay orden multi-criterio elegible por el usuario** (p. ej. "por remitente y luego por asunto"). Solo un criterio primario; el secundario (`received_at DESC`) y el desempate son fijos e invisibles. *Por qué:* cubre los casos reales sin complicar la UI ni la consulta.
- **El orden no se recuerda al cambiar de bandeja o de cuenta.** Vuelve a fecha descendente en cada contexto nuevo. *Por qué:* es un ajuste "de la vista que estás mirando", no una preferencia global; replicarlo entre vistas sorprendería más de lo que ayudaría.
- **El orden no persiste en servidor.** Vive solo en la URL del navegador; no es una columna de `users` ni un campo de la sesión. *Por qué:* es estado de UI efímero, como la página actual o el término de búsqueda.
- **Los chips no aparecen en Favoritos ni en las bandejas ficticias.** *Por qué:* Favoritos ya es "solo destacados" por definición y no agrupa; las ficticias tienen su propio sistema de filtros guardados. Quedó fuera del alcance de esta feature.
- **No hay un chip "leídos", "sin adjuntos" ni "no destacados"** (los chips solo filtran en positivo). *Por qué:* el atajo cubre los tres casos frecuentes; el caso inverso sigue disponible escribiendo el operador correspondiente en la lupa (`is:read`, etc.).
- **El chip "Con adjuntos" puede dejar fuera correos con adjunto que nadie ha abierto todavía.** El indicador de adjuntos arranca apagado y se enciende al abrir el correo por primera vez (estrategia "lazy pura"). *Por qué:* el chip **es** el operador `has:attachment` por debajo y hereda su límite exacto; arreglarlo exigiría descubrir adjuntos en cada sincronización, fuera de este alcance — ver [adjuntos.md](adjuntos.md) y [../limits/lupa.md](lupa.md).
- **Reordenar no trae correos nuevos del proveedor.** Como todo el listado, ordena y filtra solo sobre la copia local sincronizada ([listado-de-correos.md](listado-de-correos.md)). *Por qué:* el listado nunca consulta al proveedor en vivo; ordenar es una operación local sobre lo ya sincronizado.

---

## 6. Enlaces

- Comportamiento de ordenar y filtrar: [../features/ordenar-y-filtrar-listado.md](../features/ordenar-y-filtrar-listado.md)
- Listado base (paginación, total, orden por defecto): [../features/listado-de-correos.md](../features/listado-de-correos.md) · [./listado-de-correos.md](listado-de-correos.md)
- Búsqueda y operadores (la lupa): [../features/lupa.md](../features/lupa.md) · [./lupa.md](lupa.md)
- Favoritos: [../features/favoritos.md](../features/favoritos.md) · [./favoritos.md](favoritos.md)
- Agrupación por conversación: [../features/conversaciones.md](../features/conversaciones.md) · [./conversaciones.md](conversaciones.md)
- Adjuntos (clip, estrategia lazy): [../features/adjuntos.md](../features/adjuntos.md) · [./adjuntos.md](adjuntos.md)
