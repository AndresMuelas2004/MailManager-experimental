# Límites de la visualización del contenido del correo

Catálogo cuantitativo y exhaustivo de las **fronteras de saneamiento** del visor de correos: qué etiquetas, atributos, protocolos y reglas CSS se permiten, qué umbrales aplican, y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md)**. Aquí solo van las listas exactas y los topes.

La gestión de los adjuntos descargables tiene sus propios límites en **[adjuntos.md](adjuntos.md)**. Este documento cubre el cuerpo del mensaje y las imágenes embebidas.

Todas estas listas y umbrales están **hardcodeados** en el pipeline de saneamiento y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook).

---

## 1. Protocolos permitidos en enlaces e imágenes

Solo estos esquemas sobreviven en `href`, `src`, etc. Cualquier otro (`javascript:`, `vbscript:`, `file:`, `ftp:`, `tel:`…) se elimina.

| Protocolo | Para qué | 
|-----------|----------|
| `http` | Enlaces y recursos web normales |
| `https` | Enlaces y recursos web seguros |
| `mailto` | Enlaces de "enviar correo a" |
| `cid` | Referencia interna a imágenes embebidas (se resuelven a `data:` antes de mostrarse) |
| `data` | Imágenes embebidas ya incrustadas en el HTML |

> **Por qué tan corta:** cualquier esquema fuera de esta lista o bien permite ejecutar código (`javascript:`) o bien no aporta nada a la lectura de un correo. Es la postura segura.

---

## 2. Reglas CSS (at-rules) — qué se conserva y qué se descarta

El CSS de los bloques `<style>` se filtra regla a regla.

| At-rule | Decisión | Por qué |
|---------|----------|---------|
| `@media` | **Se conserva** | Lleva los diseños responsive; sin ella las plantillas modernas perderían su layout de escritorio. |
| `@supports` | **Se conserva** | Variantes condicionales legítimas de estilo. |
| `@font-face` | **Se conserva** | Las firmas corporativas con fuentes web siguen renderizando. |
| `@import` | **Se descarta** | Traería hojas de estilo externas (fuga de recursos y de privacidad). |
| `@keyframes` | **Se descarta** | Animaciones; innecesarias y fuera de scope. |
| `@namespace` | **Se descarta** | Puede alterar la interpretación del documento. |
| `@charset` | **Se descarta** | La codificación ya se normaliza en otro paso; aquí solo confundiría. |
| `@page` y cualquier otra desconocida | **Se descarta** | Todo lo que no esté explícitamente permitido cae. |

> **Resiliencia:** cada regla se procesa de forma aislada. Una regla rota o de sintaxis exótica (p. ej. un `calc()` mal cerrado) se descarta sola, **sin** tumbar el resto del bloque `<style>` — así no se llevan por delante reglas críticas como las que ocultan el preheader.

**Propiedades CSS permitidas:** solo se conserva este vocabulario acotado de propiedades de maquetación, color, tipografía, espaciado y bordes (las que usan las plantillas reales). Cualquier propiedad fuera de esta lista —y cualquier valor que contenga `expression(…)`, `javascript:` o `vbscript:`— se elimina. Aplica tanto al CSS de los bloques `<style>` como al `style="…"` inline de cada elemento.

```
align-items, background, background-color, background-image,
background-position, background-repeat, background-size, border,
border-bottom, border-bottom-color, border-bottom-left-radius,
border-bottom-right-radius, border-bottom-style, border-bottom-width,
border-collapse, border-color, border-left, border-left-color,
border-left-style, border-left-width, border-radius, border-right,
border-right-color, border-right-style, border-right-width,
border-spacing, border-style, border-top, border-top-color,
border-top-left-radius, border-top-right-radius, border-top-style,
border-top-width, border-width, bottom, box-shadow, box-sizing,
caption-side, clear, color, display, empty-cells, float,
font, font-family, font-size, font-stretch, font-style,
font-variant, font-weight, gap, height, justify-content, left,
letter-spacing, line-height, list-style, list-style-position,
list-style-type, margin, margin-bottom, margin-left, margin-right,
margin-top, max-height, max-width, min-height, min-width,
mso-line-height-rule, mso-table-lspace, mso-table-rspace, opacity,
outline, overflow, overflow-wrap, overflow-x, overflow-y,
padding, padding-bottom, padding-left, padding-right, padding-top,
page-break-after, page-break-before, position, right, src,
table-layout, text-align, text-decoration, text-indent,
text-overflow, text-shadow, text-transform, top, vertical-align,
visibility, white-space, width, word-break, word-spacing,
word-wrap, z-index
```

Nota: las propiedades `mso-*` (`mso-line-height-rule`, `mso-table-lspace`, `mso-table-rspace`) se conservan a propósito: son inocuas (las ignora cualquier navegador) y evitan romper plantillas que las llevan inline. El filtro de propiedades inline lo aplica también `bleach` en el paso final mediante `CSSSanitizer`, con la **misma** lista.

---

## 3. Etiquetas HTML permitidas

Solo sobrevive este conjunto; cualquier otra etiqueta se elimina (su contenido de texto puede conservarse, salvo en `<script>`/`<title>`, que se eliminan con todo su contenido).

```
a, abbr, b, blockquote, br, center, code, dd, del, div, dl, dt, em, font,
h1, h2, h3, h4, h5, h6, hr, i, img, ins, li, mark, ol, p, pre, q, s, small,
span, strong, style, sub, sup, table, tbody, td, tfoot, th, thead, tr, u, ul, wbr
```

Nota: `style` está en la lista (se conserva el bloque, ya saneado, para que sobrevivan las `@media`). `script` **no** está y, además, se elimina con su contenido.

---

## 4. Atributos permitidos (por etiqueta)

| Etiqueta | Atributos permitidos |
|----------|----------------------|
| Todas (`*`) | `class`, `id`, `style`, `dir`, `lang`, `title`, `align`, `valign` |
| `a` | `href`, `target`, `rel` |
| `img` | `src`, `alt`, `width`, `height`, `border` |
| `td` | `colspan`, `rowspan`, `width`, `height`, `align`, `valign`, `bgcolor`, `background` |
| `th` | `colspan`, `rowspan`, `width`, `height`, `align`, `valign`, `bgcolor`, `background` |
| `table` | `border`, `cellpadding`, `cellspacing`, `width`, `align`, `bgcolor`, `background` |
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
| Etiquetas `<html>` / `<head>` / `<body>` | Eliminadas como envoltorio (su contenido se conserva) | Se aplana el documento a un fragmento seguro; el fondo del `<body>` se preserva aparte. |

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
| **Gmail** | "UTF-8 primero": (1) se intenta UTF-8 estricto; (2) si falla, se usa la codificación declarada por la parte; (3) último recurso, UTF-8 tolerando bytes inválidos. Devuelve vacío solo si el propio base64 está corrupto. |
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
| Política de *referrer* | **Restringida** | No se filtra de más a dónde navega el usuario. |
| Ancho de las imágenes | **Limitado al 100 %** del visor | Imágenes enormes no rompen el layout. |

> El backend ya entrega HTML saneado; el sandbox es la **segunda** capa de defensa. Cada una mitigaría lo peor por separado; juntas hacen el visor muy difícil de abusar.

---

## 9. Caché del contenido

| Aspecto | Valor / regla |
|---------|---------------|
| Qué se cachea | El HTML **ya saneado** (no el original), más el texto plano, en la base de datos local. |
| Cuándo se puebla | En la primera apertura del correo (cache-aside): miss → **una** descarga del proveedor (cuerpo + adjuntos en la misma consulta) → saneado → persistencia → entrega. |
| Reaperturas | Instantáneas, **sin** llamada al proveedor ni re-saneamiento. |
| Invalidación por cambio de pipeline | Cuando la cadena de saneamiento cambia de forma relevante, la caché del contenido se vacía de golpe para forzar el re-procesado con las reglas nuevas. Transparente para el usuario (un correo concreto vuelve a tardar 1-2 s esa primera vez). El **último vaciado** acompañó a la unificación de cuerpo+adjuntos en una sola lectura del proveedor (migración `0036`). |
| Invalidación por borrado/desconexión | El borrado de un correo o la desconexión de una cuenta limpian su contenido cacheado automáticamente (cascada en la base de datos). |

No hay un tope de tamaño propio para el cuerpo del correo: el HTML se guarda completo. (El límite de tamaño relevante es el de los **adjuntos**, documentado en [adjuntos.md](adjuntos.md).)

### 9.1 Tiempo de vivencia (TTL deslizante) del contenido cacheado

| Aspecto | Valor / regla |
|---------|---------------|
| Vida útil del cuerpo cacheado | **30 días desde el último acceso** (columna `last_accessed_at` de `email_content`). Es el **mismo plazo** que el TTL del binario de los adjuntos descargados ([adjuntos.md](adjuntos.md)), pero con un mecanismo de purga distinto (ver más abajo). |
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
| Tope por cuenta y sincronización | **50** correos como máximo (los **más recientes** primero, `ORDER BY received_at DESC`), por cuenta, en cada sincronización. Es el único valor que pasa el código Python; el TTL (30 días) y la ventana (48 h) viven en las propias consultas SQL. |
| Qué se excluye | Los correos cuyo cuerpo **ya está cacheado** (`LEFT JOIN email_content … IS NULL`): nunca se re-descargan. También quedan fuera enviados, spam, papelera y los correos **ya leídos**. |
| Concurrencia | **Secuencial**, mensaje a mensaje (no en paralelo), para no chocar con los topes de peticiones simultáneas por usuario/buzón de Gmail y Outlook (429). |
| Tolerancia a fallos | Best-effort: corre en el `BackgroundTask` tras responder; un fallo en un correo no aborta el resto y nunca afecta a la respuesta de sincronización ya enviada. |
| Reintentos | **Ninguno**: lo que falle se reintentará en la siguiente sincronización (si el correo sigue siendo no leído y reciente). |
| Índices | No añade índice nuevo: la selección de objetivos reutiliza los índices existentes de `email_metadata` (volumen del MVP). |

---

## 10. Qué NO soporta (limitaciones aceptadas)

| No soporta | Por qué |
|------------|---------|
| **Ejecutar JavaScript del correo** | Intencional: se elimina todo `<script>` y el iframe no concede permiso de ejecución. Un correo no es una aplicación. |
| **Cargar hojas de estilo o recursos externos vía CSS** (`@import`, `<link>`) | Evita fugas de privacidad y de recursos; el correo debe ser autocontenido. |
| **Incrustar partes marcadas inline pero NO referenciadas** por el cuerpo | Por la regla estricta (D-13), si el cuerpo no usa la parte vía `cid:`, se promociona a adjunto descargable en vez de incrustarse en el HTML. |
| **Bloqueo o proxy de imágenes remotas** (`<img src="https://…">`) | No hay protección anti-rastreo: los protocolos `http`/`https` se permiten en `src`, así que una imagen remota (incluidos los píxeles de seguimiento) se carga directa desde su servidor. Solo las imágenes `cid:` embebidas se resuelven a `data:`; las remotas no se tocan. |
| **Render del layout específico de Outlook de escritorio** | Los bloques solo-Outlook se descartan a propósito (duplicarían contenido en un visor no-Outlook). |
| **Etiquetas/atributos fuera de la lista blanca** (formularios, `<iframe>` anidados, `<object>`, `<embed>`, manejadores de eventos…) | Solo se permite lo necesario para leer un correo; todo lo demás es superficie de ataque. |
| **Edición del correo** | El visor es de **solo lectura**; no es un editor. |
| **Recuperación de imágenes embebidas corruptas** | Si una imagen `cid:` no se puede resolver, se deja un icono de imagen rota (best-effort); no hay reintento ni reconstrucción. |
| **Relleno automático de correos "solo Outlook" vacíos** | Solo se registra un aviso; no se inventa contenido. |
| **Purga del contenido por cron o por endpoint manual** | El barrido del TTL de los cuerpos cacheados ocurre **solo** durante las sincronizaciones, sobre las cuentas que se sincronizan. No hay programador (igual que el TTL de adjuntos) **ni** endpoint admin (a diferencia de los adjuntos). Una cuenta que no se sincronice no se limpia. |
| **Pre-carga de enviados, spam, papelera o correo ya leído** | La pre-carga se limita a los **no leídos recientes de la bandeja de entrada**: son los que el usuario va a abrir con más probabilidad; ampliarla gastaría cuota del proveedor en cuerpos que casi nunca se consultan. |
| **Re-pre-carga de un no leído de hace más de 48 h cuyo cuerpo ya se liberó por TTL** | La ventana de pre-carga son 48 h: un no leído más antiguo cuyo contenido ya se purgó **no** se vuelve a preparar solo; se carga bajo demanda (breve espera) la próxima vez que se abra. |
| **Pre-carga garantizada o completa** | Es best-effort y tope de 50 por cuenta y sincronización: si hay más de 50 no leídos recientes, o si la preparación de alguno falla, esos cuerpos se cargarán bajo demanda (o en la siguiente sincronización). No hay reintento inmediato. |

---

> El visor llega hasta: **solo 5 protocolos** (`http`, `https`, `mailto`, `cid`, `data`), **3 at-rules CSS** conservadas (`@media`, `@supports`, `@font-face`) frente al resto descartadas, una **lista blanca acotada** de etiquetas y atributos, **cero JavaScript**, bloques **solo-Outlook descartados** (aviso por encima de 200 bytes descartados y menos de 50 caracteres visibles), corrección de codificación **UTF-8-first en Gmail**, imágenes embebidas resueltas a `data:` **solo si el cuerpo las usa**, todo dentro de un **iframe aislado** sin ejecución de scripts ni acceso a la sesión, y con el resultado **cacheado** tras la primera apertura. La caché tiene un **TTL deslizante de 30 días** desde el último acceso (purgado **solo durante las sincronizaciones**, sobre las cuentas sincronizadas), y se **pre-cargan** los **no leídos de las últimas 48 h** de la bandeja de entrada, **hasta 50 por cuenta y sincronización**, en segundo plano y de forma secuencial. El comportamiento completo está en [../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md).
