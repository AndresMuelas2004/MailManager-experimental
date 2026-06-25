# Límites de la lupa de búsqueda

Catálogo cuantitativo de **hasta dónde llega** la lupa de búsqueda: topes con cifras exactas, el **catálogo cerrado de operadores** (con los valores que admite cada uno) y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/lupa.md](../features/lupa.md)**. Aquí solo van los números y los límites.

Todos estos valores están **hardcodeados** y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook); la búsqueda no llama nunca al proveedor, así que ningún límite depende de cuotas externas. Las mismas cifras rigen tanto la lupa sobre una bandeja real como la lupa dentro de una bandeja ficticia (ambos endpoints validan `q` igual y comparten la misma tokenización, el mismo parseo de operadores y la misma consulta SQL).

---

## 1. Topes con cifras exactas

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Mínimo de caracteres para buscar | **2** | Frontend (dispara la búsqueda) **y** backend (valida el parámetro) | Por debajo de 2 caracteres no se lanza ninguna búsqueda y el listado se muestra sin filtrar. El backend rechaza un `q` de 1 carácter en el borde de la API. |
| Longitud máxima del término | **200 caracteres** | Backend (validación del parámetro `q`) | Un término de más de 200 caracteres es rechazado en el borde de la API antes de tokenizar. |
| Debounce (pausa antes de buscar) | **300 ms** | Frontend | Tiempo que el usuario debe dejar de teclear para que salga la petición. Cada nueva pulsación reinicia el contador; solo la última cuenta. |
| Máximo de palabras de **texto libre** (tokens) por búsqueda | **10** | Backend (tokenización) | A partir de la 11.ª palabra de texto libre, el resto se descarta **silenciosamente** (sin error ni aviso). Los espacios sobrantes no cuentan como tokens. Los operadores no consumen este cupo: tienen el suyo (fila siguiente). |
| Máximo de **operadores** por búsqueda | **10** | Backend (parseo de operadores) | Cupo **independiente** del de texto libre. A partir del 11.º operador reconocido, el resto se descarta en silencio. En la práctica nadie encadena tantos. |
| Resultados por **página** | **50** | Backend (`limit` por defecto) = tamaño de página del frontend | La búsqueda se **pagina** igual que el listado: cada página muestra 50 correos que casan, los más recientes primero. El total exacto de coincidencias se devuelve aparte (campo `total`) y alimenta el indicador "X–Y de Z". Ver [listado-de-correos.md](listado-de-correos.md). |
| Tope técnico del parámetro de límite | **500** | Backend (validación del listado) | El endpoint acepta un `limit` de hasta 500, pero el frontend de la lupa siempre usa el tamaño de página (50) — no expone forma de subirlo. |

### Notas sobre los topes

- **El mínimo de 2 caracteres está duplicado a propósito** en frontend y backend: el frontend evita disparar peticiones inútiles, y el backend lo valida igualmente como red de seguridad (cualquier cliente que llame directamente a la API con `q` de 1 carácter recibe un error de validación).
- **El tope de 10 palabras es silencioso**: pegar un párrafo entero no da error, simplemente se buscan las 10 primeras palabras. Es una salvaguarda frente a consultas patológicas, no una validación visible para el usuario.
- **Hay dos cupos de 10 separados**: 10 palabras de texto libre **y** 10 operadores. Son independientes — una búsqueda con 10 operadores y 10 palabras de texto libre las usa todas; pasarse en uno no consume del otro.
- **El término se mide tras recortar espacios**: un `q` compuesto solo por espacios se trata como "sin búsqueda".
- **El límite de 200 caracteres aplica al campo entero** (texto libre + operadores juntos), porque es la cadena `q` completa la que se valida en el borde de la API antes de parsear.

---

## 1-bis. Catálogo cerrado de operadores

El conjunto de operadores es **fijo**: cualquier `clave:` que no esté en esta tabla se trata como texto literal. Los operadores son **solo en inglés** (decisión cerrada). Un operador reconocido con un **valor no admitido** se ignora (no aporta filtro); el comportamiento tolerante completo está en [../features/lupa.md](../features/lupa.md).

| Operador | Valor que admite | Campo / efecto | Notas |
|----------|------------------|----------------|-------|
| `from:` | texto libre (admite comillas) | El **remitente** (email o nombre) contiene el texto | Subcadena, sin tildes ni mayúsculas, igual que el texto libre. |
| `to:` | texto libre (admite comillas) | El **destinatario "Para"** contiene el texto | Casa contra el **email y el nombre** del único "Para" guardado (ver sección 2). Es el único operador que mira el destinatario. |
| `subject:` | texto libre (admite comillas) | El **asunto** contiene el texto | Subcadena, sin tildes ni mayúsculas. |
| `has:` | `attachment`, `attachments` | Correos **marcados** con adjunto | Cualquier otro valor (`has:drive`) se ignora. La marca de "tiene adjunto" es perezosa (solo se conoce tras abrir el correo): ver [../features/lupa.md](../features/lupa.md) y [../features/adjuntos.md](../features/adjuntos.md). |
| `before:` | fecha `AAAA/MM/DD` o `AAAA-MM-DD` | Recibidos **antes** de esa fecha (excluye el día) | Otros formatos o fechas inválidas se ignoran (ver sección 1-ter). |
| `after:` | fecha `AAAA/MM/DD` o `AAAA-MM-DD` | Recibidos **desde** esa fecha (incluye el día) | Idem. |
| `is:` | `read`, `unread`, `favorite`, `starred` | Leídos / no leídos / favoritos | `starred` es **alias** de `favorite`. Cualquier otro valor (`is:importante`) se ignora. |
| `in:` | `inbox`, `allmail`, `sent`, `archive`, `spam`, `trash` | Restringe la **bandeja** de búsqueda | `inbox` y `allmail` apuntan al mismo conjunto (la bandeja de entrada real, `ALL_MAIL`). `archive` apunta a los **archivados** (`ARCHIVE`). Cualquier otro valor (`in:archivados`, en español) se ignora. Aplicación sensible a la vista (sección 2). |

### Reglas del catálogo

- **Combinación**: todos los operadores y el texto libre se unen con **AND**. No hay OR, ni paréntesis, ni negación (ver "qué NO soporta").
- **`in:` — el último válido gana**: si se escriben varios `in:` reconocidos, manda el último. Los `in:` con valor no admitido se ignoran (no cuentan).
- **`in:trash` apunta a la papelera real (`TRASH`), nunca al estado interno "papelera vaciada"** (`DELETED`): ese estado no es seleccionable desde la lupa por diseño.
- **`in:archive` rescata los archivados dentro de una bandeja ficticia**: `ARCHIVE` queda fuera de una ficticia por defecto (igual que `TRASH`/`SPAM`), pero `in:archive` es el **único** box excluido por defecto que `in:` puede recuperar dentro de una ficticia; `in:trash` / `in:spam` sobre una ficticia por defecto siguen dando vacío (ver [bandejas-ficticias.md](bandejas-ficticias.md)). En una bandeja normal, `in:archive` simplemente cambia la búsqueda a Archivados como cualquier otro `in:`.
- **Comillas**: `from:`, `to:` y `subject:` (y el texto libre) admiten comillas dobles para frases con espacios; los demás operadores toman un único token.

---

## 1-ter. Fechas aceptadas en `before:` / `after:`

| Aspecto | Valor exacto | Nota |
|---------|--------------|------|
| Formatos aceptados | **`AAAA/MM/DD`** y **`AAAA-MM-DD`** | Año de 4 cifras, mes y día de 2 cifras (rellenos con cero). No se aceptan otros separadores ni anchuras variables (`2026/1/1` no vale). |
| Zona horaria de interpretación | **Europe/Madrid** (CET/CEST, con cambio de hora real) | La medianoche se calcula en hora española, no en UTC ni con un desfase fijo. |
| `after:D` | `recibido >= medianoche de D` | **Incluye** el día D completo. |
| `before:D` | `recibido < medianoche de D` | **Excluye** el día D. |
| Fecha mal formada o imposible | **se ignora** ese filtro | `before:ayer`, `before:31/13/2026`, `after:2026/02/30` → el filtro de fecha se descarta en silencio; el resto de la búsqueda sigue. |

---

## 2. Alcance de la búsqueda (qué campos y qué datos mira)

| Aspecto | Alcance | Motivo |
|---------|---------|--------|
| Campos donde busca el **texto libre** | **Asunto, email del remitente, nombre del remitente** | Son los datos de cabecera disponibles en el listado local sin descargar el correo entero. El texto libre NO mira el destinatario. |
| Campos que añaden los **operadores** | `to:` → email y nombre del **"Para"** (el único guardado); `subject:` → asunto; `from:` → remitente; `is:`/`has:`/`before:`/`after:` → estado (leído, favorito, adjunto, fecha) | Cada operador consulta su columna; `to:` es el único acceso al destinatario, y solo al primer "Para" que la app sincroniza. |
| Fuente de datos | **Solo la base de datos local** (PostgreSQL) | No se llama a Gmail ni a Outlook; la búsqueda filtra lo ya sincronizado. Por eso es instantánea y funciona aunque el proveedor esté caído. |
| Bandeja (box) | **Solo el box actual** (principal, enviados, archivados, spam o papelera) en una bandeja real; en una bandeja ficticia, el/los box que definan sus criterios guardados. El operador `in:` puede **cambiar** ese box (en una bandeja real/unificada) o **intersecarlo** (en una ficticia, donde `in:archive` además rescata los archivados excluidos por defecto) | La lupa hereda el contexto del listado; `in:` permite redirigir/afinar la bandeja sin cambiar de vista (comportamiento por vista en [../features/lupa.md](../features/lupa.md)). |
| Cuentas | **Las del modo de vista actual** (todas en vista unificada, una en vista de cuenta); en una bandeja ficticia, **todas las cuentas de la bandeja** (pueden abarcar varias bandejas reales) | Hereda el alcance del listado que se está viendo, sin configuración extra. |
| Dentro de una bandeja ficticia | La lupa (texto libre **y** operadores) se combina (AND) con los **criterios guardados** de la bandeja; un `in:` que pida una bandeja excluida por la ficticia da resultado vacío — **salvo `in:archive`**, que rescata los archivados (el único box excluido por defecto recuperable así) | Los criterios fijos acotan el universo; la lupa filtra encima. Ambos endpoints comparten la misma consulta SQL. Los operadores son búsqueda puntual: no se guardan como criterios de la ficticia. |

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
| **Búsqueda en el cuerpo del correo** | El texto del mensaje no se busca, ni siquiera con operadores | El cuerpo no se sincroniza en el listado (se baja al abrir cada correo); buscar en él obligaría a descargar todos los correos. |
| **CC / BCC y la lista completa de destinatarios** | `to:ana` no encuentra un correo donde Ana iba en CC, ni un correo con varios "Para" si Ana no es el primero | La app solo guarda el **primer** destinatario "Para"; CC/BCC y el resto de "Para" no se sincronizan en la metadata. `to:` casa solo contra ese único "Para". |
| **Tamaño del correo (`larger:` / `smaller:`)** | `larger:5M` no filtra por peso | El tamaño del mensaje no se sincroniza hoy; haría falta un dato que la app no guarda. Fuera de scope MVP. |
| **Operadores lógicos avanzados (OR, paréntesis, negación, etiquetas)** | No hay `OR`, ni `(a OR b)`, ni `-from:x` (excluir), ni `label:`/`category:` | El MVP solo combina con **AND**. Negación, OR y agrupación son una fase posterior. |
| **Guardar los operadores como criterios de una bandeja ficticia** | Escribir `from:banco is:unread` en la lupa de una ficticia no crea un filtro guardado | Los operadores son búsqueda **puntual** en la lupa; las bandejas ficticias mantienen su propio juego de criterios guardados (ver [bandejas-ficticias.md](../features/bandejas-ficticias.md)). |
| **Panel de "Búsqueda avanzada" con campos/chips** | No hay un formulario que componga la cadena por el usuario | Sería una fase 2 que reutilizaría toda la lógica de parseo de detrás. Hoy solo existe la chuleta de ayuda ("?"). |
| **Comodines (`%`, `_`, `\`)** | `50%` busca literalmente "50%", no actúa como comodín | Esos caracteres se escapan y se tratan como texto literal; el usuario no puede inyectar patrones ni romper la consulta. |
| **Búsqueda transversal entre bandejas reales sin `in:`** | El texto libre no busca a la vez en principal + papelera | El alcance del texto libre sobre una bandeja real está acotado al box actual (ver sección 2). `in:` redirige a **una** bandeja, no busca en varias a la vez. La excepción es una bandeja ficticia, que sí puede agregar cuentas de varias bandejas reales: ahí la lupa busca en todo ese conjunto, pero el alcance lo define la bandeja ficticia, no la lupa. |

---

## 4. Trampas de infraestructura

- **La insensibilidad a tildes depende de la extensión `unaccent` de PostgreSQL**, activada por la migración `0020_create_extension_unaccent`. Una base de datos a la que no se le haya aplicado esa migración hace fallar **toda** búsqueda en tiempo de consulta (la función `unaccent(text)` no existiría). No es un límite "de producto" sino un requisito de despliegue que conviene tener presente al provisionar un entorno nuevo.
- **Las fechas en hora de Madrid dependen de la base de datos de zonas horaria del sistema.** El backend resuelve `Europe/Madrid` vía `zoneinfo`, que en imágenes base "slim" (y en Windows) no trae la base de datos de zonas; por eso el despliegue incluye el paquete `tzdata`. La zona se resuelve **al importar el módulo** de servicios, así que sin `tzdata` el fallo no se limita a `before:`/`after:`: el módulo no carga y la API no arranca. Es un requisito de despliegue, no un límite de producto.

---

> La lupa llega hasta: **2 caracteres mínimo, 300 ms de debounce, hasta 10 palabras de texto libre + hasta 10 operadores (cupos separados), un campo de 200 caracteres como máximo, resultados paginados de 50 por página (sin scroll infinito) con total exacto de coincidencias**, sobre la base de datos local; el texto libre mira asunto/email/nombre del remitente, y un **catálogo cerrado de operadores** (`from: to: subject: has:attachment before:/after: is:read|unread|favorite|starred in:inbox|sent|archive|spam|trash`) combinados con AND añade destinatario "Para", estado, fecha (hora de Madrid) y bandeja — y deliberadamente no ofrece tolerancia a erratas, plurales, sinónimos, ordenación por relevancia, OR/negación/paréntesis, tamaño, cuerpo ni CC/BCC. El comportamiento completo está en [../features/lupa.md](../features/lupa.md).
