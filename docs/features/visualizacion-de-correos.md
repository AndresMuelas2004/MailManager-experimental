# Visualización del contenido del correo — comportamiento

Este documento describe **qué hace** la app cuando un usuario abre un correo para leerlo, y **qué experimenta** delante de la pantalla. El objetivo es enseñar el cuerpo del mensaje **lo más fiel posible al original** pero **saneado**: sin scripts, sin trucos que filtren la privacidad y sin que un correo malicioso pueda hacer nada peligroso. No entra en cómo está cableado el código; es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

La lista exhaustiva de "qué se permite y qué no" (etiquetas, atributos, protocolos, reglas CSS, umbrales) y la lista de "lo que NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md)**. Este fichero solo menciona los límites de pasada y enlaza a ese catálogo cuando hace falta.

La gestión de los **adjuntos** que cuelgan del correo (las tarjetas de descarga, el clip, la descarga bajo demanda) tiene su propio documento: **[adjuntos.md](adjuntos.md)**. Aquí solo se cubre el cuerpo del mensaje y las **imágenes embebidas** que forman parte del propio HTML.

> **Dónde se monta este render hoy.** Todo lo que describe este documento —descargar el cuerpo una vez, sanearlo, resolver `cid:`, mostrarlo en un iframe aislado, cachearlo— es **el render de un cuerpo de mensaje** y no cambia. Lo que cambia es **el contenedor**: en las bandejas reales y en la unificada, abrir una fila ya **no** abre un único mensaje, sino la **cadena completa de la conversación**, y el cuerpo de cada mensaje del hilo se carga **al expandirlo** (carga perezosa) usando exactamente este mismo render. Ese envoltorio (la fila-conversación, la cadena, el orden, el marcado del hilo) se documenta en [conversaciones.md](conversaciones.md). El visor de **un solo mensaje** que aquí se describe en su forma directa sobrevive hoy en la pestaña de **Favoritos** (que no agrupa). Marcar como leído cambia de matiz en conversación: abrir el hilo marca **todos** sus mensajes no leídos de golpe (no solo el abierto) — ver [conversaciones.md](conversaciones.md).

---

## 1. Qué pasa cuando el usuario abre un correo

Al hacer clic en un correo de la lista, se abre una ventana modal con la cabecera (asunto, remitente, fecha, cuenta) y, debajo, el **cuerpo del mensaje**. Mientras el cuerpo se carga aparece un spinner; cuando llega, se pinta el correo renderizado tal y como lo diseñó el remitente, salvo por las transformaciones de seguridad que se explican más abajo.

Lo importante de este flujo:

- **El cuerpo no se descarga durante la sincronización del buzón.** La lista solo trae metadata (asunto, remitente, fecha). El contenido completo (HTML + texto) se baja **solo al abrir el correo**, una vez. Por eso la lista carga rápido aunque haya miles de correos.
- **La primera apertura puede tardar uno o dos segundos** (hay que ir al proveedor, sanear el HTML y guardarlo). Las siguientes aperturas del mismo correo son **instantáneas**: el contenido ya saneado queda cacheado en la base de datos local y se sirve directo, sin volver a llamar a Gmail/Outlook ni a re-procesar nada (patrón *cache-aside*, sección 7). Además, el contenido de los correos **no leídos recientes de la bandeja de entrada se prepara por adelantado** durante la sincronización, así que muchas veces ni siquiera la *primera* apertura tiene espera (ver sección 7.2). Y una vez abierto un correo, **reabrirlo** —o reexpandir un mensaje dentro de una conversación— en la misma sesión es instantáneo **sin tocar la red**: su cuerpo ya saneado queda también en la memoria del navegador (ver sección 7.5).
- **Marcar como leído** ocurre en paralelo: abrir un correo no leído lo marca como leído una sola vez (no se repite si el usuario reabre la misma ventana).

Si el correo ya no existe en nuestra base de datos local (por ejemplo, fue borrado entre el listado y el clic), la app responde con un error claro de "correo no encontrado" en lugar de intentar descargarlo del proveedor.

#### Ejemplo

> El usuario abre un newsletter de una tienda. Spinner ~1 s mientras la app baja el HTML del proveedor, lo limpia y lo guarda. Aparece el correo con su cabecera de imágenes, sus columnas y sus colores. El usuario lo cierra y lo vuelve a abrir un minuto después: esta vez aparece al instante, sin spinner perceptible.

---

## 2. Qué tipos de contenido sabe mostrar

Un correo puede llegar de tres formas, y la app las trata así:

- **Correo con cuerpo HTML** (el caso normal de newsletters, facturas, notificaciones): se renderiza el HTML saneado. Es el grueso de este documento.
- **Correo solo de texto plano** (sin HTML): se muestra el texto tal cual, respetando saltos de línea y espacios, con una tipografía legible. No hay nada que sanear porque no hay marcado.
- **Correo sin contenido**: si el mensaje no trae ni HTML ni texto utilizable, se muestra un aviso de "Este correo no tiene contenido" en vez de una ventana vacía.

Cuando hay HTML, **el HTML manda**: aunque el correo también traiga una versión de texto plano, se prioriza la versión HTML para que el usuario vea el correo como fue diseñado.

---

## 3. El correo se ve fiel, pero saneado

Esta es la parte central. El HTML que envía un remitente **no es de fiar**: puede traer scripts, balizas de rastreo, trucos para romper el layout de la app o contenido pensado para ejecutarse. Antes de enseñarlo, la app lo pasa por una **cadena de saneamiento** que conserva la apariencia del correo legítimo y elimina todo lo peligroso o inútil.

El resultado que ve el usuario es un correo **visualmente equivalente al original** en la inmensa mayoría de los casos —mismos colores, imágenes, tipografías, columnas responsive— pero **inerte**: no puede ejecutar código ni hacer nada que el usuario no haya pedido.

### 3.1 Qué se conserva

- **La maquetación y los estilos**: colores, fuentes, tamaños, márgenes, bordes, tablas, columnas. Se mantiene tanto el CSS embebido en bloques `<style>` como el que va inline en cada elemento.
- **Las imágenes embebidas** del propio correo (logos, banners, firmas con foto), incluidas las referenciadas con `cid:` (ver sección 4).
- **Las imágenes de fondo de tablas y celdas** definidas con el atributo HTML heredado `background` (`<td background="https://…">`). Muchas plantillas (las rejillas de producto de AliExpress, por ejemplo) pintan ahí la miniatura en lugar de usar `<img>`. Se conservan; si se descartaran, la celda se quedaría solo con su color de relleno y se vería como un **recuadro gris** en vez de la imagen. La lista exacta de etiquetas que admiten este atributo está en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).
- **Los diseños responsive**: las reglas que adaptan el correo a distintos anchos de pantalla sobreviven, así que las plantillas de newsletter modernas renderizan su versión de escritorio correctamente dentro del visor. Esto incluye maquetación moderna con flexbox y propiedades CSS lógicas, además de las tablas clásicas. También se conservan las **marcas de prioridad** (`!important`) que esas reglas usan: sin ellas perdían contra los estilos que el propio saneado vuelca en cada elemento, y el correo se veía con su diseño de escritorio comprimido dentro de un visor estrecho — columnas sin apilarse y piezas pensadas para móvil que nunca aparecían.
- **Los contenedores semánticos con estilo**: las etiquetas estructurales de HTML5 (`section`, `article`, `header`, `footer`, `figure`…) y la maquinaria de columnas de tabla (`caption`, `col`, `colgroup`) se conservan con sus estilos. Antes se eliminaba la etiqueta dejando solo el texto, con lo que un contenedor con fondo o geometría perdía su aspecto.
- **Los atributos de maquetación heredados**: el fondo de una fila (`<tr bgcolor>`), la altura de filas y tablas, los márgenes de imagen (`hspace`/`vspace`) y el `nowrap` de las celdas — el vocabulario de las plantillas de correo antiguas — se conservan. La lista exacta está en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).
- **El fondo del correo**: si el remitente fijó un fondo de página (un color —por ejemplo, un marco lavanda alrededor del contenido— o una imagen de fondo), se preserva en lugar de quedar tapado por el fondo blanco del visor.
- **Los enlaces**: se mantienen y, al pulsarlos, **abren en una pestaña nueva** del navegador (nunca dentro del visor). Los enlaces de teléfono (`tel:`) también funcionan. Además, cada enlace se **endurece** en el saneamiento: se le fuerza la apertura en pestaña nueva y un `rel` que corta toda referencia de la pestaña abierta hacia el visor (ver sección 6).

### 3.2 Qué se elimina

- **Todo el JavaScript**: los bloques `<script>` se eliminan por completo, contenido incluido. En el correo no se ejecuta **ningún** código (ver también la sección 6, sobre el aislamiento del visor).
- **Etiquetas y atributos no permitidos**: solo sobrevive un conjunto acotado de etiquetas (texto, tablas, listas, imágenes, enlaces…) y de atributos. Cualquier cosa fuera de esa lista se descarta. La lista exacta está en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).
- **Protocolos peligrosos en enlaces e imágenes**: solo se permiten unos pocos esquemas seguros (los típicos de web, correo, teléfono e imágenes embebidas). Un `href="javascript:…"` o similar se elimina. Los esquemas de imagen embebida (`cid:`, `data:`) se aceptan en imágenes y fondos pero **no en enlaces**: si llegan en un `href`, el enlace pierde su destino y queda solo el texto.
- **Reglas CSS que traen recursos externos o cambian el modo de interpretación** del documento: se conservan las reglas de maquetación y las responsive, pero se descartan las que importarían hojas externas o reconfigurarían el parseo. El detalle de qué reglas CSS se mantienen y cuáles caen está en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).
- **Los estilos de "modo oscuro" del remitente**: las reglas condicionadas al esquema de color del sistema (`prefers-color-scheme`) se eliminan y, además, el visor fija el documento del correo en modo claro. El correo se muestra **siempre con su diseño claro de base**, igual que hace Gmail web, aunque el sistema operativo del usuario esté en modo oscuro. Sin esto, un usuario en modo oscuro veía la paleta oscura del remitente aplicada a medias (fondos negros con las imágenes y textos pensados para fondo claro — ilegible).
- **Las variables CSS (`var(…)` / custom properties)**: las plantillas que las usan traen siempre un mecanismo de respaldo clásico (el atributo `bgcolor`, declaraciones duplicadas) porque Gmail tampoco las soporta. Se descartan tanto las definiciones como los usos, y ese respaldo clásico es el que pinta el correo. Conservar los usos con las definiciones ya eliminadas dejaba el color "vacío" **y además** anulaba el respaldo, borrando fondos enteros.
- **Cabeceras y metadatos del documento que no deben verse**: el título del documento (`<title>`), las declaraciones de tipo, los `<meta>`, `<link>`, `<base>` y restos de islas XML de Office se eliminan para que no se cuelen como texto visible ni desordenen el render.

### 3.3 Por qué se sanea en el servidor y no en el navegador

El saneamiento ocurre **en el backend**, antes de guardar y antes de enviar nada al navegador. La razón es doble: (1) así el HTML peligroso nunca llega intacto al cliente, y (2) el resultado saneado se cachea una sola vez y se reutiliza en cada apertura, sin repetir el trabajo. El navegador recibe HTML ya limpio y, además, lo muestra dentro de un sandbox (sección 6) como segunda capa de defensa.

### 3.4 Robustez: si algo del saneamiento falla, no se pierde el correo

La cadena de saneamiento es **tolerante a fallos**: si un paso concreto tropieza con un HTML especialmente roto, ese paso se salta dejando el contenido como estaba y la cadena continúa, en vez de abortar y dejar al usuario sin correo. El último paso (la limpieza final con la lista blanca) siempre se ejecuta, así que el HTML que llega al navegador **siempre** está saneado aunque algún paso intermedio haya fallado. El precio aceptado es que, en un correo patológico, alguna mejora cosmética (por ejemplo, restaurar dimensiones de una imagen) podría no aplicarse — pero el correo se ve y es seguro.

---

## 4. Imágenes embebidas (`cid:` → `data:`)

Muchos correos no enlazan sus imágenes desde Internet, sino que las **adjuntan dentro del propio mensaje** y las referencian con un identificador interno (`cid:…`). Es lo típico del logo de una firma o de las imágenes de cabecera de un newsletter.

La app **resuelve esas imágenes embebidas** para que se vean: localiza la imagen dentro del correo, la convierte a una URL `data:` (la imagen viaja incrustada en el propio HTML, codificada) y sustituye la referencia `cid:` por esa URL. El resultado es que el usuario ve el logo y los banners directamente, sin que el navegador tenga que pedir nada a ningún servidor externo.

Esto se aplica tanto a las imágenes referenciadas desde un atributo (`src="cid:…"`, `background="cid:…"`) como a las referenciadas desde CSS (`url(cid:…)`), que es lo que aparece cuando los estilos se aplican inline.

El emparejamiento entre el identificador declarado por la parte embebida y la referencia `cid:` del HTML es **tolerante**: no distingue mayúsculas de minúsculas ni percent-encoding (`cid:image%40x` casa con `image@x`). Los remitentes reales no son consistentes en esto, y con un emparejamiento literal la imagen quedaba como icono roto (o, peor, se promocionaba a adjunto descargable) aunque estuviera perfectamente adjunta.

### 4.1 Solo se incrustan las imágenes que el cuerpo usa de verdad

La regla es **estricta**: una parte embebida solo se incrusta como imagen inline si **el cuerpo del correo la referencia realmente** mediante `cid:`. Si una parte viene marcada como "inline" pero el HTML no la usa en ningún sitio, **no** se incrusta en el cuerpo: se promociona a **adjunto descargable** y aparece como una tarjeta más (ver [adjuntos.md](adjuntos.md)). Así se evitan dos errores típicos de correos mal construidos: que una imagen "inline pero no usada" desaparezca sin que el usuario la vea, o que un PDF con identificador interno acabe escondido dentro del HTML. Esta distinción (la regla "D-13" del repositorio) es la misma que separa imagen inline de adjunto descargable.

### 4.2 Si una imagen embebida no se puede resolver, el correo no se pierde

Resolver cada imagen es **best-effort**: si una imagen concreta no se puede recuperar (fallo de red, parte corrupta), la app **deja la referencia `cid:` intacta** y sigue. El usuario verá un icono de imagen rota en ese hueco, pero el resto del correo se renderiza con normalidad. Un fallo aislado en una imagen nunca tumba la visualización del mensaje entero.

#### Ejemplo

> Una firma corporativa trae el logo de la empresa como imagen embebida y un PDF de "política de privacidad" también embebido pero NO referenciado en el cuerpo. La app incrusta el logo en el HTML (se ve en su sitio) y promociona el PDF a tarjeta de adjunto descargable bajo el cuerpo. Si el logo no se pudiera recuperar, en su lugar aparece un icono de imagen rota y el texto de la firma se ve igualmente.

### 4.3 Las imágenes remotas pasan por un proxy propio (privacidad)

Las imágenes que el correo **no** lleva embebidas, sino que enlaza desde Internet (`<img src="https://cdn…">`, los fondos de tabla/celda `background="https://…"` y los `background-image: url(https://…)` del CSS), ya **no las descarga el navegador directamente** del servidor del remitente. Al sanear el correo, la app **reescribe** todas esas URLs remotas para que apunten a un **proxy propio del backend**: cuando el visor pinta el correo, el navegador pide cada imagen al backend, y es el backend quien la descarga del remitente, la valida y la sirve.

Para el usuario el correo se ve **igual que antes** —las imágenes aparecen en su sitio—, pero con dos ganancias:

- **Privacidad: el servidor del remitente nunca ve la IP del usuario.** Ve la IP del backend. Esto vale también para los **píxeles de seguimiento**: el remitente ya no aprende la IP ni el lugar desde donde se lee el correo.
- **Menos contactos con el remitente: una imagen ya vista no se le vuelve a pedir.** El backend guarda cada imagen descargada en una **caché de servidor** (compartida entre todos los usuarios y correos), así que reabrir el correo días después, desde otro dispositivo o con la caché del navegador vacía **no** genera una nueva descarga contra el remitente. Efecto secundario de privacidad: al remitente **no le llega un nuevo "abierto"** en cada reapertura.

**Las imágenes remotas cargan más rápido, de golpe.** Para descargar las imágenes de un correo, el backend **reutiliza una conexión ya abierta** con cada servidor (CDN) en lugar de renegociar una conexión nueva por cada imagen. Combinado con la caché de servidor —que se va calentando entre correos—, las imágenes de un mismo correo aparecen prácticamente **a la vez**, en vez de irse pintando una a una. Es una mejora **solo de transporte**: no cambia nada de la privacidad ni de la pereza —el remitente sigue sin ver la IP del usuario y no se pide ninguna imagen hasta que el correo se abre de verdad—. Los parámetros exactos de la reutilización de conexiones están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).

**El proxy es perezoso: solo se contacta al remitente cuando abres de verdad el correo.** La reescritura de URLs ocurre al sanear, pero **no descarga ninguna imagen**; la descarga (a través del backend) sucede únicamente cuando el visor renderiza el correo, es decir, cuando el usuario lo abre. Ni la pre-carga del cuerpo en la base de datos (sección 7.2) ni la pre-carga en la memoria del navegador (sección 7.5) piden imagen alguna: solo guardan el HTML. Así, el patrón de privacidad es **estrictamente mejor que antes**: la primera lectura de un correo con tracking pixel sí genera un contacto con el remitente, pero **anonimizado tras la IP del backend** y **nunca antes** de que el usuario abra el correo; las reaperturas ya no generan ninguno.

**Solo se proxean imágenes remotas `http(s)`.** Las imágenes **embebidas** (`cid:`, ya convertidas a `data:` — secciones 4 a 4.2) no cambian: siguen viajando dentro del propio HTML, sin red.

**Las fuentes web del remitente ya no se cargan.** El proxy solo sabe servir imágenes, así que una fuente no puede pasar por él; y dejarla cargar directa abría justo el agujero que el proxy existe para tapar —el navegador pidiéndole algo al servidor que el remitente indique, con la IP del usuario—. Por eso la regla `@font-face` se descarta entera al sanear, igual que hace Gmail. El efecto visible es pequeño: el correo se muestra con la siguiente tipografía de su propia lista (Arial, Helvetica y similares), que las plantillas de correo siempre declaran.

Cuenta como remota también la forma **sin protocolo** (`//servidor/imagen.png`), habitual en plantillas antiguas: dentro del visor esa dirección se completaría con el protocolo de la propia app y el navegador acabaría pidiéndosela al remitente. Se reescribe igual que el resto, así que la garantía de privacidad no tiene ese agujero.

**Si el proxy no puede servir una imagen, se ve como imagen rota.** Igual que cualquier imagen que falla: si el servidor del remitente está caído, la imagen fue borrada, o el destino se **bloquea por seguridad**, en su hueco aparece el icono de imagen rota y el resto del correo se renderiza con normalidad. El bloqueo por seguridad corresponde a la protección **anti-SSRF**: si una imagen remota apunta a un destino peligroso (una dirección interna/privada camuflada por un remitente malicioso), el proxy la rechaza. Los límites exactos del proxy (tamaños, tiempos, esquemas y destinos bloqueados, plazo de la caché de servidor) están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).

**Las imágenes mal etiquetadas por el remitente también se ven.** Algunos remitentes alojan sus imágenes en almacenes (S3 y similares) que las sirven declarando un tipo de contenido genérico («esto son bytes») en lugar de «esto es una imagen». En ese caso el proxy **no se fía de la etiqueta**: inspecciona los primeros bytes del fichero y, si son de verdad una imagen (JPEG, PNG, GIF, WebP…), la sirve con su tipo real. Si los bytes no corresponden a ninguna imagen conocida —una página de error, un SVG, contenido arbitrario— la rechaza, como siempre. Sin esta tolerancia, correos reales llegaban con **todas** sus imágenes rotas de forma permanente.

**Abrir un correo con muchas imágenes no rompe ninguna al azar.** La primera apertura de un correo con decenas de imágenes dispara todas sus descargas casi a la vez; el backend **encola** las peticiones excedentes en lugar de dejarlas fallar, de modo que todas las imágenes acaban pintándose (las últimas tardan un instante más). Antes, ese estallido podía agotar recursos internos y un subconjunto aleatorio de imágenes aparecía roto o como un hueco gris hasta reabrir el correo varias veces. Las cotas exactas de concurrencia están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).

---

## 5. Asimetrías entre Gmail y Outlook

El cuerpo de un correo llega de forma distinta según el proveedor, y la app absorbe esas diferencias para que el usuario vea siempre un resultado coherente.

### 5.1 Acentos y codificación (mojibake)

El problema clásico: un remitente escribe en UTF-8 pero **etiqueta mal** su correo como Latin-1 / Windows-1252. Si se respetara esa etiqueta equivocada, los acentos saldrían corruptos (`José` se vería como `JosÃ©`).

- **Gmail** entrega el cuerpo en crudo, así que la app aplica una estrategia **"UTF-8 primero"**: intenta decodificar como UTF-8 (que es lo correcto en la práctica totalidad de los correos modernos); solo si eso falla, recurre a la codificación que el correo declaraba; y como último recurso decodifica en UTF-8 tolerando bytes sueltos. Esto elimina el mojibake en los correos que mienten sobre su codificación.
- **Excepción a la regla anterior**: hay codificaciones (ISO-2022-JP y familia — típicas del correo japonés y coreano —, HZ, UTF-7) cuyos bytes son **todos ASCII**, así que el intento "UTF-8 primero" nunca falla con ellas… pero devuelve las secuencias de escape como texto visible en lugar del texto real. Cuando el correo declara una de esas codificaciones, la app la respeta **primero** y solo cae a la estrategia normal si esa decodificación falla (es decir, si la etiqueta también mentía).
- **Outlook** (Graph API) hace esa negociación de codificación en su propio servidor, así que sus cuerpos no sufren este problema y no necesitan la corrección.

Además, en el HTML que llega de Outlook es habitual encontrar una declaración de charset falsa (por ejemplo `us-ascii`). La app **elimina esas declaraciones equivocadas** y, cuando el documento tiene cabecera, inyecta una declaración UTF-8 canónica para que el motor de render no se equivoque al interpretar los acentos.

### 5.2 Bloques de Outlook (MSO) que no deben verse

Las plantillas de correo suelen traer bloques pensados **solo para Outlook de escritorio**, escritos como "comentarios condicionales". Hay dos tipos y la app los trata de forma opuesta:

- **Bloques pensados para clientes que NO son Outlook** (la variante que cualquier webmail debería mostrar): se **conservan** y se desenvuelven, porque ese ES el contenido que el usuario debe ver.
- **Bloques pensados SOLO para Outlook de escritorio**: se **descartan por completo**. El visor de la app no es Outlook de escritorio, así que mostrar esos bloques produciría **duplicados visibles** (el mismo logo o la misma cabecera apareciendo dos veces, una por cada variante de la plantilla). Descartarlos es lo que evita esa duplicación.

Hay un caso límite cubierto: si un correo es **legacy "solo Outlook"** y, al descartar sus bloques, se queda prácticamente sin contenido visible, la app lo **registra como aviso** en los logs para que sea observable, en lugar de dejar que el correo desaparezca en silencio. No se aplica ningún relleno automático — simplemente queda constancia. El umbral exacto que dispara ese aviso está en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).

### 5.3 Descubrimiento de imágenes embebidas

Para resolver las imágenes `cid:`, cada proveedor expone su contenido de forma distinta (Gmail recorre el árbol MIME del mensaje; Outlook pide la lista de adjuntos del mensaje pidiendo siempre el identificador "inmutable"). El usuario no percibe esta diferencia: en ambos casos ve sus imágenes embebidas en el sitio correcto.

---

## 6. Cómo se aísla el correo en pantalla (el visor)

Aunque el HTML ya llega saneado del backend, la app añade una **segunda capa de defensa** al pintarlo: el cuerpo del correo se muestra dentro de un **iframe aislado (sandbox)**, no directamente en la página de la app. Esto significa que:

- **El correo no puede ejecutar JavaScript** dentro del visor. El sandbox no concede el permiso de ejecución de scripts, así que aunque algo se colara, no correría.
- **El correo no puede acceder a la sesión ni a los datos de la app**. Está confinado: no puede leer cookies, ni el almacenamiento, ni manipular el resto de la página de MailManager.
- **Los enlaces se abren en una pestaña nueva** del navegador, fuera del visor, y con una política de *referrer* restringida para no filtrar de más a dónde se navega.
- **La pestaña abierta no puede "volver atrás" contra el visor**: como el sandbox permite abrir pestañas, el saneamiento fuerza en cada enlace los atributos que cortan la referencia inversa (`window.opener`). Sin eso, una página maliciosa abierta desde un correo podría intentar navegar el visor a otro sitio mientras el usuario no mira (*reverse tabnabbing*).
- **Las imágenes se ajustan al ancho** del visor para que un correo con imágenes enormes no rompa el layout.

En conjunto: el backend entrega HTML limpio, y el frontend lo muestra en una "caja de cristal" de la que el correo no puede salir. Cualquiera de las dos capas por separado ya mitigaría lo peor; juntas, el visor es muy difícil de abusar. Los detalles de qué permite y qué bloquea exactamente el sandbox están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).

---

## 7. El contenido se cachea tras la primera apertura

El cuerpo saneado de cada correo se guarda en la base de datos local de la app la primera vez que alguien lo abre. A partir de ahí:

- **Reabrir el mismo correo es instantáneo** y no consume cuota del proveedor: se sirve el HTML ya saneado desde la caché.
- **Lo que se cachea es el resultado YA saneado**, no el HTML original. Es decir, el trabajo de limpieza se hace una sola vez por correo.
- La lista de adjuntos descargables (ver [adjuntos.md](adjuntos.md)) se recalcula en cada apertura para mantenerse coherente aunque una purga por TTL haya borrado los binarios.
- **Bajar el cuerpo es además algo más rápido** que antes: en una apertura sin caché, la app pide al proveedor el cuerpo y la lista de adjuntos en **una sola consulta** en lugar de dos.

### 7.1 La caché no vive para siempre: tiempo de vivencia deslizante

El contenido guardado tiene un **tiempo de vivencia (TTL) deslizante**: si pasas mucho tiempo sin volver a abrir un correo, su contenido cacheado se elimina automáticamente para liberar espacio en la base de datos. El reloj **se reinicia con cada apertura**, así que el contenido de los correos que consultas a menudo permanece siempre disponible y rápido; solo se libera lo que llevabas mucho tiempo sin mirar.

Como el cuerpo de un correo es inmutable (no cambia con el tiempo), este TTL es **pura liberación de espacio**, no caducidad por frescura: borrar el cuerpo no pierde nada, porque siempre se puede volver a pedir al proveedor. Este plazo se **acortó** recientemente y **ya no coincide** con el de los binarios de los adjuntos descargados (ver [adjuntos.md](adjuntos.md)): el cuerpo cacheado se libera **antes** que un adjunto descargado. Los dos plazos exactos están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).

La limpieza de lo caducado **no la dispara un proceso programado**: ocurre **durante las sincronizaciones**, sobre las cuentas que se sincronizan. Una cuenta que pase mucho tiempo sin sincronizarse no se limpia hasta que vuelva a sincronizar. Para el usuario es transparente: la purga corre en segundo plano, después de que la sincronización ya haya respondido, y no la ralentiza.

### 7.2 Pre-carga: preparar por adelantado los no leídos recientes

Para que abrir el correo reciente sea inmediato, la app **pre-carga su contenido por adelantado**. Cada vez que se sincroniza una bandeja (algo que ocurre, como mínimo, al entrar y abrirla), y **después de que la sincronización haya respondido**, un trabajo en segundo plano prepara el cuerpo de los correos **no leídos**, **recibidos hace poco**, de la **bandeja de entrada** de cada cuenta sincronizada. Así, cuando el usuario vaya a abrirlos uno a uno, aparecen al instante, sin spinner.

Detalles del comportamiento:

- **Solo los no leídos recientes de la bandeja de entrada.** No se pre-cargan los enviados, ni el spam, ni la papelera, ni los correos ya leídos. La ventana de "reciente" y el número máximo de correos preparados por cuenta en cada sincronización están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).
- **Lo que ya estuviera cacheado no se vuelve a pedir.** La pre-carga salta los correos cuyo cuerpo ya está en la caché: nunca re-descarga lo que ya tiene.
- **Es best-effort y secuencial.** Los cuerpos se preparan de uno en uno (no en paralelo) para no chocar con los límites de peticiones simultáneas de Gmail/Outlook; si la preparación de un correo falla, no aborta la del resto. Como ocurre en segundo plano tras responder, un fallo aquí nunca afecta a la sincronización ni a la lista que el usuario ya está viendo.
- **Prepara el cuerpo Y los adjuntos**, igual que una apertura normal. Esto es deliberado: si solo se guardara el cuerpo, la siguiente apertura sería un acierto de caché que ya no volvería a descubrir los adjuntos (el descubrimiento solo ocurre al bajar el contenido), y el clip y las tarjetas de adjunto quedarían ocultos para siempre en el correo pre-cargado.

### 7.3 Si reabres un correo viejo cuyo contenido ya se liberó

Si abres un correo tan antiguo que su contenido ya se había eliminado de la caché por el TTL, la app lo vuelve a pedir al proveedor **una única vez** (con una breve espera de carga), lo sanea y lo guarda otra vez, con su tiempo de vivencia **renovado** desde esa apertura. Es exactamente el mismo flujo que una primera apertura: no se pierde nada, solo se vuelve a pagar el coste de carga de algo que llevabas mucho tiempo sin mirar.

### 7.4 Por qué a veces "se refresca solo" un correo viejo

La caché del contenido se **invalida deliberadamente** cuando la cadena de saneamiento cambia de forma relevante (por ejemplo, cuando se mejoró el tratamiento de los bloques de Outlook, de los acentos o de las imágenes embebidas, o cuando se introdujo el **proxy de imágenes remotas** —que reescribe las URLs de imagen guardadas dentro del cuerpo cacheado). En esos momentos, los correos cacheados con la versión antigua del saneamiento se vacían de la caché para forzar a que se vuelvan a bajar y a procesar con las reglas nuevas la próxima vez que se abran. Para el usuario es transparente: como mucho, un correo concreto vuelve a tardar uno o dos segundos en abrirse esa primera vez tras el cambio. El borrado de un correo o la desconexión de una cuenta también limpian su contenido cacheado automáticamente.

### 7.5 Segunda capa de caché: en la memoria del navegador (reapertura sin red)

Además de la caché en la base de datos (que evita volver a llamar al proveedor), la app mantiene una **segunda caché en la memoria del navegador** con el cuerpo ya saneado de los correos que va abriendo o pre-cargando. Su efecto es distinto y complementario:

- **Reabrir es instantáneo y sin red.** Una vez que un correo se ha abierto en la sesión actual, volver a abrirlo —o reexpandir uno de sus mensajes dentro de una conversación— **no vuelve a pedir el cuerpo**: se sirve desde la memoria del navegador, sin spinner y sin ninguna petición. (La caché de la base de datos ya hacía la reapertura rápida, pero seguía costando una ida y vuelta de red; esta capa la elimina.)
- **Pre-carga en memoria de los no leídos recientes visibles.** Al abrir una bandeja (real o virtual), la app prepara en segundo plano —en la memoria del navegador— el cuerpo de los correos **no leídos**, **recibidos hace poco**, de la **bandeja de entrada** que se ven en la **página actual**. Cuando el usuario los abre uno a uno, aparecen al instante. Es la misma ventana de "reciente" y el mismo alcance que la pre-carga en la base de datos (sección 7.2); el tope y los plazos están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md). Esta preparación se hace además con **concurrencia limitada** —solo unas pocas peticiones a la vez, no toda la página de golpe—, para no saturar el backend justo cuando el usuario intenta abrir un correo: de otro modo la propia pre-carga podía disparar demasiadas peticiones simultáneas y, paradójicamente, **retrasar la primera apertura**. La cifra exacta está en el mismo catálogo de límites.
- **La pre-carga en memoria no descarga imágenes.** Solo trae el HTML ya saneado (y resuelve sus URLs de proxy); **no** monta el visor ni pide ninguna imagen, así que **no contacta con ningún remitente** (ver la nota de privacidad de la sección 4.3). Las imágenes se piden —a través del proxy del backend— solo cuando el usuario abre de verdad el correo.

Esta caché en memoria es **efímera**: vive mientras la pestaña está abierta y se descarta al cabo de un rato sin usarse (el plazo exacto está en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md)). No sustituye a la caché de la base de datos —que sí sobrevive entre sesiones—, sino que se apoya en ella.

---

## 8. Casos borde y comportamientos a tener en cuenta

- **Correo cuyo cuerpo está vacío tras descartar bloques de Outlook**: se ve un correo casi vacío y queda un aviso en logs (sección 5.2). No hay relleno automático.
- **Imagen embebida irrecuperable**: icono de imagen rota en ese hueco; el resto del correo se ve bien (sección 4.2).
- **Imagen remota que el proxy no puede servir** (remitente caído, imagen borrada, o destino bloqueado por la protección anti-SSRF): icono de imagen rota en ese hueco; el resto del correo se ve con normalidad (sección 4.3).
- **Correo que solo trae texto plano**: se muestra el texto respetando saltos de línea, sin saneamiento de HTML (no hay marcado que sanear).
- **Correo con un `<title>` largo**: el título del documento **no** aparece como texto al principio del cuerpo; se elimina al sanear (era un error típico que metía el asunto repetido dentro del cuerpo).
- **Newsletter responsive de escritorio**: se ve la versión de escritorio correctamente, porque las reglas responsive sobreviven al saneamiento.
- **Correo con fondo de color**: el marco/fondo del remitente se conserva y no queda tapado por el blanco del visor.
- **Correo borrado entre el listado y el clic**: error de "correo no encontrado", sin gastar llamada al proveedor.
- **Correo muy antiguo cuyo contenido se liberó por TTL**: se vuelve a pedir al proveedor una vez (breve espera) y se re-cachea con el contador a cero (sección 7.3). No se pierde nada.
- **Correo no leído de hace más de la ventana de pre-carga cuyo contenido ya se había liberado**: **no** se vuelve a pre-cargar solo; se cargará bajo demanda, con una breve espera, la próxima vez que se abra. La pre-carga solo cubre los no leídos recientes (sección 7.2).
- **Reapertura en la misma sesión**: instantánea y **sin red**, servida desde la memoria del navegador (sección 7.5); reexpandir un mensaje de una conversación ya visto se comporta igual.

---

## 9. Resumen en una frase

> Al abrir un correo, la app descarga su cuerpo una sola vez (en una única consulta que trae también sus adjuntos), lo pasa por una cadena de saneamiento en el servidor que conserva la apariencia (estilos, imágenes, diseño responsive, fondo) pero elimina todo lo peligroso (scripts, etiquetas/atributos/protocolos no permitidos, reglas CSS que traen recursos externos, cabeceras del documento), endurece cada enlace para que abra en pestaña nueva sin dejar referencia inversa hacia el visor, resuelve las imágenes embebidas `cid:` a `data:` solo cuando el cuerpo las usa de verdad (emparejando el identificador sin sensibilidad a mayúsculas ni percent-encoding), **reescribe las imágenes remotas `http(s)` hacia un proxy propio del backend** para que el remitente nunca vea la IP del usuario y una imagen ya vista no se le vuelva a pedir, reutilizando conexiones para que las imágenes carguen de golpe (perezoso: solo se contacta al remitente cuando el usuario abre de verdad el correo, nunca al pre-cargarlo), corrige el mojibake de los correos que mienten sobre su codificación (UTF-8-first en Gmail, con la codificación declarada primero para las ASCII-enmascaradas tipo ISO-2022) y descarta los bloques pensados solo para Outlook de escritorio para no duplicar contenido; después lo muestra dentro de un iframe aislado que no puede ejecutar JavaScript ni tocar la sesión, y cachea el resultado ya saneado —**en la base de datos y en la memoria del navegador**— para que reabrir el mismo correo sea instantáneo (la reapertura en la misma sesión ni siquiera toca la red). La caché de la base de datos tiene un tiempo de vivencia deslizante, **ahora más corto**, que se reinicia con cada apertura (libera sola el contenido que llevas mucho sin abrir, recuperable bajo demanda), y la app pre-carga por adelantado —en segundo plano— el contenido de los no leídos recientes de la bandeja de entrada, **sin descargar sus imágenes**, para que abrirlos sea instantáneo. Las listas exactas de lo permitido y lo no soportado, el plazo del TTL, los límites del proxy y los topes de la pre-carga están en [../limits/visualizacion-de-correos.md](../limits/visualizacion-de-correos.md).
