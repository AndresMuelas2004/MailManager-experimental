# Características de la lupa de búsqueda — comportamiento (MVP)

Este documento describe **qué hace** la lupa cuando un usuario la usa y **qué experimenta** delante de la app, sin entrar en cómo está cableado el código. Es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la pantalla.

La lupa empezó siendo un buscador de **texto literal** sobre asunto y remitente. Ahora, además de ese texto libre (que **funciona exactamente igual que siempre**), entiende **operadores al estilo de Gmail** (`from:`, `to:`, `subject:`, `has:attachment`, `before:`, `after:`, `is:`, `in:`) dentro del **mismo campo de búsqueda de siempre** — no hay pantalla nueva ni botón nuevo. Igual que en Gmail, **más términos = menos resultados**.

Los topes concretos (mínimo de caracteres, debounce, número de tokens y de operadores, resultados por carga), el **catálogo cerrado de operadores** con los valores que admite cada uno, y la lista exhaustiva de "qué NO soporta" viven en un documento aparte: **[../limits/lupa.md](../limits/lupa.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están las cifras exactas.

---

## 1. Qué busca la lupa y dónde

La lupa es un **filtro sobre lo que ya está en la app**, no un buscador que sale a Internet:

- Filtra los correos que **ya están sincronizados** en la base de datos local de MailManager. **No llama a Gmail ni a Outlook** — solo mira lo que ya tenemos guardado. Por eso es instantánea y funciona igual aunque el proveedor esté caído.
- El **texto libre** busca en tres datos de cada correo: el **asunto**, el **email del remitente** y el **nombre del remitente**. Para que un correo aparezca basta con que **una sola** de esas tres piezas contenga lo que el usuario escribió.
- Los **operadores** amplían el conjunto de campos consultables más allá de esos tres: `to:` busca en el destinatario "Para", `is:`/`has:`/`in:`/`before:`/`after:` filtran por estado (leído, favorito, adjunto, fecha, bandeja). El detalle de cada operador está en la sección 2-bis.

Lo que la lupa **no** mira en ningún caso: el cuerpo del correo, CC/BCC, la lista completa de destinatarios, ni el contenido de los adjuntos. Es una búsqueda de "cabecera", no de "texto completo". La razón es directa: el cuerpo no se sincroniza durante el listado (se baja solo al abrir cada correo), así que filtrar por cuerpo obligaría a descargar todos los correos primero — justo lo contrario de una búsqueda local rápida. (El destinatario "Para" sí es consultable, pero **solo con el operador `to:`** y solo contra el primer destinatario que la app guarda — ver sección 2-bis y [../limits/lupa.md](../limits/lupa.md).)

---

## 2. Qué grado de "inteligencia" tiene el texto libre

Esta sección describe cómo casa el **texto libre** (lo que no es un operador reconocido). Los operadores se describen en las secciones 2-bis a 2-quáter.

La búsqueda de texto libre del MVP es **literal con dos relajaciones**: el texto escrito tiene que aparecer tal cual en alguno de los tres campos (asunto, email o nombre del remitente), pero ignorando **mayúsculas/minúsculas** y **tildes/acentos**.

### Lo que sí encuentra

- **Coincidencia en cualquier posición** de la cadena. Si el usuario escribe `fact`, encuentra correos cuyo asunto sea `Factura`, `Refactura 2024` o `Esto es una factura`. No hace falta que la palabra empiece por lo escrito; la coincidencia es de subcadena.
- **Sin distinguir mayúsculas/minúsculas**. Escribir `JUAN`, `juan` o `Juan` da exactamente los mismos resultados.
- **Sin distinguir tildes/acentos**. Escribir `jose` encuentra `José`; `accion` encuentra `acción`; `maria` encuentra `María`. Y al revés: escribir `José` también encuentra `jose`. Esta normalización es imprescindible para usar la app con normalidad en español, donde la gente teclea sin tildes constantemente.

> La insensibilidad a tildes se apoya en una extensión de PostgreSQL (`unaccent`) que se activa con una migración dedicada. Es un detalle de infraestructura con una trampa silenciosa asociada que se documenta en [../limits/lupa.md](../limits/lupa.md): una base de datos a la que no se le haya aplicado esa migración hace fallar toda búsqueda.

### Lo que NO encuentra

La lupa **no** corrige erratas, **no** entiende plurales ni raíces de palabra, y **no** conoce sinónimos. Son limitaciones aceptadas a propósito para el MVP; la lista completa con un ejemplo de cada una está en [../limits/lupa.md](../limits/lupa.md). En una frase: si lo escribes mal o usas otra palabra distinta a la que aparece en el correo, no aparece.

Los caracteres especiales de los patrones de búsqueda (`%`, `_`, `\`) se tratan como **texto literal**, no como comodines. Buscar `50%` busca correos que contengan literalmente "50%", no "cualquier cosa que empiece por 50". El usuario no tiene forma de inyectar comodines ni de romper la consulta — todo lo que escribe se interpreta al pie de la letra.

---

## 2-bis. Los operadores estilo Gmail

Dentro del **mismo campo**, el usuario puede acotar con operadores `clave:valor`. El conjunto es **cerrado** y los operadores son **solo en inglés** (decisión cerrada, igual que Gmail); el catálogo completo con los valores que admite cada uno está en [../limits/lupa.md](../limits/lupa.md). En resumen, qué hace cada uno:

- `from:` — el **remitente** (nombre o dirección) contiene el texto. `from:linkedin`.
- `to:` — el **destinatario principal** ("Para") contiene el texto. `to:ana`. Es el **único** operador que mira el destinatario, y solo casa contra el primer "Para" que la app guarda (ver la limitación heredada más abajo).
- `subject:` — el **asunto** contiene el texto. `subject:factura`.
- `has:attachment` — correos **marcados** como que llevan adjunto. `has:attachment`.
- `before:` / `after:` — recibidos **antes** / **desde** una fecha. `before:2026/01/01`, `after:2025/12/01` (ver sección 2-ter).
- `is:unread` / `is:read` — **no leídos** / **leídos**. `is:unread`.
- `is:favorite` (alias `is:starred`) — **favoritos**. `is:favorite`.
- `in:` — restringe la **bandeja** en la que se busca: `in:inbox`, `in:sent`, `in:archive`, `in:spam`, `in:trash` (ver sección 2-quáter, su comportamiento depende de dónde estés). `in:archive` busca entre los **archivados** (ver [acciones-sobre-correos.md](acciones-sobre-correos.md)).

### Cómo se combinan

- **Todo se combina con "Y" (AND)**: operadores y texto libre juntos. `from:linkedin oferta` = (remitente contiene "linkedin") **Y** (texto libre "oferta" en asunto o remitente). `is:unread asunto importante` = no leídos que además contienen "asunto" e "importante" en asunto/remitente. Cada operador y cada palabra de texto libre **estrechan** el resultado.
- **El texto libre sigue intacto**: lo que no es un operador reconocido se busca como subcadena en asunto y remitente, sin tildes ni mayúsculas que importen, con las mismas reglas multi-palabra de la sección 4.
- **Frases con comillas**: para buscar varias palabras como una frase contigua se usan comillas dobles. `subject:"acción requerida"` busca ese texto seguido en el asunto; `to:"ana garcía"` el destinatario "Para" contiene esa frase. Las comillas también valen para el texto libre: `"acción requerida"` exige esas dos palabras seguidas en asunto o remitente (en vez de cada una por su cuenta).
- **Se puede buscar solo con operadores**, sin texto libre: `is:unread`, `from:banco`, `has:attachment` o `before:2026/01/01` son búsquedas válidas por sí solas. (Eso sí, siguen sujetas al mínimo de caracteres del campo completo — ver sección 3.)

### Comportamiento tolerante (no rompe la experiencia)

La lupa **nunca da error** por lo que se escriba en el campo; ante algo que no entiende, lo ignora o lo trata como texto, pero sigue buscando con el resto:

- **Operador desconocido** (`foo:bar`): se trata como **texto literal** y se busca tal cual (incluidos los dos puntos), como una palabra más de texto libre.
- **Fecha mal escrita** (`before:ayer`, `before:31/13/2026`): ese filtro de fecha **se ignora en silencio**; el resto de la búsqueda sigue. Solo se aceptan los formatos de fecha de la sección 2-ter.
- **Valor no soportado** en `is:` / `has:` / `in:` (`is:importante`, `has:drive`, `in:archivados` —el valor válido es el inglés `in:archive`): ese operador **se ignora** (no aporta filtro), el resto sigue.
- **Criterios contradictorios** dan **cero resultados** de forma natural, sin aviso especial: `is:read is:unread`, un rango de fechas imposible (`after:2026/02/01 before:2026/01/01`), o pedir `in:` de una bandeja que la bandeja ficticia en la que estás excluye. Se muestra el mensaje habitual de "No se encontraron correos para tu búsqueda".

> La razón de esta tolerancia es de experiencia: la lupa se dispara mientras el usuario teclea (ver sección 3), así que abortar con un error a mitad de escritura sería molesto y constante. Es preferible ignorar lo que no se entiende y devolver los resultados de lo que sí.

---

## 2-ter. Fechas: formato y zona horaria

`before:` y `after:` interpretan la fecha en **hora de Madrid** (Europe/Madrid). Así, `before:2026/01/01` significa "recibidos antes de la medianoche del 1 de enero de 2026 hora española", que es lo que el usuario espera. La zona es la real (con su cambio de horario verano/invierno), no un desfase fijo, para que el corte de medianoche caiga donde el usuario cree durante todo el año.

- `after:D` **incluye** el día D completo (desde su medianoche).
- `before:D` **excluye** el día D (corta en su medianoche).
- Combinándolos se acota un rango: `after:2026/01/01 before:2026/02/01` = todo enero de 2026.

Los formatos de fecha aceptados (`AAAA/MM/DD` y `AAAA-MM-DD`) y qué cuenta como "fecha mal escrita" están en [../limits/lupa.md](../limits/lupa.md).

---

## 2-quáter. El operador de bandeja `in:` y dónde estás

`in:` decide **en qué bandeja se busca**, y se comporta de forma distinta según la vista en la que esté el usuario:

- **En una bandeja normal o en la vista unificada** (Recibidos, Enviados, Archivados, Spam, Papelera): `in:` **cambia** la bandeja en la que buscas, ganando a la bandeja en la que estabas. Estando en "Recibidos", `in:sent oferta` busca "oferta" en Enviados; `in:archive` busca entre los archivados. Los resultados se muestran con las **columnas correctas** ("De" / "Para") para la bandeja pedida, no para la de origen.
- **En Favoritos**: sigues viendo **solo favoritos**; `in:sent` los restringe a los favoritos que están en Enviados. Es decir, `in:` afina dentro de los favoritos, no los sustituye.
- **Dentro de una bandeja ficticia**: `in:` **se suma** (AND) a lo que la bandeja ficticia ya define. Si la ficticia incluye una bandeja, `in:` puede afinar dentro de ella; si pides una bandeja que la ficticia **excluye** (p. ej. la ficticia es "todo menos papelera" y escribes `in:trash`), el resultado es **vacío**, de forma natural. **`in:archive` es la excepción**: aunque los archivados quedan fuera de una bandeja ficticia por defecto, escribir `in:archive` los **rescata** dentro de la ficticia (es el único box excluido por defecto que `in:` puede recuperar — ver [bandejas-ficticias.md](bandejas-ficticias.md)).

> Las columnas "De" / "Para" de la tabla siguen a la **bandeja efectiva**: cuando un `in:` válido cambia la bandeja, todos los correos devueltos pertenecen a esa bandeja, así que la tabla repinta sus columnas para tener sentido (en Enviados importa "Para", en Recibidos importa "De"). Es un ajuste **solo visual**: el filtrado real lo aplica el servidor a partir de lo que el usuario escribió.

---

## 3. Cuándo se dispara la búsqueda

La lupa **no lanza una búsqueda por cada letra** que el usuario teclea. Sería un derroche de tráfico y crearía una experiencia "pegajosa". En su lugar, espera a que se cumplan tres condiciones a la vez: que el texto haya cambiado, que el usuario haga una **breve pausa** al teclear (debounce), y que el texto tenga un **mínimo de caracteres**. Los valores exactos de ese debounce y de ese mínimo están en [../limits/lupa.md](../limits/lupa.md).

Si falla cualquiera de las tres, **no se hace ninguna búsqueda**.

### Paso a paso de lo que ve el usuario

- **Usuario escribe muy poco** (por debajo del mínimo): no pasa nada. Ve el listado completo del buzón sin filtrar.
- **Usuario alcanza el mínimo de caracteres**: arranca un cronómetro interno (el debounce).
- **Usuario sigue tecleando antes de que venza el cronómetro**: el cronómetro se cancela y arranca uno nuevo desde cero. La búsqueda anterior nunca llega a salir. Solo la última pulsación cuenta.
- **Usuario hace una pausa**: ahí sí, se lanza **una sola** búsqueda con el texto actual y se actualizan los resultados en pantalla.
- **Usuario borra hasta dejar menos del mínimo** (o vacía el input por completo, con la "X" del campo): la lupa **deja de filtrar** y vuelve a mostrar el listado completo del buzón en el que estaba. No se queda esperando — limpia el filtro de forma activa y el listado vuelve a su estado normal.
- **Usuario hace clic en un correo de los resultados**: abre ese correo normalmente. La lupa no interfiere.
- **Usuario vuelve a teclear**: se reinicia el ciclo desde el principio.

### El texto buscado vive en la URL

El término de búsqueda se guarda en la dirección de la página (como `?q=...`). Esto tiene dos consecuencias visibles para el usuario:

- Si **recarga la página** con una búsqueda activa, la búsqueda se mantiene (el campo aparece relleno y el listado sigue filtrado).
- Puede **copiar y compartir el enlace** de una búsqueda concreta, y quien lo abra verá el mismo filtro aplicado.

El historial del navegador no se llena de entradas por cada letra: la actualización del término reemplaza la entrada actual en lugar de apilar una nueva, así que el botón "atrás" no obliga a deshacer la búsqueda carácter a carácter.

### Por qué un debounce y por qué un mínimo de caracteres

- **El debounce** evita disparar una petición mientras el usuario sigue escribiendo. El valor elegido (ver [../limits/lupa.md](../limits/lupa.md)) es imperceptible — más corto que la pausa natural entre palabras — y coincide con el estándar del sector (Google, Gmail, GitHub, Slack, Notion, Linear). Más corto generaría peticiones inútiles; más largo introduciría un lag perceptible.
- **El mínimo de caracteres** evita que una sola letra (`a`, `e`, `s`...) coincida con prácticamente todos los correos del buzón y vuelva la búsqueda inútil. Con el mínimo elegido ya hay suficiente especificidad para que los resultados sirvan.

---

## 4. Búsqueda con varias palabras

Si el usuario escribe **más de una palabra** separada por espacios (p. ej. `juan factura`), la lupa trata cada palabra como un **token independiente** y exige que **cada token aparezca en alguna de las tres piezas** del correo (no necesariamente en la misma). Es exactamente lo que hacen Google o Gmail: añadir palabras **estrecha** los resultados, no los amplía.

### Ejemplos con `juan factura`

- Encuentra un correo cuyo asunto es `"Factura de Juan"` (los dos tokens caen en el asunto).
- Encuentra un correo cuyo asunto es `"Juan envió la factura"`.
- Encuentra un correo cuyo remitente se llama `"Juan"` y cuyo asunto es `"factura mensual"` — el token `juan` casa con el nombre del remitente y el token `factura` con el asunto, en campos distintos.
- **No** encuentra un correo cuyo asunto es `"Factura de Pedro"` — falta el token `juan`.
- **No** encuentra un correo cuyo remitente se llama `"Juan"` y cuyo asunto es `"Hola"` — falta el token `factura`.

### El orden no importa

Escribir `juan factura` y `factura juan` da exactamente los mismos resultados. Lo único que cuenta es que **todos los tokens estén presentes** en algún sitio del correo, en cualquier orden.

### Espacios sobrantes

Los espacios de más se ignoran: espacios al principio, al final, o varios seguidos entre palabras no cuentan como tokens vacíos. `  juan   factura  ` se interpreta igual que `juan factura`. Un texto compuesto solo por espacios equivale a no buscar nada.

### Tope de palabras

Si el usuario pega un texto muy largo, solo se consideran las primeras palabras hasta un tope de seguridad (ver [../limits/lupa.md](../limits/lupa.md)). Es una salvaguarda silenciosa frente a consultas patológicas; en la práctica nadie busca correos con tantas palabras. Las palabras que sobran simplemente no se tienen en cuenta, sin error ni aviso.

---

## 5. Alcance de la búsqueda dentro de la app

La lupa **respeta el contexto donde está el usuario**. No busca a lo loco por toda la app — hereda exactamente el alcance del listado que se está mirando.

### Solo en el buzón (box) actual

La lupa filtra dentro del **box** donde el usuario está viendo los correos:

- En la bandeja principal busca solo en la bandeja principal.
- En **Enviados** busca solo en enviados.
- En **Spam**, solo en spam.
- En **Papelera**, solo en la papelera.

Para buscar en otra bandeja, el usuario tiene que **cambiar de buzón** y volver a usar la lupa. Es el comportamiento natural y el mismo que hace Gmail por defecto.

### Multi-cuenta: hereda el modo del listado

MailManager soporta varios correos por mailbox (vista unificada). La lupa hereda el mismo modo en el que se está viendo la bandeja, sin que el usuario tenga que configurar nada:

- **Vista unificada del mailbox** (sin cuenta concreta seleccionada): la lupa busca en **todas las cuentas** del mailbox a la vez.
- **Vista de una cuenta concreta**: la lupa busca **solo en esa cuenta**.

### También dentro de una bandeja ficticia

La misma lupa de texto libre está disponible **dentro de una bandeja ficticia** (virtual mailbox), con exactamente las mismas reglas (mínimo de caracteres, debounce, tokens, normalización de mayúsculas/tildes, campos buscados y persistencia en la URL). Lo único que cambia es el alcance: en lugar de heredarlo de un box, hereda el **conjunto de cuentas de la bandeja ficticia** (que puede abarcar varias bandejas reales a la vez) y los **criterios guardados** de esa bandeja.

La lupa y los criterios guardados **se combinan, no se excluyen**: los criterios fijos de la bandeja ficticia (remitente, asunto que contiene, leído/no leído, etc.) acotan el universo de correos, y encima de ese universo la lupa exige además que todas las palabras escritas aparezcan en el asunto, el email o el nombre del remitente. Es un AND entre "lo que define la bandeja ficticia" y "lo que el usuario teclea en la lupa". El mecanismo de criterios guardados se documenta en [bandejas-ficticias.md](bandejas-ficticias.md); aquí solo importa que la lupa se monta encima de él sin cambiar su propio comportamiento.

---

## 6. Cómo se muestran los resultados

- Los resultados aparecen en la **misma tabla de correos** que el usuario ya estaba viendo. La pantalla no cambia: la lista simplemente se "filtra" mostrando solo los correos que casan. Si no hay ninguno, se muestra un mensaje específico de "No se encontraron correos para tu búsqueda" (distinto del "No hay correos en esta bandeja" que se ve cuando el buzón está realmente vacío).
- Los resultados están **ordenados por fecha de recepción descendente** (los más recientes primero), igual que el listado normal del buzón.
- **No hay ordenación por relevancia**. Un correo donde la palabra buscada aparece cinco veces no sale antes que uno donde aparece una sola vez. Lo único que decide el orden es la fecha. (El porqué de no implementar ranking está en [../limits/lupa.md](../limits/lupa.md).)
- Los resultados se **paginan igual que cualquier bandeja**: páginas numeradas con barra "Anterior/Siguiente", números de página e indicador "X–Y de Z" (aquí "Z" es el total de correos que casan con la búsqueda en la copia local). El tamaño de página es el mismo del listado — ver [../limits/lupa.md](../limits/lupa.md) y [listado-de-correos.md](listado-de-correos.md). **No hay scroll infinito**: se salta entre páginas. Al **cambiar el texto buscado**, la navegación vuelve a la **página 1** (cambió el conjunto de resultados). Si una búsqueda no casa nada, no aparece la barra de paginación (se ve el mensaje de "sin resultados").

Nada de esto cambió con los operadores: misma tabla, mismo orden por fecha **sin ranking por relevancia** (un operador no reordena), misma paginación numerada, mismo mensaje de "sin resultados". Los operadores solo afectan a **qué** correos casan, no a cómo se muestran ni a cómo se recorren.

---

## 6-bis. `has:attachment` y una limitación heredada importante

`has:attachment` filtra por correos **marcados** como que llevan adjunto. Pero la app **solo sabe** que un correo lleva adjunto una vez que lo has **abierto** al menos una vez — la marca de "tiene adjunto" se calcula al abrir el correo, no durante la sincronización. Esto **no es nuevo de esta funcionalidad** (es así desde antes), pero el operador lo hace visible: un correo con adjunto que **nunca abriste** puede **no** aparecer en `has:attachment`. La explicación completa de por qué la marca es perezosa vive en [adjuntos.md](adjuntos.md); aquí basta saber que `has:attachment` hereda esa marca tal cual.

---

## 6-ter. Cómo se descubren los operadores

Para que el usuario sepa que los operadores existen sin tener que conocerlos de memoria, junto al campo de búsqueda aparece una **ayuda mínima**: un pequeño icono de ayuda ("?") que, al pulsarlo, despliega un panel con la **lista de operadores y un ejemplo de cada uno**, más un recordatorio de que se combinan con espacios (Y) y de que se usan comillas para frases. Se cierra al pulsar fuera o con la tecla Escape.

No es una pantalla nueva ni cambia el flujo: es solo una chuleta a un clic. Está disponible junto a la lupa en **todas** las vistas donde hay búsqueda (bandeja de una cuenta, vista unificada, Favoritos y bandejas ficticias). Los textos del panel están en español; los operadores que muestra están en inglés, igual que en el campo.

---

## 7. Qué pasa "por debajo" mientras el usuario escribe (resumen rápido)

Para entender el flujo completo de un vistazo:

1. El usuario escribe en la lupa → se actualiza el texto en pantalla y en la URL, **sin tocar la red todavía**.
2. Si el texto supera el mínimo y el usuario deja de escribir durante el debounce → el navegador hace **una única petición** al backend de MailManager.
3. El backend consulta la base de datos local (PostgreSQL) y devuelve los correos que casan con todas las reglas anteriores. No se llama a Gmail ni a Outlook.
4. La tabla de correos se repinta con los resultados.
5. Si el usuario hace clic en un correo, lo abre normalmente.
6. Si el usuario vuelve a escribir o a borrar, todo el ciclo empieza de nuevo.

Si llega la respuesta de una búsqueda antigua justo cuando el usuario ya estaba tecleando otra cosa, esa respuesta se **descarta automáticamente** (la petición anterior se cancela) para que un resultado obsoleto nunca pise el de la búsqueda más reciente. Y como cada texto buscado se cachea por separado, repetir una búsqueda reciente es instantáneo: no se vuelve a pedir al servidor.

---

## 8. Resumen en una frase

> La lupa es un filtro **local, sobre el buzón y la cuenta que se están viendo**, que combina **texto libre** (literal, sin tildes ni mayúsculas que importen, todas las palabras en asunto/remitente) con **operadores estilo Gmail** (`from: to: subject: has:attachment before: after: is:unread/read is:favorite in:inbox|sent|archive|spam|trash`) unidos con "Y", admite comillas para frases, interpreta las fechas en hora de Madrid, es **tolerante** ante errores (operador desconocido → texto, fecha/valor inválidos → se ignoran, contradicción → cero resultados), hace que `in:` sea sensible a la vista (cambia la bandeja en una vista normal, afina dentro de Favoritos y de una bandeja ficticia, donde `in:archive` además rescata los archivados excluidos por defecto) y ofrece una ayuda mínima ("?") para descubrir los operadores — todo **sin cambiar el campo, el disparo, el orden, la paginación ni la persistencia en la URL**; las cifras exactas, el catálogo cerrado de operadores y todo lo que deliberadamente no soporta viven en [../limits/lupa.md](../limits/lupa.md).
