# Características de la lupa de búsqueda — comportamiento (MVP)

Este documento describe **qué hace** la lupa cuando un usuario la usa y **qué experimenta** delante de la app, sin entrar en cómo está cableado el código. Es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la pantalla.

Los topes concretos (mínimo de caracteres, debounce, número de tokens, resultados por carga) y la lista exhaustiva de "qué NO soporta" viven en un documento aparte: **[../limits/lupa.md](../limits/lupa.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están las cifras exactas.

---

## 1. Qué busca la lupa y dónde

La lupa es un **filtro sobre lo que ya está en la app**, no un buscador que sale a Internet:

- Filtra los correos que **ya están sincronizados** en la base de datos local de MailManager. **No llama a Gmail ni a Outlook** — solo mira lo que ya tenemos guardado. Por eso es instantánea y funciona igual aunque el proveedor esté caído.
- Busca en tres datos de cada correo: el **asunto**, el **email del remitente** y el **nombre del remitente**.
- Para que un correo aparezca en los resultados basta con que **una sola** de esas tres piezas contenga lo que el usuario escribió.

Lo que la lupa **no** mira: el cuerpo del correo, los destinatarios ("Para"), CC/BCC, ni el contenido de los adjuntos. Es una búsqueda de "cabecera", no de "texto completo". La razón es directa: el cuerpo no se sincroniza durante el listado (se baja solo al abrir cada correo), así que filtrar por cuerpo obligaría a descargar todos los correos primero — justo lo contrario de una búsqueda local rápida.

---

## 2. Qué grado de "inteligencia" tiene la búsqueda

La búsqueda del MVP es **literal con dos relajaciones**: el texto escrito tiene que aparecer tal cual en alguno de los tres campos, pero ignorando **mayúsculas/minúsculas** y **tildes/acentos**.

### Lo que sí encuentra

- **Coincidencia en cualquier posición** de la cadena. Si el usuario escribe `fact`, encuentra correos cuyo asunto sea `Factura`, `Refactura 2024` o `Esto es una factura`. No hace falta que la palabra empiece por lo escrito; la coincidencia es de subcadena.
- **Sin distinguir mayúsculas/minúsculas**. Escribir `JUAN`, `juan` o `Juan` da exactamente los mismos resultados.
- **Sin distinguir tildes/acentos**. Escribir `jose` encuentra `José`; `accion` encuentra `acción`; `maria` encuentra `María`. Y al revés: escribir `José` también encuentra `jose`. Esta normalización es imprescindible para usar la app con normalidad en español, donde la gente teclea sin tildes constantemente.

> La insensibilidad a tildes se apoya en una extensión de PostgreSQL (`unaccent`) que se activa con una migración dedicada. Es un detalle de infraestructura con una trampa silenciosa asociada que se documenta en [../limits/lupa.md](../limits/lupa.md): una base de datos a la que no se le haya aplicado esa migración hace fallar toda búsqueda.

### Lo que NO encuentra

La lupa **no** corrige erratas, **no** entiende plurales ni raíces de palabra, y **no** conoce sinónimos. Son limitaciones aceptadas a propósito para el MVP; la lista completa con un ejemplo de cada una está en [../limits/lupa.md](../limits/lupa.md). En una frase: si lo escribes mal o usas otra palabra distinta a la que aparece en el correo, no aparece.

Los caracteres especiales de los patrones de búsqueda (`%`, `_`, `\`) se tratan como **texto literal**, no como comodines. Buscar `50%` busca correos que contengan literalmente "50%", no "cualquier cosa que empiece por 50". El usuario no tiene forma de inyectar comodines ni de romper la consulta — todo lo que escribe se interpreta al pie de la letra.

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
- Hay un **tope de resultados por carga** y **no hay scroll infinito** en el MVP — los valores y el motivo están en [../limits/lupa.md](../limits/lupa.md). En la práctica: una búsqueda devuelve hasta ese tope de correos (los más recientes que casan) y no hay forma de "cargar más"; lo natural es afinar la búsqueda con más palabras hasta acotar.

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

> La lupa es un filtro **local, literal, sobre el buzón y la cuenta que se están viendo**, que normaliza mayúsculas y tildes, se dispara tras una breve pausa cuando el texto supera un mínimo de caracteres, exige que **todas** las palabras del usuario aparezcan en el asunto, el email o el nombre del remitente (en cualquier orden, en cualquier campo), y muestra un tope de resultados ordenados por fecha sin scroll infinito; las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/lupa.md](../limits/lupa.md).
