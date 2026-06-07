# Límites de la lupa de búsqueda

Catálogo cuantitativo de **hasta dónde llega** la lupa de búsqueda: topes con cifras exactas y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/lupa.md](../features/lupa.md)**. Aquí solo van los números y los límites.

Todos estos valores están **hardcodeados** y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook); la búsqueda no llama nunca al proveedor, así que ningún límite depende de cuotas externas. Las mismas cifras rigen tanto la lupa sobre una bandeja real como la lupa dentro de una bandeja ficticia (ambos endpoints validan `q` igual y comparten la misma tokenización y la misma consulta SQL).

---

## 1. Topes con cifras exactas

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Mínimo de caracteres para buscar | **2** | Frontend (dispara la búsqueda) **y** backend (valida el parámetro) | Por debajo de 2 caracteres no se lanza ninguna búsqueda y el listado se muestra sin filtrar. El backend rechaza un `q` de 1 carácter en el borde de la API. |
| Longitud máxima del término | **200 caracteres** | Backend (validación del parámetro `q`) | Un término de más de 200 caracteres es rechazado en el borde de la API antes de tokenizar. |
| Debounce (pausa antes de buscar) | **300 ms** | Frontend | Tiempo que el usuario debe dejar de teclear para que salga la petición. Cada nueva pulsación reinicia el contador; solo la última cuenta. |
| Máximo de palabras (tokens) por búsqueda | **10** | Backend (tokenización) | A partir de la 11.ª palabra, el resto se descarta **silenciosamente** (sin error ni aviso). Los espacios sobrantes no cuentan como tokens. |
| Resultados por **página** | **50** | Backend (`limit` por defecto) = tamaño de página del frontend | La búsqueda se **pagina** igual que el listado: cada página muestra 50 correos que casan, los más recientes primero. El total exacto de coincidencias se devuelve aparte (campo `total`) y alimenta el indicador "X–Y de Z". Ver [listado-de-correos.md](listado-de-correos.md). |
| Tope técnico del parámetro de límite | **500** | Backend (validación del listado) | El endpoint acepta un `limit` de hasta 500, pero el frontend de la lupa siempre usa el tamaño de página (50) — no expone forma de subirlo. |

### Notas sobre los topes

- **El mínimo de 2 caracteres está duplicado a propósito** en frontend y backend: el frontend evita disparar peticiones inútiles, y el backend lo valida igualmente como red de seguridad (cualquier cliente que llame directamente a la API con `q` de 1 carácter recibe un error de validación).
- **El tope de 10 palabras es silencioso**: pegar un párrafo entero no da error, simplemente se buscan las 10 primeras palabras. Es una salvaguarda frente a consultas patológicas, no una validación visible para el usuario.
- **El término se mide tras recortar espacios**: un `q` compuesto solo por espacios se trata como "sin búsqueda".

---

## 2. Alcance de la búsqueda (qué campos y qué datos mira)

| Aspecto | Alcance | Motivo |
|---------|---------|--------|
| Campos donde busca | **Asunto, email del remitente, nombre del remitente** | Son los datos de cabecera disponibles en el listado local sin descargar el correo entero. |
| Fuente de datos | **Solo la base de datos local** (PostgreSQL) | No se llama a Gmail ni a Outlook; la búsqueda filtra lo ya sincronizado. Por eso es instantánea y funciona aunque el proveedor esté caído. |
| Bandeja (box) | **Solo el box actual** (principal, enviados, spam o papelera) en una bandeja real; en una bandeja ficticia, el/los box que definan sus criterios guardados | La lupa hereda el contexto del listado; para buscar en otra bandeja real hay que cambiar de buzón. |
| Cuentas | **Las del modo de vista actual** (todas en vista unificada, una en vista de cuenta); en una bandeja ficticia, **todas las cuentas de la bandeja** (pueden abarcar varias bandejas reales) | Hereda el alcance del listado que se está viendo, sin configuración extra. |
| Dentro de una bandeja ficticia | La lupa se combina (AND) con los **criterios guardados** de la bandeja | Los criterios fijos acotan el universo; la lupa filtra encima por texto libre. Ambos endpoints comparten la misma consulta SQL. |

---

## 3. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Ejemplo | Por qué |
|------------|---------|---------|
| **Tolerancia a erratas (typos)** | `facutra` no encuentra `factura` | La búsqueda es por subcadena literal; no hay distancia de edición ni corrección difusa. Se descartó `pg_trgm` para el MVP. |
| **Stemming / plurales / raíces** | `facturas` no encuentra `factura`; `comprar` no encuentra `compra` | No hay análisis lingüístico; se busca la secuencia exacta de letras, no la raíz. Se descartó `tsvector`/full-text search para el MVP. |
| **Sinónimos / significado** | `pedido` no encuentra correos sobre `compra` u `orden` | La lupa no entiende el significado; solo busca las letras escritas. |
| **Ordenación por relevancia** | Un correo con 5 coincidencias no sale antes que uno con 1 | El único criterio de orden es la fecha de recepción (descendente). Implementar ranking requeriría full-text search, fuera de scope MVP. |
| **Scroll infinito** | Los resultados no se cargan en scroll continuo | Los resultados se recorren por **páginas numeradas** (sí hay paginación), no por scroll infinito. Es el modelo de Gmail/Outlook web. |
| **Buscar más allá de lo sincronizado** | Un correo antiguo que la app no bajó no aparece por mucho que se pagine | La lupa filtra solo la copia local; paginar no baja más histórico del proveedor. El total "Z" es el de lo sincronizado, no el del buzón en vivo. |
| **Búsqueda en el cuerpo del correo** | El texto del mensaje no se busca | El cuerpo no se sincroniza en el listado (se baja al abrir cada correo); buscar en él obligaría a descargar todos los correos. |
| **Búsqueda en destinatarios / CC / BCC** | Un correo "Para: ana@..." no aparece al buscar `ana` salvo que `ana` esté en asunto/remitente | Solo se indexan los tres campos de cabecera del remitente y el asunto. |
| **Comodines (`%`, `_`, `\`)** | `50%` busca literalmente "50%", no actúa como comodín | Esos caracteres se escapan y se tratan como texto literal; el usuario no puede inyectar patrones ni romper la consulta. |
| **Búsqueda transversal entre bandejas reales** | No busca a la vez en principal + papelera | El alcance de la lupa sobre una bandeja real está acotado al box actual (ver sección 2). La excepción es una bandeja ficticia, que sí puede agregar cuentas de varias bandejas reales: ahí la lupa busca en todo ese conjunto, pero el alcance lo define la bandeja ficticia, no la lupa. |

---

## 4. Trampa de infraestructura

- **La insensibilidad a tildes depende de la extensión `unaccent` de PostgreSQL**, activada por la migración `0020_create_extension_unaccent`. Una base de datos a la que no se le haya aplicado esa migración hace fallar **toda** búsqueda en tiempo de consulta (la función `unaccent(text)` no existiría). No es un límite "de producto" sino un requisito de despliegue que conviene tener presente al provisionar un entorno nuevo.

---

> La lupa llega hasta: **2 caracteres mínimo, 300 ms de debounce, 10 palabras como máximo, resultados paginados de 50 por página (sin scroll infinito) con total exacto de coincidencias, solo sobre asunto/email/nombre del remitente del box y las cuentas actuales en la base de datos local** — y deliberadamente no ofrece tolerancia a erratas, plurales, sinónimos ni ordenación por relevancia. El comportamiento completo está en [../features/lupa.md](../features/lupa.md).
