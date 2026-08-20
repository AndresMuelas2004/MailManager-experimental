# Límites de la visualización del contenido del correo

Catálogo cuantitativo y exhaustivo de las **fronteras de saneamiento** del visor de correos: qué etiquetas, atributos, protocolos y reglas CSS se permiten, qué umbrales aplican, y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md)**. Aquí solo van las listas exactas y los topes.

La gestión de los adjuntos descargables tiene sus propios límites en **[adjuntos.md](adjuntos.md)**. Este documento cubre el cuerpo del mensaje y las imágenes embebidas.

Todas estas listas y umbrales están **hardcodeados** en el pipeline de saneamiento y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook).

---

## 1. Protocolos permitidos en enlaces e imágenes

Solo estos esquemas sobreviven en `href`, `src`, etc. Cualquier otro (`javascript:`, `vbscript:`, `file:`, `ftp:`…) se elimina.

| Protocolo | Para qué | 
|-----------|----------|
| `http` | Enlaces y recursos web normales |
| `https` | Enlaces y recursos web seguros |
| `mailto` | Enlaces de "enviar correo a" |
| `tel` | Enlaces de "llámanos" (típicos en pies de página con teléfono) |
| `cid` | Referencia interna a imágenes embebidas (se resuelven a `data:` antes de mostrarse) — **solo en imágenes/fondos, nunca en enlaces** |
| `data` | Imágenes embebidas ya incrustadas en el HTML — **solo en imágenes/fondos, nunca en enlaces** |

> **Por qué tan corta:** cualquier esquema fuera de esta lista o bien permite ejecutar código (`javascript:`) o bien no aporta nada a la lectura de un correo. Es la postura segura.
>
> **Matiz sobre los enlaces:** en un `<a href="…">` solo se aceptan `http`, `https`, `mailto` y `tel` (más los `href` relativos o de fragmento, que no tienen esquema). `cid:` y `data:` son protocolos "de imagen": si llegan en el `href` de un enlace, el atributo se elimina y queda solo el texto del enlace. Además, **todo enlace superviviente se endurece**: se le fuerza `target="_blank"` y `rel="noopener noreferrer"` (ver sección 8).

---

## 2. Reglas CSS (at-rules) — qué se conserva y qué se descarta

El CSS de los bloques `<style>` se filtra regla a regla.

| At-rule | Decisión | Por qué |
|---------|----------|---------|
| `@media` | **Se conserva** | Lleva los diseños responsive; sin ella las plantillas modernas perderían su layout de escritorio. |
| `@supports` | **Se conserva** | Variantes condicionales legítimas de estilo. |
| `@font-face` | **Se descarta** | Decisión de **privacidad**, no de capacidad: su `src: url(…)` era la única referencia remota que el proxy de imágenes nunca reescribe (el proxy solo sirve `image/*`), así que el navegador pedía la fuente **directamente al servidor que indicara el remitente** — un canal de rastreo que esquiva el proxy entero. Medido sobre el corpus real: 108 referencias en el 31 % de los correos, no un residuo raro. Gmail también lo elimina. Los correos afectados caen a la siguiente fuente de su propia lista `font-family`. |
| `@import` | **Se descarta** | Traería hojas de estilo externas (fuga de recursos y de privacidad). |
| `@keyframes` | **Se descarta** | Animaciones; innecesarias y fuera de scope. |
| `@namespace` | **Se descarta** | Puede alterar la interpretación del documento. |
| `@charset` | **Se descarta** | La codificación ya se normaliza en otro paso; aquí solo confundiría. |
| `@page` y cualquier otra desconocida | **Se descarta** | Todo lo que no esté explícitamente permitido cae. |

> **Resiliencia:** cada regla se procesa de forma aislada. Una regla rota o de sintaxis exótica (p. ej. un `calc()` mal cerrado) se descarta sola, **sin** tumbar el resto del bloque `<style>` — así no se llevan por delante reglas críticas como las que ocultan el preheader.

> **`!important` se conserva** en el bloque `<style>` que sobrevive. Es lo que permite que una regla `@media` gane a los estilos que el propio saneado acaba de volcar en el `style="…"` de cada elemento; sin él, las reglas responsive perdían siempre la cascada y el correo se veía con su diseño de escritorio dentro del visor, que es estrecho.

**Propiedades CSS permitidas:** solo se conserva este vocabulario acotado de propiedades de maquetación, color, tipografía, espaciado y bordes (las que usan las plantillas reales). Cualquier propiedad fuera de esta lista —y cualquier valor que contenga `expression(…)`, `javascript:` o `vbscript:`— se elimina. Aplica tanto al CSS de los bloques `<style>` como al `style="…"` inline de cada elemento.

```
align-content, align-items, align-self, background, background-attachment,
background-clip, background-color, background-image, background-origin,
background-position, background-repeat, background-size, border,
border-bottom, border-bottom-color, border-bottom-left-radius,
border-bottom-right-radius, border-bottom-style, border-bottom-width,
border-collapse, border-color, border-left, border-left-color,
border-left-style, border-left-width, border-radius, border-right,
border-right-color, border-right-style, border-right-width,
border-spacing, border-style, border-top, border-top-color,
border-top-left-radius, border-top-right-radius, border-top-style,
border-top-width, border-width, bottom, box-shadow, box-sizing,
caption-side, clear, color, column-gap, direction, display, empty-cells,
flex, flex-basis, flex-direction, flex-flow, flex-grow, flex-shrink,
flex-wrap, float, font, font-family, font-size, font-stretch, font-style,
font-variant, font-weight, gap, height, inset, justify-content,
justify-items, justify-self, left, letter-spacing, line-height, list-style,
list-style-image, list-style-position, list-style-type, margin,
margin-block, margin-block-end, margin-block-start, margin-bottom,
margin-inline, margin-inline-end, margin-inline-start, margin-left,
margin-right, margin-top, max-height, max-width, min-height, min-width,
mso-line-height-rule, mso-table-lspace, mso-table-rspace, object-fit,
object-position, opacity, order, outline, overflow, overflow-wrap,
overflow-x, overflow-y, padding, padding-block, padding-block-end,
padding-block-start, padding-bottom, padding-inline, padding-inline-end,
padding-inline-start, padding-left, padding-right, padding-top,
page-break-after, page-break-before, position, right, row-gap, src,
table-layout, text-align, text-decoration, text-decoration-color,
text-decoration-line, text-decoration-style, text-decoration-thickness,
text-indent, text-overflow, text-shadow, text-transform, top, unicode-bidi,
vertical-align, visibility, white-space, width, word-break, word-spacing,
word-wrap, z-index
```

Nota: las propiedades `mso-*` (`mso-line-height-rule`, `mso-table-lspace`, `mso-table-rspace`) se conservan a propósito: son inocuas (las ignora cualquier navegador) y evitan romper plantillas que las llevan inline. El filtro de propiedades inline lo aplica también `bleach` en el paso final mediante una subclase de `CSSSanitizer`, con la **misma** lista.

Además del filtro por nombre de propiedad, se aplican estas reglas de **valor y de regla** (idénticas en bloques `<style>` y en `style=""` inline):

| Regla CSS | Tratamiento | Por qué |
|-----------|-------------|---------|
| Valores con `expression(…)`, `javascript:`, `vbscript:` | Declaración eliminada | Ejecución de código / esquemas peligrosos. |
| Valores con `var(…)` (usos de custom properties) | Declaración eliminada | Las definiciones `--x` nunca sobreviven al saneado; el uso huérfano computa "vacío" **y además** pisa el fallback clásico (`bgcolor`) que la plantilla trae para este caso. Gmail también los elimina; al quitarlos, el fallback pinta. |
| Definiciones de custom properties (`--x: …`) | Eliminadas | Nombre fuera de la lista blanca de propiedades. |
| `@media (prefers-color-scheme: …)` (dark **y** light) | Bloque entero eliminado | El visor es solo-claro (paridad con Gmail web, que también las elimina); sin esto, con el SO en modo oscuro el correo mostraba la paleta oscura del remitente a medias. El visor además fija `color-scheme: light` en el documento y en el propio `<iframe>` (protege los cuerpos cacheados antes del filtro). |
| El resto de `@media` (ancho, orientación…) y `@supports` | Se conservan | Diseños responsive. |

Nota (interna pero *load-bearing*): tras la lista blanca final, las entidades HTML que el serializador deja dentro de los bloques `<style>` se **des-escapan** (`&gt;` → `>`, nunca `&lt;`). `<style>` es *rawtext* — el navegador no decodifica entidades ahí — y un combinador hijo serializado como `&gt;` **parte el selector** en el parser CSS por el `;` de la entidad, convirtiendo reglas acotadas (los hacks de modo oscuro `[data-ogsc]` de Outlook) en reglas globales que pintaban fondos oscuros en el visor claro.

---

## 3. Etiquetas HTML permitidas

Solo sobrevive este conjunto; cualquier otra etiqueta se elimina (su contenido de texto puede conservarse, salvo en `<script>`/`<title>`, que se eliminan con todo su contenido).

```
a, abbr, address, article, aside, b, big, blockquote, br, caption, center,
cite, code, col, colgroup, dd, del, dfn, div, dl, dt, em, figcaption,
figure, font, footer, h1, h2, h3, h4, h5, h6, header, hr, i, img, ins, kbd,
li, main, mark, nav, ol, p, pre, q, s, samp, section, small, span, strike,
strong, style, sub, sup, table, tbody, td, tfoot, th, thead, time, tr, tt,
u, ul, var, wbr
```

Nota: `style` está en la lista (se conserva el bloque, ya saneado, para que sobrevivan las `@media`). `script` **no** está y, además, se elimina con su contenido.

Nota: los envoltorios semánticos de HTML5 (`section`, `article`, `header`, `footer`, `figure`, …) y la maquinaria de columnas de tabla (`caption`, `col`, `colgroup`) están en la lista a propósito: al eliminar una etiqueta se conserva su texto pero se pierde **el estilo que llevaba encima** (un `<section style="background:…">` perdería su fondo), y las plantillas modernas los usan como contenedores con estilo.

---

## 4. Atributos permitidos (por etiqueta)

| Etiqueta | Atributos permitidos |
|----------|----------------------|
| Todas (`*`) | `class`, `id`, `style`, `dir`, `lang`, `title`, `align`, `valign` |
| `a` | `href`, `target`, `rel` — `target` y `rel` se **fuerzan** después a `_blank` / `noopener noreferrer` (sección 8) |
| `img` | `src`, `alt`, `width`, `height`, `border`, `hspace`, `vspace` |
| `td` | `colspan`, `rowspan`, `width`, `height`, `align`, `valign`, `bgcolor`, `background`, `nowrap` |
| `th` | `colspan`, `rowspan`, `width`, `height`, `align`, `valign`, `bgcolor`, `background`, `nowrap` |
| `tr` | `bgcolor`, `height` |
| `table` | `border`, `cellpadding`, `cellspacing`, `width`, `height`, `align`, `bgcolor`, `background` |
| `col` / `colgroup` | `span`, `width`, `bgcolor` |
| `font` | `color`, `size`, `face` |
| `ol` | `start`, `type` |

Cualquier atributo fuera de esta tabla (incluidos manejadores de eventos como `onclick`, `onload`, etc.) se elimina.

> **El atributo `background` (no confundir con `bgcolor`).** Es el atributo HTML heredado que apunta una tabla o celda a una **imagen de fondo** (`<td background="https://…">`). Muchas plantillas de correo lo usan como alternativa a `<img>` para las miniaturas de producto y los mosaicos de cabecera (p. ej. las rejillas de AliExpress). Está en la lista blanca de `td`/`th`/`table`; sin él, `bleach` lo eliminaría y la celda quedaría solo con su `background-color` de relleno, mostrándose como un **recuadro gris**. `bleach` lo trata como atributo de URI, así que **se le aplica el mismo filtro de protocolos** que a `src`/`href` (un `background="javascript:…"` se elimina).

---

## 5. Etiquetas y construcciones que se eliminan siempre

| Construcción | Tratamiento | Por qué |
|--------------|-------------|---------|
| `<script>` | Eliminada **con su contenido** | No se ejecuta ningún JS en el visor. |
| `<title>` | Eliminada con su contenido | Si no, su texto se colaría como cuerpo visible (el asunto repetido dentro del correo). |
| `<!DOCTYPE>` | Eliminada | Construcción de cabecera; a nivel de fragmento confunde al parser. |
| `<meta>` | Eliminada | Metadatos de cabecera (incluida la declaración de charset falsa). |
| `<link>` | Eliminada | Traería hojas/recursos externos. |
| `<base>` | Eliminada | Cambiaría la resolución de URLs del fragmento. |
| `<xml>` (islas MSO de Office) | Eliminada con su contenido | Restos de Outlook que no deben renderizarse. |
| Etiquetas `<html>` / `<head>` / `<body>` | Eliminadas como envoltorio (su contenido se conserva) | Se aplana el documento a un fragmento seguro; el fondo del `<body>` se preserva aparte: su `style`, su `bgcolor` y su `background` (imagen de fondo de página completa) se promueven a un `<div>` envolvente, de modo que ni el reset blanco del visor los tapa ni la imagen de fondo desaparece. |

---

## 6. Bloques condicionales de Outlook (MSO)

| Tipo de bloque | Tratamiento |
|----------------|-------------|
| Variante para clientes **no-Outlook** (downlevel-revealed) | **Se conserva** y se desenvuelve (ES el contenido a mostrar). |
| Variante **solo Outlook de escritorio** (downlevel-hidden, incluidas islas XML) | **Se descarta por completo** (evita duplicados visibles en un visor que no es Outlook). |

**Umbral de aviso "correo solo-Outlook vacío":** si se descartan **más de 200 bytes** de bloques solo-Outlook **y** el cuerpo resultante queda con **menos de 50 caracteres visibles**, la app registra un aviso en los logs. No hay relleno automático: solo se deja constancia para que un newsletter legacy "solo Outlook" sea observable en lugar de desaparecer en silencio.

---

## 7. Codificación de caracteres (mojibake)

| Proveedor | Estrategia | 
|-----------|-----------|
| **Gmail** | "UTF-8 primero": (1) se intenta UTF-8 estricto; (2) si falla, se usa la codificación declarada por la parte; (3) último recurso, UTF-8 tolerando bytes inválidos. Devuelve vacío solo si el propio base64 está corrupto. **Excepción:** si la codificación declarada es "ASCII-enmascarada" (ISO-2022-JP/KR, HZ-GB-2312, UTF-7 — sus bytes son todos ASCII, así que el UTF-8 estricto "acertaría" devolviendo las secuencias de escape como texto visible), se intenta **primero la declarada** y solo si falla se cae a la estrategia normal. |
| **Outlook** | La negociación de codificación la hace el servidor de Graph; no necesita corrección en cliente. |

Además, en cualquier proveedor se eliminan las declaraciones de charset falsas del HTML (p. ej. `us-ascii`, `windows-1252`) y, **solo cuando el documento tiene cabecera**, se inyecta una declaración UTF-8 canónica. Si no hay cabecera, no se inyecta nada (el contenido ya llega decodificado como texto Unicode y anteponer una `<meta>` podría empeorar el render).

---

## 8. Aislamiento del visor (iframe sandbox)

El cuerpo se renderiza dentro de un iframe con permisos mínimos.

| Capacidad | Estado en el visor | Efecto |
|-----------|--------------------|--------|
| Ejecutar JavaScript | **Bloqueado** | El correo no corre scripts (no se concede el permiso de ejecución). |
| Acceso a sesión / cookies / almacenamiento de la app | **Bloqueado** | El iframe está confinado: no comparte origen con la app. |
| Abrir ventanas emergentes (popups) | **Permitido** | Necesario para que los enlaces puedan abrirse. |
| Que los popups escapen del sandbox | **Permitido** | Los enlaces se abren como pestañas normales del navegador. |
| Destino de los enlaces | **Pestaña nueva** (`target="_blank"`) | Nunca se navega dentro del visor. |
| Atributos forzados en cada enlace | `target="_blank"` + `rel="noopener noreferrer"` (los del remitente se sobrescriben) | La pestaña abierta no conserva referencia al visor (`window.opener` queda cortado — anti *reverse tabnabbing*) ni recibe *referrer*. |
| Política de *referrer* | **Restringida** | No se filtra de más a dónde navega el usuario. |
| Ancho de las imágenes | **Sin límite** (el visor no impone `max-width`) | Un `max-width` porcentual dentro de una celda de tabla auto-layout puede colapsar la celda a 0 px y hacer desaparecer la imagen (el logo de cabecera de Amazon.es desaparecía así). Gmail web tampoco restringe las imágenes; el contenido más ancho que el visor se desplaza con scroll horizontal. |
| Esquema de color del documento | **Fijado a claro** (`color-scheme: light` en el documento y en el `<iframe>`) | Las media queries de modo oscuro del remitente nunca se activan, ni siquiera en los cuerpos cacheados antes del filtro del saneador (sección 2). |

> El backend ya entrega HTML saneado; el sandbox es la **segunda** capa de defensa. Cada una mitigaría lo peor por separado; juntas hacen el visor muy difícil de abusar.

---

## 9. Caché del contenido

| Aspecto | Valor / regla |
|---------|---------------|
| Qué se cachea | El HTML **ya saneado** (no el original), más el texto plano, en la base de datos local. |
| Cuándo se puebla | En la primera apertura del correo (cache-aside): miss → **una** descarga del proveedor (cuerpo + adjuntos en la misma consulta) → saneado → persistencia → entrega. |
| Reaperturas | Instantáneas, **sin** llamada al proveedor ni re-saneamiento. |
| Invalidación por cambio de pipeline | Cuando la cadena de saneamiento cambia de forma relevante, la caché del contenido se vacía de golpe para forzar el re-procesado con las reglas nuevas. Transparente para el usuario (un correo concreto vuelve a tardar 1-2 s esa primera vez). El **último vaciado** acompañó a los **arreglos de `<style>`** — des-escapado de entidades en el CSS, eliminación de las media queries de modo oscuro y de las declaraciones `var(…)` (migración `0046`; los anteriores: `0043` proxy de imágenes, `0044` premailer sin red). |
| Invalidación por borrado/desconexión | El borrado de un correo o la desconexión de una cuenta limpian su contenido cacheado automáticamente (cascada en la base de datos). |

No hay un tope de tamaño propio para el cuerpo del correo: el HTML se guarda completo. (El límite de tamaño relevante es el de los **adjuntos**, documentado en [adjuntos.md](adjuntos.md).)

### 9.1 Tiempo de vivencia (TTL deslizante) del contenido cacheado

| Aspecto | Valor / regla |
|---------|---------------|
| Vida útil del cuerpo cacheado | **7 días desde el último acceso** (columna `last_accessed_at` de `email_content`). Plazo **deliberadamente más corto** que el TTL del binario de los adjuntos descargados (**30 días**, [adjuntos.md](adjuntos.md)): **ya no comparten plazo** (antes ambos eran 30 días). Mecanismo de purga distinto (ver más abajo). |
| Qué reinicia el contador | Cada apertura del correo (acierto de caché) sella `last_accessed_at = now()`. Toca **solo** ese campo, nunca `fetched_at` (el cuerpo es inmutable: una lectura no es una re-descarga). El sellado es best-effort: si falla, no rompe la lectura del correo. |
| Cuándo arranca el contador | Al **persistir** la fila (cada fila de `email_content` es una entrada de caché creada al guardarse, así que `last_accessed_at` nunca es nulo). A diferencia del TTL de adjuntos, no hay guarda `IS NOT NULL` en la purga. |
| Qué se purga | **Solo** el contenido caducado de `email_content`. Una re-apertura posterior lo vuelve a descargar (cache-aside) con el contador a cero. |
| Mecanismo de purga | **Automático durante las sincronizaciones**, no manual ni programado (no hay cron en el MVP, y **tampoco** endpoint admin —a diferencia de la purga de adjuntos). Un único `DELETE` indexado por `(account_id, last_accessed_at)`. |
| Alcance de cada purga | Solo las **cuentas que se sincronizan** en esa petición. Una cuenta que no se sincronice durante mucho tiempo **no se limpia** hasta que vuelva a sincronizarse. |
| Cuándo corre | En segundo plano (`BackgroundTask`), **después** de que la sincronización haya respondido; **antes** de la pre-carga (libera espacio que la pre-carga luego rellena). No ralentiza la sincronización. |

### 9.2 Pre-carga de contenido (prefetch post-sincronización)

| Aspecto | Valor / regla |
|---------|---------------|
| Qué se pre-carga | Cuerpo **+ adjuntos** (la misma ruta que una apertura normal) de los correos **no leídos** (`is_read = FALSE`), de la **bandeja de entrada** (`box = 'ALL_MAIL'`). |
| Ventana de "reciente" | Recibidos en las **últimas 48 horas** (`received_at >= now() - INTERVAL '48 hours'`). |
| Tope por cuenta y sincronización | **50** correos como máximo (los **más recientes** primero, `ORDER BY received_at DESC`), por cuenta, en cada sincronización. Es el único valor que pasa el código Python; la ventana (48 h) y la bandeja objetivo (`ALL_MAIL`) viven en la propia consulta SQL. |
| Qué se excluye | Los correos cuyo cuerpo **ya está cacheado** (`LEFT JOIN email_content … IS NULL`): nunca se re-descargan. También quedan fuera enviados, spam, papelera y los correos **ya leídos**. |
| Concurrencia | **Secuencial**, mensaje a mensaje (no en paralelo), para no chocar con los topes de peticiones simultáneas por usuario/buzón de Gmail y Outlook (429). |
| Tolerancia a fallos | Best-effort: corre en el `BackgroundTask` tras responder; un fallo en un correo no aborta el resto y nunca afecta a la respuesta de sincronización ya enviada. |
| Reintentos | **Ninguno**: lo que falle se reintentará en la siguiente sincronización (si el correo sigue siendo no leído y reciente). |
| Índices | No añade índice nuevo: la selección de objetivos reutiliza los índices existentes de `email_metadata` (volumen del MVP). |

### 9.3 Pre-carga y caché en la memoria del navegador (segunda capa)

Complementa la caché de la base de datos (§ 9) y la pre-carga de servidor (§ 9.2): guarda el cuerpo ya saneado en la **memoria del navegador** para que abrir/reabrir no muestre spinner. Es del lado **cliente**, no servidor.

| Aspecto | Valor / regla |
|---------|---------------|
| Qué guarda | El cuerpo ya saneado con sus **URLs de proxy ya resueltas** a la ruta absoluta del backend (listo para el iframe). La misma entrada la usan la apertura y la pre-carga. |
| Vigencia de la entrada | `staleTime` **infinito** (el cuerpo es inmutable y sus URLs firmadas son estables: reabrir nunca revalida — acierto de caché, sin red). Se descarta de memoria tras **30 minutos** sin usarse (`gcTime`). |
| Qué se pre-carga | Correos **no leídos**, de la **bandeja de entrada** (`box = ALL_MAIL`), recibidos en las **últimas 48 h**, presentes en la **página actual** del listado. Mismo alcance que la pre-carga de servidor (§ 9.2). |
| Cuándo | Al cargar el listado (bandejas reales y virtuales), en segundo plano cuando el navegador está ocioso (`requestIdleCallback`, con fallback a `setTimeout`). |
| Concurrencia | **3** peticiones `/content` en vuelo a la vez (`PREFETCH_CONCURRENCY`). Se calientan **todos** los objetivos, pero por «carriles»: un cursor compartido alimenta 3 peticiones simultáneas como máximo. Antes se lanzaba **una por objetivo sin límite** (hasta una página entera, ~50, de golpe), saturando el backend síncrono justo cuando el usuario abría un correo. Un cambio de página/objetivo o el desmontaje **aborta** el lanzamiento de nuevas peticiones (las ya en vuelo se dejan terminar; TanStack Query deduplica una entrada ya caliente). |
| Privacidad | La pre-carga **solo** trae el HTML (JSON de `/content`) y resuelve sus URLs de proxy; **no** monta el iframe ni crea ningún `<img>`, así que **no descarga imágenes** ni contacta con ningún remitente (§ 10). |
| Deduplicación | Una entrada ya fresca en caché es un no-op: nunca re-descarga un correo ya abierto o ya pre-cargado. |

---

## 10. Proxy de imágenes remotas

Las imágenes remotas `http(s)` del correo se sirven a través del backend (`GET /image-proxy`) en lugar de descargarse del servidor del remitente. El **comportamiento** (privacidad, pereza, caché de servidor) está en [../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md) § 4.3; aquí van las cifras.

### 10.1 Qué se reescribe hacia el proxy

Al sanear el cuerpo (paso final, tras la lista blanca), estas referencias de imagen remota `http(s)` se reescriben a una URL firmada del proxy:

| Referencia | ¿Se reescribe? |
|------------|----------------|
| `<img src="https://…">` | Sí |
| `background="https://…"` en `td` / `th` / `table` | Sí |
| `style="… url(https://…)"` inline (propiedades de imagen) | Sí |
| Bloque `<style>` con `url(https://…)` en propiedades de imagen | Sí |
| Cualquiera de las anteriores en forma **relativa al protocolo** (`//host/ruta`) | Sí — se normaliza a `https://host/ruta` antes de firmar. Dentro del `srcdoc` del visor resuelve contra la URL de la propia app, así que sin reescribir el navegador la pediría directamente al remitente (fuga de IP) |
| `@font-face { src: url(https://…) }` | **No aplica** — el `@font-face` se descarta antes (sección 2), así que ninguna referencia a fuente llega hasta aquí |
| `cid:` / `data:` / URL relativa o de fragmento | **No** — no son remotas |

Propiedades CSS consideradas "de imagen" para reescribir su `url(...)`: `background`, `background-image`, `list-style`, `list-style-image`.

**El destino de un `url(...)` entrecomillado puede contener paréntesis** (`url("…?fit=crop(1,1)")`, habitual en los parámetros de transformación de los CDN de imágenes): se reescribe entero. Sin comillas, el paréntesis cierra el `url()` como manda CSS.

Antes de firmar, la URL se normaliza como haría el navegador: se eliminan tabulaciones y saltos de línea incrustados y se recortan los espacios de los extremos.

**Reescritura resiliente:** la pasada estructurada (lxml) reescribe atributos y CSS; si tropieza con un HTML roto, un **fallback por regex** reescribe al menos los atributos `src=` / `background=`. En un correo patológico donde actúe el fallback, un `url(...)` raro podría quedar sin reescribir (se cargaría directo del remitente) — residuo aceptado.

### 10.2 Firma y URL centinela

| Aspecto | Valor / regla |
|---------|---------------|
| Prefijo centinela en el HTML cacheado | `https://mm-image-proxy.invalid/img?u=<base64url(url)>&s=<firma>`. El host `.invalid` (RFC 6761) **nunca resuelve**: si el frontend no lo reescribe, la imagen simplemente se rompe (fail-closed, sin fuga de IP). |
| Firma | **HMAC-SHA256** de la URL original, clave `IMAGE_PROXY_SIGNING_KEY` (env). El endpoint la verifica en tiempo constante; firma **presente pero inválida** → **403** `image_proxy_forbidden`. Params `u`/`s` **ausentes** → **422** (validación de FastAPI), no 403. |
| Resolución en el cliente | El frontend cambia el prefijo centinela por la URL absoluta real del proxy (`{apiBase}/image-proxy`), conservando `?u=…&s=…`. |
| La clave de firma es *load-bearing* | Como `/image-proxy` está **exento del rate limit**, una clave débil/predecible convertiría el endpoint en un relay abierto de imágenes. **Guarda de arranque:** con `IMAGE_PROXY_REQUIRE_KEY=true` (producción), el backend **rechaza arrancar** si la clave falta o es la de dev. Rotar la clave invalida todas las URLs ya firmadas; los correos abiertos con frecuencia **no** se auto-recuperan (nunca se purgan por inactividad), así que rotar exige `TRUNCATE email_content`. |

### 10.3 Descarga (fetcher anti-SSRF)

| Límite | Valor exacto | Nota |
|--------|--------------|------|
| Esquemas permitidos | `http`, `https` | Cualquier otro → bloqueado (403). |
| Tamaño máximo de imagen | **10 MB** | Aplica al `Content-Length` declarado y al streaming real; excederlo → 502. |
| Timeout | **10 s por fase** (connect / read / write / pool) | |
| Redirecciones máximas | **3** (4 peticiones en total) | Cada salto se revalida anti-SSRF antes de la petición. |
| Content-Type | Debe empezar por `image/`, **o** ser genérico (`application/octet-stream`, `binary/octet-stream`, cabecera ausente) con un cuerpo cuyos **magic bytes** sean una imagen ráster reconocible (JPEG, PNG, GIF, WebP, AVIF, BMP, ICO) | Un tipo genérico con cuerpo no reconocible → 502. Un tipo no-imagen no-genérico (`text/html`…) → 502 sin descargar el cuerpo. El sniffing existe porque S3/GCS sirven `application/octet-stream` cuando el remitente subió la imagen sin fijar tipo (correos reales con **todas** sus imágenes rotas). **SVG nunca se sniffea**: solo se sirve si el upstream declara `image/svg+xml`. |
| Cabeceras hacia el remitente | Solo un `User-Agent` fijo | **Sin** cookies, credenciales, `Referer` ni nada que revele al usuario. |
| Reutilización de conexiones (keep-alive) | Cliente `httpx` **compartido** a nivel de proceso | Varias imágenes del mismo host reutilizan una conexión TCP+TLS viva en lugar de un handshake nuevo por imagen (el coste dominante en newsletters con muchas imágenes). Pool: hasta **20** conexiones keep-alive, **100** conexiones en total como máximo, expiración **30 s** de inactividad. Se crea de forma perezosa y se cierra al apagar la app (best-effort). `follow_redirects=False` sigue fijo: cada salto se revalida a mano (anti-SSRF, más abajo). |

**Destinos bloqueados por anti-SSRF** (cualquier host que resuelva a uno de estos → 403): direcciones privadas, loopback, link-local, reservadas, multicast, "unspecified", IPv4 mapeada en IPv6, más CGNAT (`100.64.0.0/10`), metadatos de nube IPv4 (`169.254.169.254`) e IMDS de AWS IPv6 (`fd00:ec2::254`). Cada salto de redirección se valida por separado.

**Residuo TOCTOU aceptado (MVP):** entre validar el host y abrir la conexión, httpx re-resuelve el DNS; un registro con TTL de sub-segundo que pase de IP pública a privada podría colarse por esa ventana. Cerrarla del todo (fijar la conexión a la IP validada preservando el SNI de TLS) queda como endurecimiento futuro.

### 10.4 Caché de servidor de imágenes proxeadas

| Aspecto | Valor / regla |
|---------|---------------|
| Tabla / clave | `image_proxy_cache`, keyeada por el **SHA-256 hex de la URL original**. Clave **global** (compartida entre cuentas y usuarios): la misma imagen de CDN referenciada desde muchos correos se descarga del remitente **una sola vez**. |
| Almacenamiento | Binario inline en `image_bytes` (BYTEA); imágenes acotadas (10 MB) y servidas enteras. |
| TTL | **30 días deslizantes** desde el último acceso (`last_accessed_at`). El refresco en cada servida está **limitado a un bump por URL cada 24 h** (throttle en memoria) para no abrir una conexión de BD por imagen servida; como el TTL es de 30 días, esa precisión sub-diaria es irrelevante. **Distinto** del TTL del cuerpo (`email_content`, 7 días — § 9.1); coincide con el de los binarios de adjuntos ([adjuntos.md](adjuntos.md)). |
| Purga | **Manual**: `POST /admin/image-proxy/purge` (cabecera `X-Admin-Token` = env `IMAGE_PROXY_PURGE_TOKEN`). Sin cron/scheduler. Tres estados: env sin definir → **503** `purge_disabled`; token ausente/incorrecto → **401** `invalid_admin_token`; correcto → ejecuta y devuelve `{purged_count, freed_bytes}`. (Mismo modelo que la purga de adjuntos.) |

### 10.5 Endpoint y caché del navegador

| Aspecto | Valor / regla |
|---------|---------------|
| Endpoint | `GET /image-proxy?u=…&s=…` — **sin cookie de sesión** (el iframe del visor es de origen "null" y la petición es un subrecurso cross-site, así que la cookie nunca viaja; la firma HMAC es la puerta de acceso). |
| Rate limit | **Exento** del límite global por-IP: un newsletter puede llevar docenas de imágenes, y un bucket por imagen dispararía el límite al abrir un solo correo. La firma + el anti-SSRF acotan el abuso. |
| Concurrencia de servido | Máx. **8** peticiones servidas a la vez (semáforo en el servicio; las excedentes **esperan**, no fallan) + **3 intentos** con esperas de **50/150 ms** en las lecturas/escrituras de la caché ante un pool de BD agotado. Sin estas cotas, el burst de un open en frío agotaba el pool (25 conexiones compartidas con sync/prefetch/backfill) y un subconjunto aleatorio de imágenes fallaba con 503 instantáneo (celdas grises / iconos rotos). |
| Caché del navegador | `Cache-Control: private, max-age=2592000, immutable` (**30 días**; la URL firmada es estable) + `X-Content-Type-Options: nosniff`. |
| Códigos de estado | **403** firma inválida (`image_proxy_forbidden`) o destino bloqueado por anti-SSRF (`image_proxy_blocked_target`); **422** si faltan los params `u`/`s`; **502** fallo de descarga upstream, contenido no-imagen/sobredimensionado o error de lectura de caché (`image_proxy_upstream_error`); **503** agotamiento **sostenido** del pool de BD tras agotar los reintentos (`database_connection_error`). Desde un `<img>` el navegador solo distingue 2xx de no-2xx (imagen rota); los códigos importan para logs/tests. |

---

## 11. Qué NO soporta (limitaciones aceptadas)

| No soporta | Por qué |
|------------|---------|
| **Ejecutar JavaScript del correo** | Intencional: se elimina todo `<script>` y el iframe no concede permiso de ejecución. Un correo no es una aplicación. |
| **Cargar hojas de estilo o recursos externos vía CSS** (`@import`, `<link>`) | Evita fugas de privacidad y de recursos; el correo debe ser autocontenido. |
| **Incrustar partes marcadas inline pero NO referenciadas** por el cuerpo | Por la regla estricta (D-13), si el cuerpo no usa la parte vía `cid:`, se promociona a adjunto descargable en vez de incrustarse en el HTML. |
| **Fuentes web del remitente** (`@font-face`) | No se pueden servir por el proxy (solo sirve `image/*`) y cargarlas directas abría un canal de rastreo que lo esquiva, así que la at-rule se **descarta** entera (§ 2). El correo cae a la siguiente fuente de su lista `font-family`. |
| **Cierre total de la ventana TOCTOU de DNS** | El anti-SSRF valida el host y luego httpx lo re-resuelve; un DNS con TTL de sub-segundo que pase de IP pública a privada podría colarse. Fijar la conexión a la IP validada queda como endurecimiento futuro (§ 10.3). |
| **Fidelidad perfecta del cuerpo cuando lxml malinterpreta un fragmento** | Si la pasada estructurada de lxml pierde la mayoría de los elementos de layout (o lanza), se usa un fallback por regex que reescribe `src=` / `background=` **y** todos los `url(...)` remotos sobre la cadena original, sin reestructurar (no se pierde contenido). La **privacidad se preserva**: ninguna URL cruda sobrevive. |
| **Purga del proxy de imágenes por cron o automática** | Solo hay purga manual vía `POST /admin/image-proxy/purge` (igual que los adjuntos). Sin programador en el MVP. |
| **Descarga asíncrona o paralela del proxy en el servidor** | El endpoint `GET /image-proxy` es **síncrono** por decisión (MVP): la aceleración viene de reutilizar conexiones (keep-alive, § 10.3), no de paralelizar. Dos semáforos (máx. 8 cada uno) acotan las descargas upstream y el servido completo (§ 10.5) para que abrir un correo con muchas imágenes no agote ni el threadpool compartido ni el pool de BD. |
| **Servir como imagen un cuerpo sin firma de imagen reconocible bajo Content-Type genérico** | El sniffing de magic bytes (§ 10.3) admite solo formatos ráster conocidos; un HTML de error, un SVG o bytes arbitrarios etiquetados `octet-stream` se rechazan — el proxy nunca es un relay ciego. |
| **Aislamiento por usuario de las imágenes proxeadas** | La caché del proxy es global (keyeada por hash de URL) para deduplicar y minimizar contactos con el remitente; no se particiona por usuario ni por correo (§ 10.4). |
| **Persistencia de la caché en memoria del navegador entre sesiones** | La caché en memoria (§ 9.3) es efímera: se pierde al cerrar la pestaña y se descarta tras 30 min sin uso. La persistencia entre sesiones la aporta la caché de la base de datos, no esta capa. |
| **Render del layout específico de Outlook de escritorio** | Los bloques solo-Outlook se descartan a propósito (duplicarían contenido en un visor no-Outlook). |
| **Modo oscuro del remitente** (`@media (prefers-color-scheme: dark)` y los hacks `[data-ogsc]`/`[data-ogsb]`) | El visor es solo-claro: las media queries de esquema se eliminan y el documento se fija a `color-scheme: light` (paridad con Gmail web). Los selectores `[data-ogsc]` sobreviven pero no casan con nada (ese atributo solo existe en Outlook web). |
| **Variables CSS (custom properties / `var(…)`)** | Gmail tampoco las soporta, así que toda plantilla real trae fallback clásico (`bgcolor`, declaraciones duplicadas); se eliminan definiciones y usos y el fallback pinta. Conservar los usos huérfanos anulaba el fallback (declaración de autor > atributo presentacional). |
| **Etiquetas/atributos fuera de la lista blanca** (formularios, `<iframe>` anidados, `<object>`, `<embed>`, manejadores de eventos…) | Solo se permite lo necesario para leer un correo; todo lo demás es superficie de ataque. |
| **Edición del correo** | El visor es de **solo lectura**; no es un editor. |
| **Recuperación de imágenes embebidas corruptas** | Si una imagen `cid:` no se puede resolver, se deja un icono de imagen rota (best-effort); no hay reintento ni reconstrucción. |
| **Relleno automático de correos "solo Outlook" vacíos** | Solo se registra un aviso; no se inventa contenido. |
| **Purga del contenido por cron o por endpoint manual** | El barrido del TTL de los cuerpos cacheados ocurre **solo** durante las sincronizaciones, sobre las cuentas que se sincronizan. No hay programador (igual que el TTL de adjuntos) **ni** endpoint admin (a diferencia de los adjuntos). Una cuenta que no se sincronice no se limpia. |
| **Pre-carga de enviados, spam, papelera o correo ya leído** | La pre-carga se limita a los **no leídos recientes de la bandeja de entrada**: son los que el usuario va a abrir con más probabilidad; ampliarla gastaría cuota del proveedor en cuerpos que casi nunca se consultan. |
| **Re-pre-carga de un no leído de hace más de 48 h cuyo cuerpo ya se liberó por TTL** | La ventana de pre-carga son 48 h: un no leído más antiguo cuyo contenido ya se purgó **no** se vuelve a preparar solo; se carga bajo demanda (breve espera) la próxima vez que se abra. |
| **Pre-carga garantizada o completa** | Es best-effort y tope de 50 por cuenta y sincronización: si hay más de 50 no leídos recientes, o si la preparación de alguno falla, esos cuerpos se cargarán bajo demanda (o en la siguiente sincronización). No hay reintento inmediato. |

---

> El visor llega hasta: **solo 6 protocolos** (`http`, `https`, `mailto`, `tel`, `cid`, `data` — los dos últimos solo en imágenes, nunca en enlaces), **3 at-rules CSS** conservadas (`@media`, `@supports`, `@font-face` — pero **toda `@media` de `prefers-color-scheme` se elimina**: el visor es solo-claro y fija `color-scheme: light`) frente al resto descartadas, **declaraciones `var(…)` y custom properties eliminadas** (el fallback clásico pinta), entidades de los bloques `<style>` **des-escapadas** (`&gt;` → `>`, nunca `&lt;`) para que los combinadores de selector lleguen intactos al parser CSS, una **lista blanca acotada** de etiquetas y atributos, **cero JavaScript**, cada enlace **endurecido** con `target="_blank"` + `rel="noopener noreferrer"`, bloques **solo-Outlook descartados** (aviso por encima de 200 bytes descartados y menos de 50 caracteres visibles), corrección de codificación **UTF-8-first en Gmail** (con la codificación declarada primero para ISO-2022-*/HZ/UTF-7), imágenes embebidas resueltas a `data:` **solo si el cuerpo las usa** (emparejando el `cid:` sin distinguir mayúsculas ni percent-encoding), **imágenes remotas `http(s)` reescritas a un proxy propio del backend** (`GET /image-proxy`, firma **HMAC-SHA256**, fetcher **anti-SSRF**, tope de **10 MB**, timeout de **10 s**, máx. **3** redirecciones, **conexiones reutilizadas (keep-alive)** por un cliente HTTP compartido, caché de servidor **global** de **30 días**, endpoint **síncrono y exento del rate limit**) para que el remitente nunca vea la IP del usuario, todo dentro de un **iframe aislado** sin ejecución de scripts ni acceso a la sesión, y con el resultado **cacheado** tras la primera apertura —en la base de datos y en la memoria del navegador. La caché del cuerpo en base de datos tiene un **TTL deslizante de 7 días** desde el último acceso (purgado **solo durante las sincronizaciones**, sobre las cuentas sincronizadas; ya **no** coincide con los 30 días de los adjuntos), la caché en memoria del navegador dura mientras la pestaña siga abierta (**staleTime infinito**, **gcTime 30 min**), y se **pre-cargan** —sin descargar sus imágenes— los **no leídos de las últimas 48 h** de la bandeja de entrada, **hasta 50 por cuenta y sincronización** en base de datos (secuencial, en segundo plano) y también en memoria del navegador (los de la página actual, **hasta 3 a la vez**). El comportamiento completo está en [../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md).
