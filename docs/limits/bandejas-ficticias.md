# Límites de las bandejas ficticias

Catálogo cuantitativo de **hasta dónde llega** una bandeja ficticia (virtual): topes con cifras exactas y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/bandejas-ficticias.md](../features/bandejas-ficticias.md)**. Aquí solo van los números y los límites.

Todos estos valores están **hardcodeados** y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook). El **listado** de una bandeja ficticia **nunca llama al proveedor** (solo filtra lo ya sincronizado en la base de datos local), así que ningún límite del listado depende de cuotas externas. Matiz: **al abrir** la vista sí se dispara una sincronización previa de las cuentas implicadas (orquestada por el frontend, reutilizando la sincronización por cuenta de las bandejas reales), que sí consume cuota del proveedor — pero eso es el paso de apertura, no la consulta del listado. Ver § 5 y [../features/bandejas-ficticias.md](../features/bandejas-ficticias.md) § 6.4.

---

## 1. Topes de la definición (crear / editar)

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Nombre — longitud mínima | **1 carácter** (tras recortar espacios) | Frontend y backend | Un nombre vacío o compuesto solo de espacios se rechaza con error de validación. Los espacios sobrantes se recortan antes de guardar. |
| Nombre — longitud máxima | **120 caracteres** | Frontend y backend | El campo limita la entrada y el backend revalida. |
| Cuentas — mínimo | **1 cuenta** | Frontend y backend | Una bandeja sin ninguna cuenta es inútil; se rechaza con error de validación en vez de guardar una fila vacía. |
| Cuentas — máximo | **sin tope** | — | No hay límite numérico de cuentas agregadas (más allá de las que el usuario posea). |
| "Remitente exacto" — longitud | **1 a 320 caracteres** | Backend | Mínimo 1 (la cadena vacía se rechaza, ver § 4); 320 es el largo máximo de una dirección de correo. |
| "Asunto contiene" — longitud | **1 a 200 caracteres** | Backend | Mínimo 1 (la cadena vacía se rechaza, ver § 4). |

---

## 2. Criterios de filtro disponibles (exactamente 6)

El lenguaje de filtros es una **lista cerrada de 6 claves**. Cualquier otra clave se rechaza con error de validación en el borde de la API (no se ignora en silencio).

| Criterio | Tipo de coincidencia | Valores admitidos | Notas |
|----------|----------------------|-------------------|-------|
| `box` | Carpeta única | `ALL_MAIL`, `SENT`, `SPAM`, `TRASH` | Muestra **solo** esa carpeta. Mutuamente excluyente con `box_not_in`. |
| `box_not_in` | Carpetas a excluir | Lista de `ALL_MAIL`/`SENT`/`SPAM`/`TRASH` | Muestra todo **menos** esas carpetas. Lista vacía `[]` = "no excluir nada". Mutuamente excluyente con `box`. |
| `from_email` | **Igualdad exacta** (insensible a mayúsculas) | Texto (1–320) | Coincidencia exacta de la dirección completa, **no** subcadena. |
| `subject_contains` | **Subcadena** (insensible a mayúsculas y tildes) | Texto (1–200) | "Contiene" en cualquier posición; `%` y `_` se tratan como literales. |
| `is_read` | Igualdad booleana | `true` / `false` | "Solo leídos" / "solo no leídos". |
| `is_favorite` | Igualdad booleana | `true` / `false` | "Solo favoritos" / "excluir favoritos". |

### Reglas de combinación

- **Todos los criterios rellenados se aplican a la vez (AND)**: añadir filtros siempre **estrecha** el resultado, nunca lo amplía.
- **`box` y `box_not_in` son mutuamente excluyentes**: enviar ambos a la vez se rechaza con error de validación. El motivo: el motor de consulta emitiría dos condiciones de carpeta contradictorias y devolvería **cero filas siempre**, indistinguible de "no hay coincidencias".
- **Exclusión por defecto de papelera y spam**: si no se indica ni `box` ni `box_not_in`, la bandeja excluye `TRASH` y `SPAM` automáticamente. Para incluirlos, hay que enviar `box_not_in: []` ("no excluir nada").
- **Exclusión permanente de los borrados (`DELETED`)**: el estado interno `DELETED` (correo eliminado de forma definitiva tras vaciarlo) se excluye **siempre**, incluso con `box_not_in: []`. No es un valor de carpeta seleccionable (no está entre los admitidos de `box`/`box_not_in`), así que el usuario no puede pedirlo; se oculta en todas las ramas de filtro. A diferencia de la de papelera/spam, esta exclusión **no** se puede desactivar.
- **Los criterios de filtro también se cruzan (AND) con la lupa de texto libre** si el usuario escribe algo en ella.

---

## 3. Topes del listado de correos (al abrir la bandeja)

Comparte el mismo motor de listado y la misma lupa que los buzones normales; por eso estos topes coinciden con los de [lupa.md](../features/lupa.md).

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Mínimo de caracteres para la lupa | **2** | Frontend y backend | Por debajo de 2 no se lanza búsqueda; el backend rechaza un `q` de 1 carácter. |
| Longitud máxima del término de lupa | **200 caracteres** | Backend | Rechazado en el borde de la API antes de tokenizar. |
| Debounce de la lupa | **300 ms** | Frontend | Pausa al teclear antes de lanzar la petición. |
| Máximo de palabras (tokens) por búsqueda | **10** | Backend | A partir de la 11.ª palabra el resto se descarta **silenciosamente**. |
| Conversaciones por **página** | **50** | Backend (`limit` por defecto) = tamaño de página del frontend | La bandeja **agrupa por conversación siempre** y se **pagina** igual que el listado general: 50 **hilos distintos** por página (ya deduplicados y colapsados por hilo), el más reciente primero, sin partir una conversación entre páginas. El total exacto (hilos distintos) viaja aparte y alimenta el indicador "X–Y de Z". Reglas de agregación de cada fila-hilo en [conversaciones.md](conversaciones.md); mismo tamaño de página que el listado — ver [listado-de-correos.md](listado-de-correos.md). |
| Tope técnico del parámetro de límite | **500** | Backend | El endpoint acepta `limit` hasta 500, pero el frontend siempre usa el tamaño de página (50) — no expone forma de subirlo. |
| Frescura de la caché de la vista | **0 s (siempre refresca)** | Frontend | Al reabrir la bandeja, vuelve a pedir la lista al instante en vez de servir caché. El resto de listados usan 30 s; aquí se anula a propósito porque son vistas curadas y sensibles al tiempo. |

---

## 4. Validaciones que devuelven error en vez de "tragar" el fallo

Estas entradas se rechazan **explícitamente** (error de validación en el borde de la API) en lugar de aceptarse y producir un resultado engañoso:

| Entrada rechazada | Por qué no se acepta en silencio |
|-------------------|----------------------------------|
| Clave de filtro desconocida o retirada (p. ej. `from_domain`, o una errata) | Un filtro ignorado en silencio mostraría más correos de los que el usuario cree haber pedido. La lista de filtros es cerrada (`extra="forbid"`). |
| Clave de nivel superior desconocida (p. ej. `accountIds` en camelCase) | Una clave mal escrita se descartaría y persistiría una bandeja "válida pero vacía". |
| `subject_contains: ""` o `from_email: ""` | Una cadena vacía se traduciría a "contiene cualquier cosa" (volcado de todo el buzón) indistinguible de "sin filtro". Para quitar un filtro, se omite la clave. |
| Nombre solo con espacios | Se recortaría a vacío y persistiría un nombre en blanco. |
| `box` y `box_not_in` a la vez | Produciría una bandeja perpetuamente vacía (ver § 2). |
| Cuenta no poseída por el usuario | Defensa de seguridad: evitaría agregar cuentas ajenas. Se devuelve "cuenta no encontrada" (404), **nunca** "prohibido" (403), para no filtrar qué cuentas existen. |
| Bandeja ficticia de otro usuario (leer/editar/borrar) | Se devuelve "bandeja no encontrada" (404), nunca 403, para no confirmar su existencia probando identificadores. |

---

## 5. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Ejemplo | Por qué |
|------------|---------|---------|
| **El listado importa metadata por sí mismo** | La consulta que pinta la tabla no va al proveedor; lee solo de la BD local | El **listado** solo filtra lo ya sincronizado. Lo que sí ocurre es que **al abrir** la vista se sincronizan antes las cuentas implicadas (paso orquestado por el frontend, ver § 3 y el gemelo de comportamiento § 6.4); el límite que queda es que la consulta del listado en sí no llama al proveedor — importar metadata lo hace el paso de sincronización de apertura, no el listado. |
| **Autoexpansión de la lista de cuentas** | Conectar una cuenta nueva no la mete sola en bandejas existentes | La lista es una "foto" fija elegida a mano. El modo "todas mis cuentas" autoexpandible se retiró (migración 0032) porque mezclaba correo sensible sin avisar. Para añadir una cuenta hay que editar la bandeja. |
| **Ser una carpeta real** | No se puede "mover un correo a" una bandeja ficticia | Es una vista calculada, no una carpeta en Gmail/Outlook. |
| **Selección múltiple y acciones en bloque** | No hay un "vaciar bandeja ficticia" ni casillas de selección | La vista agrupa por conversación, donde la fila es de solo lectura; las acciones (papelera, leído, favorito) operan **por mensaje** desde el visor de la conversación, sobre los correos reales subyacentes (ver [conversaciones.md](conversaciones.md)). |
| **`from_email` por subcadena o por dominio** | `@empresa.com` no trae todos los de ese dominio | El filtro de remitente es **igualdad exacta** de la dirección completa. El filtro `from_domain` existió y se retiró. Para "contiene", está la lupa de texto libre. |
| **Filtros por fecha, etiquetas o presencia de adjuntos** | No se puede crear "correos con adjunto del último mes" | Solo existen los 6 criterios de § 2. Son ampliaciones previstas pero fuera del MVP. |
| **Ordenación por relevancia / scroll infinito** | No se reordena por remitente/asunto y no hay scroll continuo | Hereda el listado normal: orden por fecha descendente y **navegación por páginas numeradas** (sí hay paginación). Para acotar, se afinan los filtros o la lupa. |
| **Tolerancia a erratas / plurales / sinónimos en la lupa** | `facutra` no encuentra `factura` | La lupa interna es por subcadena literal (ver [lupa.md](../features/lupa.md)). |

---

## 6. Comportamientos de borde con resultado acotado (no son errores)

| Situación | Resultado | Por qué |
|-----------|-----------|---------|
| Cuenta de la bandeja desconectada o sin acceso | La bandeja sigue viva y muestra **solo** las cuentas supervivientes | Se revalida la propiedad en **cada** apertura; las cuentas perdidas se ignoran en silencio (no 404, no 500). |
| Todas las cuentas de la bandeja dejan de ser del usuario | Listado **vacío** (no error) | Sin cuentas válidas no hay nada que mostrar, pero la definición sigue existiendo. |
| El mismo mensaje llega por dos cuentas (misma cuenta de proveedor bajo dos bandejas reales) | Se muestra **una sola fila** | Deduplicación del mensaje en el servidor **antes** de colapsar por hilo y de cortar la página (no después): así una página nunca queda corta por un duplicado y el total es correcto. Orden: deduplicar `provider_message_id` → agrupar por hilo → contar hilos → paginar; el total es el conteo de **hilos distintos** (no de mensajes). Preferencia de la copia superviviente del mensaje: **destinatario (`to_email`) no vacío > nombre de destinatario (`to_name`) no vacío > `received_at` más reciente**. Exclusivo de bandejas ficticias (en un buzón de una sola cuenta no puede ocurrir). |
| Bandeja con `box` = `TRASH`/`SPAM`/`SENT` | Muestra solo esa carpeta, ignorando la exclusión por defecto | `box` explícito tiene prioridad sobre el "excluye papelera/spam por defecto". |
| Correos borrados (`DELETED`) | **Nunca** aparecen en la vista | `DELETED` no es una carpeta solicitable (no está en los valores de `box`/`box_not_in`); se excluye en todas las ramas de filtro, incluida `box_not_in: []`. La exclusión es permanente, no opcional (§ 2). |

---

## 7. Trampa de infraestructura

- **El filtro "asunto contiene" (y la lupa interna) dependen de la extensión `unaccent` de PostgreSQL**, activada por la migración `0020_create_extension_unaccent`. En una base de datos sin esa migración, filtrar por asunto o usar la lupa **falla en tiempo de consulta** (la función `unaccent(text)` no existiría). No es un límite de producto sino un requisito de despliegue.

---

> Una bandeja ficticia llega hasta: **nombre de 1–120 caracteres, al menos 1 cuenta (sin tope superior, lista fija sin autoexpansión), exactamente 6 criterios de filtro combinados con AND (`box`/`box_not_in` excluyentes, papelera y spam fuera por defecto, borrados «DELETED» fuera siempre), `from_email` por igualdad exacta y `subject_contains` por subcadena, más la lupa interna (2 caracteres mínimo, 10 palabras), navegación por páginas de 50 hilos distintos (agrupados por conversación, deduplicados antes de colapsar y paginar) con total exacto y sin scroll infinito, sincronización de las cuentas implicadas al abrir, y refresco inmediato al reabrir** — y deliberadamente su listado no importa metadata por sí mismo (lo hace el paso de sincronización de apertura), no autoexpande cuentas, no filtra por fecha/etiquetas/adjuntos ni ordena por relevancia. El comportamiento completo está en [../features/bandejas-ficticias.md](../features/bandejas-ficticias.md).
