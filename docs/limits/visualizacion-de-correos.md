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
| `td` | `colspan`, `rowspan`, `width`, `height`, `align`, `valign`, `bgcolor` |
| `th` | `colspan`, `rowspan`, `width`, `height`, `align`, `valign`, `bgcolor` |
| `table` | `border`, `cellpadding`, `cellspacing`, `width`, `align`, `bgcolor` |
| `font` | `color`, `size`, `face` |
| `ol` | `start`, `type` |

Cualquier atributo fuera de esta tabla (incluidos manejadores de eventos como `onclick`, `onload`, etc.) se elimina.

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
| Cuándo se puebla | En la primera apertura del correo (cache-aside): miss → descarga del proveedor → saneado → persistencia → entrega. |
| Reaperturas | Instantáneas, **sin** llamada al proveedor ni re-saneamiento. |
| Invalidación por cambio de pipeline | Cuando la cadena de saneamiento cambia de forma relevante, la caché del contenido se vacía de golpe para forzar el re-procesado con las reglas nuevas. Transparente para el usuario (un correo concreto vuelve a tardar 1-2 s esa primera vez). |
| Invalidación por borrado/desconexión | El borrado de un correo o la desconexión de una cuenta limpian su contenido cacheado automáticamente (cascada en la base de datos). |

No hay un tope de tamaño propio para el cuerpo del correo: el HTML se guarda completo. (El límite de tamaño relevante es el de los **adjuntos**, documentado en [adjuntos.md](adjuntos.md).)

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

---

> El visor llega hasta: **solo 5 protocolos** (`http`, `https`, `mailto`, `cid`, `data`), **3 at-rules CSS** conservadas (`@media`, `@supports`, `@font-face`) frente al resto descartadas, una **lista blanca acotada** de etiquetas y atributos, **cero JavaScript**, bloques **solo-Outlook descartados** (aviso por encima de 200 bytes descartados y menos de 50 caracteres visibles), corrección de codificación **UTF-8-first en Gmail**, imágenes embebidas resueltas a `data:` **solo si el cuerpo las usa**, todo dentro de un **iframe aislado** sin ejecución de scripts ni acceso a la sesión, y con el resultado **cacheado** tras la primera apertura. El comportamiento completo está en [../features/visualizacion-de-correos.md](../features/visualizacion-de-correos.md).
