# Listado de correos (inbox) — comportamiento (MVP)

Este documento describe **qué hace** la app cuando un usuario abre una bandeja y **qué experimenta** delante de la pantalla: qué correos ve, en qué orden, con qué indicadores por fila, y cómo cada acción que dispara desde la lista acaba en el buzón correcto. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se comporta la lista de correos.

Los topes concretos (tamaño de página, frescura del caché) y la lista exhaustiva de "qué NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/listado-de-correos.md](../limits/listado-de-correos.md)**. Este fichero solo los menciona de pasada y enlaza a ese catálogo cuando hace falta.

Fronteras con otras features (no se cubren aquí, tienen su propio documento):

- La **búsqueda de texto libre** (la lupa) opera sobre este mismo listado pero se documenta en [lupa.md](lupa.md).
- La vista de **Favoritos** y el toggle de la estrella se documentan en [favoritos.md](favoritos.md).
- Las **acciones por correo y en bloque** (marcar leído, mover a papelera, spam, restaurar) se documentan en [acciones-sobre-correos.md](acciones-sobre-correos.md).
- **Abrir un correo** y ver su cuerpo se documenta en [visualizacion-de-correos.md](visualizacion-de-correos.md).
- Las **bandejas ficticias** (vistas curadas por criterios) se documentan en [bandejas-ficticias.md](bandejas-ficticias.md).
- La **agrupación por conversación** (que es como se presentan hoy las filas en las bandejas reales y en la unificada) se documenta en [conversaciones.md](conversaciones.md).

Este documento se centra en el esqueleto: **la tabla de correos de una bandeja real**.

> **Importante — cómo leer este documento hoy.** En las bandejas reales y en la unificada, las filas se presentan **agrupadas por conversación**: cada fila es un **hilo** (no un mensaje suelto), es de **solo lectura** y su única acción es **abrir** la conversación. Eso cambia tres cosas que este documento describe en su forma "clásica" (un mensaje por fila): la fila **ya no** tiene casilla de selección, **ni** estrella clicable, **ni** barra de acciones masivas, y el total "X–Y de Z" cuenta **hilos**, no mensajes. Todo eso —y el visor que enseña la cadena completa— vive en [conversaciones.md](conversaciones.md). El esqueleto que sí sigue vigente tal cual (de dónde salen los correos, las bandejas reales, el orden, la paginación numerada, el enrutado de cada acción al buzón real) se describe aquí. La **única** superficie que conserva la fila clásica con selección, acciones masivas y estrella clicable es la pestaña de **Favoritos** ([favoritos.md](favoritos.md)), que no agrupa.

---

## 1. Qué es el listado y de dónde salen los correos

El listado es la tabla central de la app: la lista de correos de un buzón. Lo más importante de su comportamiento es que **lee únicamente de la base de datos local de MailManager** — no llama a Gmail ni a Outlook para construir la lista. Las consecuencias visibles son tres:

- **Es instantáneo.** Pintar la bandeja no espera a ningún proveedor; los correos ya están guardados localmente desde la última sincronización.
- **Funciona aunque el proveedor esté caído.** Si Gmail u Outlook no responden, la lista se sigue viendo con lo último que se sincronizó.
- **Puede ir "por detrás" de la realidad.** Lo que se ve es la última foto sincronizada, no el estado en vivo del buzón del proveedor. Por eso la app sincroniza en segundo plano al entrar en la bandeja (sección 6).

Quién mete correos en esa base de datos local es la **sincronización de metadata**, una operación distinta (su comportamiento se documenta como parte del flujo de sincronización). El listado solo **consume** lo que la sincronización ya dejó guardado: asunto, remitente, destinatario principal, fecha, estado de lectura, en qué bandeja está, si tiene adjuntos y si es favorito.

---

## 2. Las bandejas reales navegables

Cada correo está clasificado en exactamente **una** bandeja. El listado siempre se mira "a través de" una de estas:

- **Bandeja principal** — la bandeja de entrada real: lo recibido que sigue en la bandeja de entrada del proveedor y no es enviados, spam ni papelera. Es la bandeja por defecto y la más usada.
- **Enviados** — los correos que el usuario mandó desde sus cuentas.
- **Archivados** — correos sacados de la bandeja de entrada sin borrarlos (ver [acciones-sobre-correos.md](acciones-sobre-correos.md) § 6-bis). Es la bandeja más nueva.
- **Spam** — correo no deseado.
- **Papelera** — correos eliminados (movidos a la papelera, todavía recuperables).

Esta clasificación la decide la sincronización al traer cada correo, aplicando una **prioridad fija**: si un correo está en la papelera cuenta como papelera; si no, si está en spam cuenta como spam; si no, si es un enviado cuenta como enviado; si no, si **sigue en la bandeja de entrada** del proveedor cuenta como bandeja principal; y si un correo recibido **ya no está en la bandeja de entrada** del proveedor (pero tampoco en spam/papelera/enviados), cuenta como **archivado**. Esta prioridad existe porque un mismo correo puede llevar varias etiquetas en Gmail a la vez (un enviado también marcado como spam, por ejemplo) y la app necesita asignarle **una sola** bandeja sin ambigüedad.

### 2.1 Un correo nunca está en dos bandejas a la vez en la lista

Como la clasificación es excluyente, mirar "Enviados" no muestra correos que estén en la papelera aunque originalmente se enviaran. Mover un correo a la papelera —o archivarlo— lo saca de su bandeja anterior en la lista (la papelera recuerda la bandeja original para poder restaurarlo, pero el correo deja de aparecer donde estaba). Esto coincide con la intuición del usuario de Gmail y Outlook: la papelera "se lleva" el correo de donde estuviera, y archivar lo saca de la bandeja de entrada.

### 2.2 La bandeja principal ahora muestra solo la bandeja de entrada real

Al llegar la nueva clasificación, la bandeja principal cambia de comportamiento de forma visible. **Antes**, mostraba todo el correo recibido que no estuviera en enviados, spam ni papelera; eso incluía, sin querer, los correos que el usuario **ya había archivado directamente en Gmail / Outlook** —se "colaban" en la bandeja principal—. **Ahora** la bandeja principal muestra **solo la bandeja de entrada real**: los correos archivados en el proveedor pasan a la nueva vista "Archivados".

Es un cambio deseable (la bandeja de entrada pasa a ser de verdad la bandeja de entrada, como en Gmail/Outlook) pero visible: un usuario con correo archivado previo verá su bandeja principal "encoger" hacia su inbox real, con el resto reubicado en "Archivados". El cambio es **gradual por cuenta**: ocurre a medida que cada cuenta se sincroniza, porque la reclasificación la hace la sincronización al volver a traer cada correo.

### 2.3 Los correos borrados de verdad no aparecen en ninguna lista

Cuando un correo se elimina **permanentemente** desde la papelera, internamente no se borra de la base de datos de golpe: se marca como definitivamente eliminado. Ese estado **no es una de las bandejas navegables**, así que esos correos no aparecen en ningún listado — ni siquiera en la papelera. Para el usuario, han desaparecido. (El porqué de conservar la fila marcada en lugar de borrarla está ligado al manejo de papelera y se documenta en [acciones-sobre-correos.md](acciones-sobre-correos.md).)

---

## 3. Vista de una cuenta vs. vista unificada

MailManager agrupa varias cuentas de correo bajo un mismo **mailbox**. El listado se puede mirar de dos formas, y cada bandeja (principal, enviados, archivados, spam, papelera) existe en ambas:

- **Vista unificada del mailbox**: muestra los correos de **todas** las cuentas conectadas de ese mailbox, mezclados en una sola lista ordenada por fecha. Es la vista por defecto cuando el usuario entra al mailbox.
- **Vista de una cuenta concreta**: el usuario selecciona una de sus cuentas (mediante las pestañas de cuenta) y la lista se restringe **solo a esa cuenta**.

La diferencia no es solo de filtrado: cambia también qué columnas tienen sentido mostrar (sección 4.2). El resto del comportamiento — orden, indicadores, paginación — es idéntico en ambas vistas.

### 3.1 Cuando el mailbox no tiene cuentas

Si un mailbox no tiene ninguna cuenta conectada, la vista unificada devuelve una lista vacía de inmediato, sin error y sin llamar a la base de datos. Es el estado natural de un mailbox recién creado.

---

## 4. Cómo se ve cada fila

> Esta sección describe la fila **clásica** (un mensaje por fila), que hoy aplica tal cual a la pestaña de **Favoritos**. En las bandejas reales y en la unificada, las filas se agrupan por conversación: **conservan la casilla de selección** (que actúa sobre el mensaje más reciente del hilo) pero la **estrella** pasa a ser un indicador agregado de solo lectura — ver [conversaciones.md](conversaciones.md). El resto de lo que aquí se cuenta (proveedor, columnas "Para"/"De", clip de adjunto, fecha, los tres indicadores de estado) **sí** sigue aplicando a la fila-conversación, solo que con los indicadores **agregados** sobre el hilo en lugar de un único mensaje.

Cada correo es una fila. De izquierda a derecha el usuario ve: una casilla de selección, una estrella de favorito, el **proveedor** del que viene (un nombre amigable tipo "Gmail" / "Outlook"), una o dos columnas de personas ("Para" / "De"), el **asunto** (con un clip delante si trae adjuntos) y la **fecha**.

### 4.1 Los tres indicadores de estado por fila

Tres señales visuales resumen el estado de cada correo sin tener que abrirlo:

- **No leído.** Un correo sin leer se pinta en **negrita** y con un **fondo gris** que lo separa del resto; los leídos van en peso normal sobre fondo blanco. Es la señal más inmediata de "esto es nuevo / pendiente".
- **Clip de adjuntos.** Si el correo tiene al menos un adjunto **descargable**, aparece un icono de clip junto al asunto. Los correos que solo traen imágenes incrustadas en el cuerpo (logos, firmas con foto) **no** muestran clip — igual que Gmail y Outlook web. Hay un matiz deliberado: ese indicador arranca apagado y solo se enciende **la primera vez que alguien abre el correo** y la app descubre que tenía partes descargables. Es decir, justo después de sincronizar, un correo con adjuntos que nadie ha abierto puede no mostrar clip todavía. Es una simplificación consciente del MVP; el porqué completo está en [adjuntos.md](adjuntos.md).
- **Favorito.** Una estrella marcada indica que el correo es favorito. En la fila **clásica** (Favoritos) la estrella es **clicable directamente desde la lista** para marcar/desmarcar sin abrir el correo; en la **fila-conversación** la estrella es un indicador **agregado de solo lectura** (favorito si lo es algún mensaje del hilo) y el marcado se hace por mensaje dentro del visor ([conversaciones.md](conversaciones.md)). El favorito es **independiente** de la bandeja y del estado de lectura: un correo favorito sigue siéndolo aunque se mueva entre bandejas o se marque como leído. El detalle de ese toggle vive en [favoritos.md](favoritos.md).

#### Ejemplo

> En la bandeja principal unificada, el usuario ve tres filas: la primera en negrita con fondo gris y un clip (no leída, con adjunto), la segunda en gris claro sin clip (no leída, sin adjuntos descargables aún), y la tercera en blanco con la estrella encendida (leída y favorita). De un vistazo sabe qué atender primero.

### 4.2 Las columnas "Para" y "De" se adaptan al contexto

Aquí hay una asimetría pensada para no mostrar información redundante. La idea de fondo: en una bandeja de una sola cuenta, **el correo propio del usuario es siempre el mismo en todas las filas**, así que repetirlo en una columna no aporta nada.

- **En la vista de una cuenta concreta** se muestra **una sola** columna de personas:
  - En bandejas de **recepción** (principal, spam, papelera) se muestra **"De"**: quién te lo mandó. El "Para" sería siempre tu propia cuenta — redundante.
  - En **Enviados** se muestra **"Para"**: a quién se lo mandaste. El "De" serías siempre tú.
- **En la vista unificada** se muestran **ambas** columnas ("Para" y "De"), porque la lista mezcla correos de varias cuentas del usuario y hace falta distinguir cuál de tus cuentas está implicada en cada fila.

Un matiz sobre el destinatario: el listado muestra **solo el primer destinatario "Para"** de cada correo, no la lista completa. Los correos con varios destinatarios siguen teniendo sus CC/BCC en el proveedor, pero la columna de la tabla enseña únicamente el destinatario principal. Es una simplificación de visualización (una sola columna "Para"), no una pérdida de datos. El porqué está documentado en [../limits/buzones-y-vista-unificada.md](../limits/buzones-y-vista-unificada.md).

> Existe además un tercer modo de tabla, **mixto**, que decide "Para"/"De" fila a fila según la bandeja real de cada correo (no a nivel de toda la tabla). Hoy lo usa la vista de **Favoritos**, que mezcla correos recibidos y enviados en una misma lista. Se documenta en [favoritos.md](favoritos.md); el listado de bandejas reales que cubre este documento usa siempre uno de los dos modos anteriores.

### 4.3 La fecha es relativa al día

La columna de fecha muestra **la hora** (HH:MM) si el correo llegó hoy, y **el día y mes** (p. ej. "14 mar") si llegó otro día. Es el formato compacto habitual de un cliente de correo: lo de hoy se distingue por la hora, lo viejo por la fecha.

---

## 5. El orden de la lista

Los correos se muestran **ordenados por fecha de recepción, de más reciente a más antiguo**. Es el orden natural de una bandeja: lo último que llegó, arriba. Este orden es el mismo en todas las bandejas, en ambas vistas (cuenta y unificada), y también cuando hay una búsqueda activa (la lupa respeta este mismo orden — ver [lupa.md](lupa.md)).

**No hay ordenación por relevancia ni ningún otro criterio**: solo la fecha decide. No se puede reordenar por remitente, por asunto ni por tamaño.

### 5.1 Por qué el orden es estable incluso con fechas empatadas

Cuando dos correos comparten exactamente la misma fecha y hora de recepción —algo frecuente con notificaciones masivas enviadas en el mismo segundo—, la fecha sola no basta para decidir cuál va antes. Para que el orden sea **totalmente determinista** y no "baile" entre cargas, la app desempata de forma fija e invisible para el usuario. El efecto práctico: la lista no cambia de orden de forma aleatoria al recargar, y dos correos del mismo segundo siempre aparecen en la misma posición relativa. Es una garantía silenciosa pero importante; el porqué técnico (evitar duplicados o saltos al paginar) vive en [../limits/listado-de-correos.md](../limits/listado-de-correos.md).

---

## 6. Qué pasa al entrar en una bandeja

Cuando el usuario abre una bandeja, ocurren dos cosas casi a la vez:

1. **Se pinta de inmediato** lo que hay en la base de datos local (instantáneo, sección 1).
2. **Se lanza una sincronización en segundo plano** con el proveedor para traer lo que haya llegado nuevo. Mientras corre, la lista ya es visible y usable; cuando termina, la lista se refresca sola con los correos nuevos.

Esta sincronización automática se dispara al entrar al mailbox y al cambiar de cuenta. Corre de forma **silenciosa**: en las bandejas reales no hay un indicador visible de "sincronizando" (el botón de refresco de la cabecera es decorativo y no se anima); el usuario nunca ve una pantalla en blanco esperando, simplemente la lista aparece al instante y se actualiza sola cuando llega lo nuevo. (La vista de Favoritos sí tiene un botón "Sincronizar favoritos" que se anima, pero esa es una acción distinta — ver [favoritos.md](favoritos.md).)

### 6.1 Frescura del caché

Dentro de una misma sesión, si el usuario sale y vuelve a la bandeja en un intervalo corto, la app reutiliza la lista ya cargada en lugar de volver a pedirla, para que la navegación sea fluida. Pasado ese intervalo, la vuelve a pedir. El valor concreto de esa "ventana de frescura" está en [../limits/listado-de-correos.md](../limits/listado-de-correos.md). Las **bandejas ficticias** usan una política de frescura distinta (más agresiva), documentada en [bandejas-ficticias.md](bandejas-ficticias.md).

---

## 7. Cómo se navega: páginas numeradas, no scroll infinito

El listado se recorre **por páginas numeradas**, no con scroll infinito ni con un botón de "cargar más". Cada página muestra un bloque fijo de correos (los más recientes primero); el tamaño de página exacto está en [../limits/listado-de-correos.md](../limits/listado-de-correos.md). Debajo de la tabla aparece una **barra de paginación** con tres elementos:

- **"Anterior" / "Siguiente"**, para moverse una página atrás o adelante. Quedan **deshabilitados** en los extremos (en la primera página no se puede ir atrás; en la última, adelante).
- Un indicador **"X–Y de Z"** (por ejemplo, `1–50 de 1.234`) que dice qué rango se está viendo y, sobre todo, **cuántos elementos hay en total** en esa bandeja/vista. Los miles se agrupan con punto. En las bandejas que **agrupan por conversación**, "Z" cuenta **hilos** (no mensajes sueltos) y una conversación nunca se parte entre dos páginas — ver [conversaciones.md](conversaciones.md); en Favoritos, "Z" cuenta mensajes.
- **Números de página** clicables que saltan directamente a una página concreta. Cuando hay muchas páginas, los tramos intermedios se resumen con puntos suspensivos (`1 … 4 5 6 … 25`), mostrando siempre la primera, la última y una ventana alrededor de la actual.

Esto cambia un comportamiento anterior del MVP: antes la lista solo dejaba ver un primer bloque de los correos más recientes y **los siguientes no eran alcanzables** desde la interfaz. Ahora el usuario puede **recorrer página a página todo lo que la app tiene sincronizado** de esa bandeja. En la práctica se ven **más correos que antes**, no menos.

Una sutileza importante: ese total "Z" es el de la **copia local** sincronizada, no el del buzón real en el proveedor (ver § 1). Si Gmail tiene decenas de miles de correos pero la app solo ha sincronizado los más recientes, el indicador muestra el total **sincronizado**. El motivo —que ni Gmail ni Outlook permiten pedir "la página N" ni un total exacto en vivo, así que la paginación numerada se construye sobre la copia local— está en [../limits/listado-de-correos.md](../limits/listado-de-correos.md).

### 7.1 La página vive en la URL y se reinicia al cambiar de contexto

La página actual se guarda en la dirección (`?page=...`), igual que el término de búsqueda. Recargar o compartir el enlace conserva la página. Al **cambiar de bandeja, de cuenta o de búsqueda**, la navegación **vuelve a la página 1** automáticamente: el contexto cambió, así que empezar por el principio es lo predecible (y evita aterrizar en una página que no existe para el nuevo filtro).

### 7.2 Si la página deja de existir, la app reencuadra a la última válida

Como la paginación es por posición, el número total de páginas puede encogerse mientras el usuario navega — por ejemplo, tras una sincronización en segundo plano o un borrado masivo. Si el usuario está en la página 5 y, de pronto, ya no hay tantas páginas, la app lo lleva sola a la **última página válida** en lugar de mostrar una página vacía. Una bandeja que se queda sin correos aterriza en la página 1.

### 7.3 Mientras carga la página siguiente, no parpadea

Al pulsar "Siguiente" o un número, la página que ya se veía **se mantiene en pantalla** hasta que llega la nueva, en lugar de vaciarse y mostrar un spinner. Los controles de paginación se deshabilitan momentáneamente durante esa transición. Es una navegación fluida, sin saltos de "tabla en blanco" entre páginas.

> Aclaración para evitar confusión: la selección múltiple y la casilla de "seleccionar todo" de la cabecera **solo existen hoy en la pestaña de Favoritos** (la única que no agrupa por conversación); las bandejas en modo conversación no tienen selección por fila (ver [conversaciones.md](conversaciones.md)). Donde existe, la casilla de "seleccionar todo" marca los correos de **la página actual** (no "todos los de todas las páginas"); la cifra exacta de cuántos caben en una página está en [../limits/listado-de-correos.md](../limits/listado-de-correos.md). Es un límite de **selección por página**, no de cuántos correos existen. Eso sí, la selección **se conserva al cambiar de página**, de modo que una acción en bloque puede afectar a correos marcados en páginas distintas. La selección y las acciones en bloque se documentan en [acciones-sobre-correos.md](acciones-sobre-correos.md).

---

## 8. Cada acción va al buzón real del correo (no al de la URL)

Esta es la regla menos obvia y la más importante del listado en un entorno multi-cuenta.

Cada fila sabe a qué **mailbox real** pertenece su correo. Cuando el usuario dispara una acción desde la lista —abrir el contenido, marcar leído/no leído, marcar favorito, mover a la papelera, marcar como spam, descargar un adjunto—, esa acción se enruta al mailbox y la cuenta **reales del correo**, no al mailbox que aparezca en la dirección de la página.

En una bandeja normal esto da igual, porque el correo pertenece al mismo mailbox que se está mirando. Pero cobra sentido en las **bandejas ficticias**, donde una sola vista puede mezclar correos de **mailboxes distintos** (ver [bandejas-ficticias.md](bandejas-ficticias.md)). Allí, usar el mailbox de la URL para operar sobre un correo de otro mailbox haría que el backend rechazara la operación (el correo "no pertenece" a ese mailbox). Cada fila lleva su propia pertenencia precisamente para que cualquier acción acierte siempre el buzón correcto.

Lo mismo aplica a las **acciones en bloque**: si el usuario selecciona correos de varios mailboxes a la vez, la app los agrupa por mailbox y lanza una operación por grupo, en lugar de una sola operación contra el mailbox de la URL. El detalle de esa repartición está en [acciones-sobre-correos.md](acciones-sobre-correos.md).

#### Ejemplo

> En una bandeja ficticia que reúne correos de la cuenta de Gmail (mailbox A) y la de Outlook (mailbox B), el usuario marca como favorito un correo que en realidad vive en el mailbox B. La estrella se enruta al mailbox B —el real del correo— y funciona, aunque la URL que está mirando sea la de la bandeja ficticia. Si la app hubiera usado el mailbox de la URL, la operación habría fallado.

---

## 9. Estados visibles de la lista

- **Cargando (primera vez):** mientras llega la primera carga, se muestra un spinner centrado en lugar de la tabla.
- **Sincronizando (en segundo plano):** la lista ya visible se mantiene intacta y usable; el refresco con el proveedor ocurre de forma silenciosa, sin indicador visible en estas bandejas. Cuando la sincronización termina, la lista se reemplaza sola con los correos nuevos.
- **Bandeja vacía:** si no hay correos, se muestra "No hay correos en esta bandeja" y **sin barra de paginación** (no tiene sentido paginar cero correos).
- **Búsqueda sin resultados:** si hay una búsqueda activa y no casa nada, el mensaje es **distinto** — "No se encontraron correos para tu búsqueda" — para que el usuario distinga "no hay nada que coincida" de "la bandeja está vacía", y tampoco hay barra de paginación. (La lupa: [lupa.md](lupa.md).)
- **Una sola página:** si todos los correos caben en una página, la barra muestra el rango ("1–37 de 37") pero "Anterior/Siguiente" quedan deshabilitados; no hay nada a lo que saltar.
- **Error:** si la carga falla, se muestra un mensaje de error legible en lugar de una pantalla rota; la lista no se pinta a medias y la barra de paginación no aparece.

---

## 10. Resumen en una frase

> El listado es la tabla de una bandeja real (principal, enviados, archivados, spam o papelera) que lee **solo de la base de datos local** —instantánea y resistente a caídas del proveedor—, mezcla todas las cuentas en vista unificada o se restringe a una en vista de cuenta, ordena siempre por fecha de recepción descendente con un desempate estable, marca por fila lo no leído, los adjuntos descargables y los favoritos, adapta las columnas "Para"/"De" al contexto para no repetir el correo propio del usuario, sincroniza en segundo plano al entrar, se navega **por páginas numeradas** con barra "Anterior/Siguiente" e indicador "X–Y de Z" (total exacto de lo **sincronizado**, no del proveedor en vivo) reiniciando a la página 1 al cambiar de contexto y reencuadrando a la última página válida si el total encoge, y enruta cada acción al mailbox **real** de cada correo para acertar siempre el buzón incluso cuando una vista mezcla varios; las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/listado-de-correos.md](../limits/listado-de-correos.md).
