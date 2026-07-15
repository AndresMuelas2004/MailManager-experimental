# Vista de conversaciones — comportamiento (MVP)

Este documento describe **qué hace** la app cuando agrupa los correos por conversación (hilo) y **qué experimenta** el usuario delante de la pantalla: cómo se colapsan los mensajes de un hilo en una sola fila del listado, qué muestra esa fila, y cómo el visor pasa a enseñar la cadena completa del hilo en lugar de un único mensaje. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se comporta la agrupación por conversación.

**No hay ninguna pantalla nueva.** Esta funcionalidad cambia cómo se **presentan** las filas y el visor dentro de los listados que ya existen (la bandeja de cuenta, la unificada y las ficticias). El modelo de buzón ([buzones-y-vista-unificada.md](buzones-y-vista-unificada.md)), el orden, la lupa y la paginación del listado ([listado-de-correos.md](listado-de-correos.md)) se mantienen; lo que cambia es la unidad: ahora cada fila es **un hilo**, no un mensaje suelto.

Los topes concretos (cuántos mensajes trae cada llamada al proveedor, qué cuenta exactamente el contador, las asimetrías Gmail/Outlook del hilo) y la lista exhaustiva de "qué NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/conversaciones.md](../limits/conversaciones.md)**. Este fichero solo los menciona de pasada y enlaza a ese catálogo cuando hace falta.

Fronteras con otras features (no se cubren aquí, tienen su propio documento):

- El **listado** en el que se montan las filas-conversación (orden, paginación, columnas "Para"/"De", enrutado de acciones al buzón real) se documenta en [listado-de-correos.md](listado-de-correos.md).
- El **render del cuerpo** de cada mensaje del hilo (HTML saneado, imágenes embebidas, caché) es exactamente el mismo de siempre y se documenta en [visualizacion-de-correos.md](visualizacion-de-correos.md).
- Las **acciones por mensaje** (favorito, papelera, spam, archivar/desarchivar, leído/no leído) se documentan en [acciones-sobre-correos.md](acciones-sobre-correos.md) y [favoritos.md](favoritos.md); aquí solo se explica **desde dónde** se disparan ahora (dentro del visor).
- **Responder / Responder a todos / Reenviar** sobre el hilo se apoyan en [responder-y-reenviar.md](responder-y-reenviar.md).

---

## 1. Qué problema resuelve

Antes, cada correo era una **fila independiente** del listado aunque perteneciera a la misma conversación: un intercambio de seis mensajes ("Re: …", "Re: Re: …") ocupaba seis filas dispersas, y al abrir cualquiera de ellas el visor enseñaba **solo ese mensaje**, sin contexto de los anteriores. El usuario tenía que reconstruir el hilo mentalmente saltando entre filas.

Con esta funcionalidad, todos los mensajes de una misma conversación se **colapsan en una sola fila** del listado (al estilo de Gmail o de la vista de conversación de Outlook), y al abrirla el visor muestra **toda la cadena de mensajes** ordenada. La vista de conversación está **siempre activa** en las bandejas indicadas (§ 4): no hay un interruptor para apagarla.

---

## 2. La fila-conversación en el listado

En las bandejas que agrupan (§ 4), cada conversación se representa con **una sola fila**, que toma como "cara visible" el **mensaje más reciente del hilo presente en esa bandeja**. La fila muestra:

- **El asunto del hilo**, normalizando los prefijos "Re:" / "Fwd:" (se enseña el asunto base, sin la pila de prefijos). Un asunto que quede vacío tras quitar los prefijos se muestra como "(Sin asunto)".
- **El remitente** del mensaje más reciente y **su fecha** (con el mismo formato relativo del listado: la hora si es de hoy, el día y mes si no).
- **Un contador** con el número de mensajes que el hilo tiene **en esa bandeja** (ver § 5). El contador solo se dibuja cuando el hilo tiene **más de un** mensaje en la bandeja: una conversación de un solo mensaje se ve como una fila normal, sin número.
- **Estado no leído agregado**: si **cualquiera** de los mensajes del hilo (en esa bandeja) está sin leer, la fila se muestra en **negrita** (y con el fondo gris de "no leído"), igual que hace Gmail.
- **Indicadores agregados**: el **clip** de adjunto aparece si **algún** mensaje del hilo lleva adjunto descargable; la **estrella** de favorito aparece rellena si **algún** mensaje del hilo está marcado como favorito.

### 2.1 La fila: selección + abrir, pero estrella de solo lectura

En modo conversación, los indicadores de la fila (negrita, clip, estrella, contador) son **informativos / de solo lectura**, y pulsar sobre la fila **abre** la conversación. Pero la fila **conserva la selección** en las bandejas estándar que agrupan (la de una cuenta y la unificada): sigue habiendo **casilla de selección por fila** y **casilla de "seleccionar toda la página"** en la cabecera, y al marcar una o más aparece la **barra de acciones masivas** (mover a papelera, marcar spam, marcar leído/no leído). La casilla **detiene la propagación**, así que marcarla no abre la conversación.

Como la fila representa el hilo por su **mensaje más reciente** (el mismo que usan Responder / Reenviar, § 7), las acciones masivas operan sobre **ese mensaje representante**, no sobre el hilo entero. Para un hilo de un solo mensaje —la inmensa mayoría del listado— es exactamente el mensaje esperado; en hilos largos, el resto de mensajes se gestiona **por mensaje** desde el visor.

Lo que la fila **no** recupera respecto al listado clásico de un mensaje por fila:

- **Estrella clicable**: la estrella es un indicador agregado de solo lectura, no un botón. El favorito se alterna **por mensaje** desde el visor (§ 7).
- **Acción sobre el hilo completo de un clic**: no existe; las acciones masivas tocan el mensaje representante, y el resto del hilo se maneja en el visor.

> **Bandejas ficticias (virtuales): fila totalmente de solo lectura.** A diferencia de la bandeja de cuenta y de la unificada, la bandeja ficticia **no** ofrece selección ni acciones masivas: su fila solo informa y abre. Reúne cuentas de varios buzones reales sin una "caja" única, así que una barra de acciones masivas no tendría un buzón al que dirigirse de forma coherente (ver [bandejas-ficticias.md](bandejas-ficticias.md)). Allí las acciones se hacen **por mensaje** en el visor.

> **Favoritos conserva la fila clásica.** La pestaña de Favoritos **no** agrupa por conversación (§ 4): un mensaje por fila, con su casilla de selección, su barra de acciones masivas y su **estrella clicable**.

#### Ejemplo

> En la bandeja unificada, una conversación de 4 mensajes (3 leídos, 1 sin leer, uno con un PDF adjunto) se ve como **una fila** en negrita, con un "4" junto al asunto base y el clip. A su izquierda **sí** hay una casilla de selección (la estrella, en cambio, es solo indicador). Si el usuario la marca y pulsa "Mover a papelera", se mueve el **mensaje más reciente** del hilo; el resto se gestiona abriendo la conversación. Pulsar en cualquier otra parte de la fila abre el visor.

---

## 3. El visor: la cadena completa de la conversación

Al abrir una fila-conversación, el visor deja de mostrar un único mensaje y pasa a mostrar **la cadena completa** del hilo dentro de una ventana modal:

- Los mensajes se presentan en **orden cronológico ascendente**: el **más antiguo arriba**, el **más reciente abajo** (estilo Gmail).
- **El mensaje que abriste** (la fila que pulsaste) **aparece expandido de inmediato**, con su cuerpo visible, **sin esperar** a que se cargue el resto de la cadena: su cuerpo se sirve de la caché precalentada del navegador (ver [visualizacion-de-correos.md](visualizacion-de-correos.md) § 7.5). Los **demás mensajes aparecen colapsados** (solo su cabecera) y **la cadena se va completando** conforme llega del proveedor, con un **indicador discreto de "cargando la conversación"** al pie mientras tanto — ya **no** un spinner a pantalla completa que bloquee la ventana. El usuario puede colapsar/expandir cualquier mensaje libremente. (Antes se expandía por defecto el mensaje más reciente del hilo; ahora es el que abriste, que suele ser el más reciente de esa bandeja pero puede no ser el más reciente absoluto si el hilo tiene mensajes más nuevos en otra bandeja.)
- La **cabecera del modal** muestra el asunto base del hilo y los botones **Responder / Responder a todos / Reenviar**, que operan sobre el mensaje más reciente (§ 7).
- Cada mensaje colapsado muestra en su cabecera el **remitente** (De), la **fecha**, su estado de no leído (un punto), su estrella si es favorito y, cuando el mensaje vive **fuera** de la bandeja de entrada, una pequeña etiqueta de ubicación ("Enviado", "Archivado", "Spam", "Papelera"). Al expandirse añade el destinatario ("Para"), su **cuerpo** renderizado y sus **adjuntos descargables**.

### 3.1 El cuerpo se carga al expandir (carga perezosa)

El cuerpo de cada mensaje **no** se descarga al abrir la conversación, **con una excepción: el mensaje que abriste**, cuyo cuerpo se pinta **de inmediato** desde la caché precalentada del navegador (por eso abrir un correo del inbox ya no espera a que se reconstruya toda la cadena — § 3). El de los **demás** mensajes se carga **en el momento de expandir** cada uno. Así, abrir una conversación larga no dispara una descarga masiva de todos los cuerpos a la vez; cada uno de los otros cuerpos se baja la primera vez que el usuario despliega su mensaje (con su breve spinner), y a partir de ahí se sirve cacheado igual que en el visor normal. El render de cada cuerpo —saneamiento del HTML, imágenes embebidas, iframe aislado, caché— es **exactamente** el de [visualizacion-de-correos.md](visualizacion-de-correos.md); la conversación no cambia nada de eso, solo decide **cuándo** se pide cada cuerpo.

> Como cada mensaje arranca con su adjunto sin descubrir, **dentro del visor de conversación no se muestra el clip en la cabecera colapsada** de cada mensaje (sería un indicador muerto). Los adjuntos descargables de un mensaje aparecen como tarjetas cuando se **expande** y su cuerpo se carga (es entonces cuando se descubren). El clip **sí** aparece, agregado, en la fila del listado (§ 2). Es la misma estrategia "lazy" de adjuntos descrita en [adjuntos.md](adjuntos.md).

### 3.2 Las acciones por mensaje viven dentro de cada mensaje expandido

Cada mensaje expandido de la cadena trae sus **propios** controles de acción, justo encima de su cuerpo: **Favorito**, **No leído**, **Spam**, **Papelera** y —según dónde esté el mensaje— **Archivar** (solo si está en la bandeja de entrada) o **Desarchivar** (solo si ya está archivado). Son las mismas acciones de siempre ([acciones-sobre-correos.md](acciones-sobre-correos.md), [favoritos.md](favoritos.md)), pero aplicadas **a ese mensaje concreto**, no al hilo entero. Este visor por mensaje es además la **única** vía para archivar/desarchivar desde una bandeja ficticia. No hay "marcar como leído": abrir la conversación ya marca todo el hilo como leído (§ 7), así que el botón ofrecido es el inverso, "No leído".

Cada acción se enruta al **buzón y la cuenta reales de ese mensaje** (que la conversación lleva consigo en cada elemento de la cadena), nunca al buzón de la URL. Esto importa porque una conversación abierta desde una **bandeja ficticia multi-buzón** puede contener mensajes que viven en **mailboxes distintos**; usar el buzón de la pantalla fallaría con "cuenta no encontrada". Es la misma garantía de "cada acción al buzón real" del listado ([listado-de-correos.md](listado-de-correos.md) § 8), trasladada a la cadena del visor.

---

## 4. En qué bandejas se agrupa (y dónde NO)

La vista de conversación se activa en:

- **La bandeja de una cuenta concreta** (entrada, enviados, spam, papelera).
- **La bandeja unificada** del buzón (que combina varias cuentas).
- **Las bandejas ficticias** (vistas virtuales definidas por el usuario), que agrupan **siempre** por conversación.

**Excepción — Favoritos NO se agrupa.** La pestaña de Favoritos mezcla mensajes recibidos y enviados en el mismo listado con una semántica especial por fila (el modo "mixto" que resuelve "Para"/"De" fila a fila — ver [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) § 4.2), lo que choca con la agrupación por conversación. Favoritos **se mantiene tal cual está hoy**: un mensaje por fila, sin agrupar, conservando selección, acciones masivas y estrella clicable.

### 4.1 La agrupación es por cuenta: dos cuentas nunca se fusionan

La identidad de un hilo la define **cada proveedor por cuenta** (Gmail con su `threadId`, Outlook con su `conversationId`), y esos identificadores son **propios de cada cuenta**. Por eso la agrupación es **por cuenta**: dos cuentas distintas que participan en un mismo intercambio **no se fusionan** en una sola conversación, ni siquiera en la vista unificada o en una bandeja ficticia. Cada cuenta enseña su propio hilo. Es una decisión deliberada para no mezclar hilos que el proveedor considera separados; el porqué técnico está en [../limits/conversaciones.md](../limits/conversaciones.md).

Hay un único matiz en las **bandejas ficticias**: si el usuario conectó **la misma cuenta de proveedor bajo dos buzones distintos** (dos "cuentas" de la app que apuntan al mismo buzón real), el mismo mensaje podría aparecer duplicado; la app lo **colapsa** y muestra el hilo una sola vez. Es la misma deduplicación que ya hacían las bandejas ficticias ([bandejas-ficticias.md](bandejas-ficticias.md) § 7.1), ahora aplicada a nivel de hilo.

### 4.2 Correos sin hilo reconocible

Un correo cuyo identificador de hilo esté **ausente** se trata como una **conversación de un solo mensaje**: se muestra como una fila individual (sin contador) y **nunca** se fusiona con otros correos sueltos. Abrir esa fila enseña ese único mensaje. Es decir, "sin hilo" se comporta exactamente como "un solo mensaje".

---

## 5. Qué significa el contador (y por qué el visor puede mostrar más)

El contador de cada fila cuenta los **mensajes del hilo presentes en la bandeja que se está mirando**, entre los que la app tiene **sincronizados localmente**. No cuenta el total absoluto de la conversación incluyendo otras bandejas ni otras cuentas.

- Ejemplo: una conversación con 4 mensajes recibidos (en Entrada) y 2 respuestas tuyas (en Enviados) muestra el contador **4** en la bandeja de Entrada y **2** en Enviados. Son dos filas distintas, una en cada bandeja, cada una con su parte del hilo.
- El **visor sí puede mostrar más** mensajes que el contador de la fila. Al abrir la conversación, la app trae del proveedor **toda** la cadena del hilo (§ 6), incluidos mensajes de otras bandejas o que la app nunca había sincronizado. Esta asimetría entre "lo que cuenta la fila" (su bandeja, lo sincronizado) y "lo que muestra el visor" (el hilo completo del proveedor) es **intencionada y aceptada**.

#### Ejemplo

> En la bandeja de Entrada, una fila-conversación muestra el contador "2" (dos mensajes recibidos en Entrada). El usuario la abre y el visor enseña **5** mensajes: los 2 de Entrada, 2 respuestas que él mandó (que viven en Enviados) y 1 mensaje antiguo del hilo que la app nunca había llegado a sincronizar. El "2" de la fila y el "5" del visor son ambos correctos: cuentan cosas distintas.

---

## 6. Conversación completa y fiel (incluye mensajes no sincronizados)

La app solo conserva sincronizada una porción reciente del buzón, así que una conversación antigua puede tener mensajes que nunca guardó. Para que el visor muestre la conversación **completa y fiel**, al abrirla la app **pide al proveedor (Gmail / Outlook) todos los mensajes del hilo**, incluidos los que no tenía guardados localmente y los que viven en otras bandejas (Enviados, Spam, Papelera).

- Los mensajes traídos se muestran con su **estado fresco del proveedor** (en qué bandeja están, si están leídos, si son favoritos) en ese mismo momento — así, un mensaje que se movió de carpeta o se leyó desde otro dispositivo se refleja al instante al abrir la conversación.
- Esos mensajes se **persisten** (se guardan en la copia local) la primera vez que se piden. El objetivo es que **al reabrir la conversación** se sirvan desde la base de datos sin volver a reconstruir el hilo de cero; aun así, cada apertura hace una **comprobación ligera** contra el proveedor para detectar mensajes nuevos del hilo que hayan llegado después (no se sirve una foto vieja de hace 30 s — la frescura aquí es máxima, igual que en las bandejas ficticias).
- Esa persistencia es **best-effort**: si guardar falla, el visor **se abre igual** (se construye con lo que el proveedor acaba de devolver). Lo único que se pierde en ese caso es la aceleración de la próxima apertura, no la conversación.

> **Reaperturas en Outlook: sin duplicados y con acierto de caché.** Outlook entrega el **mismo** mensaje con un **identificador distinto** según por dónde se pida (la sincronización de la bandeja y la reconstrucción del hilo usan endpoints distintos, y sus identificadores no coinciden — ni siquiera forzando el identificador "inmutable"). Para que reabrir una conversación de Outlook no **duplique** filas ni fuerce una segunda descarga del cuerpo, al guardar el hilo la app **empareja cada mensaje por su identidad física** —su fecha de envío, su remitente y su asunto— con la fila que ya tenía almacenada, en lugar de fiarse del identificador del proveedor. Así, reabrir **actualiza** la fila existente y sirve el cuerpo cacheado (acierto de caché), en vez de **insertar** una fila nueva y volver a descargar. **La cadena del visor usa esos mismos identificadores estables**: expandir cualquier mensaje del hilo, marcarlo favorito, moverlo o responder desde el visor encuentra siempre su copia local — sin errores de "correo no encontrado" ni acciones que parezcan no hacer nada por apuntar a un identificador que solo existía en el endpoint de conversación. En Gmail los identificadores son estables, así que esto no cambia nada. **Limitación aceptada:** las filas duplicadas que dejaron aperturas **anteriores** (previas a esta corrección) **no** se limpian solas; solo se evita crear nuevas (ver [../limits/conversaciones.md](../limits/conversaciones.md) § 7).

### 6.1 Cómo afecta a la fila ya visible (paginación estable)

Cuando, al abrir una conversación, la app descarga y guarda mensajes antiguos de **ese mismo hilo y esa misma bandeja**, esos mensajes se **integran en la fila-conversación que ya estaba visible**: no aparece una fila nueva en la bandeja actual, sino que **la cuenta de esa fila se actualiza**. El número de conversaciones de la página y el corte entre páginas **se mantienen estables** (una conversación nunca se parte entre dos páginas — § 8). Si el hilo tuviera mensajes en **otra** bandeja (típicamente Enviados), esos sí pueden reflejarse como (o sumarse a) la fila correspondiente de **esa otra** bandeja la próxima vez que se mire.

Por eso, tras abrir una conversación, la app **refresca los listados** en segundo plano: el contador de la fila y los indicadores agregados pueden cambiar al integrarse los mensajes recién traídos.

---

## 7. Acciones sobre la conversación

- **Marcar como leído al abrir.** Al abrir una conversación, **todos** sus mensajes no leídos se marcan como leídos (como Gmail), de una sola vez, de modo que la fila deja de estar en negrita de inmediato. Si el hilo ya estaba todo leído, no se hace ninguna llamada. El marcado se enruta al buzón real de cada mensaje (un hilo de una bandeja ficticia puede cruzar varios mailboxes), agrupando por buzón.
- **Responder / Responder a todos / Reenviar.** Operan, por defecto, sobre el **mensaje más reciente** del hilo (continuar la conversación), y se lanzan desde los botones de la cabecera del visor. El threading y la herencia de adjuntos son los de [responder-y-reenviar.md](responder-y-reenviar.md).
- **Favorito, mover a papelera, mover a spam, archivar/desarchivar, marcar no leído, descargar adjuntos.** Se realizan **por mensaje, dentro del visor** (§ 3.2): cada mensaje de la cadena mantiene sus acciones individuales.

En esta primera versión **no** existen acciones que afecten a la conversación **entera** de una sola vez (marcar todo el hilo desde un botón, borrar la conversación completa): eso queda **fuera del alcance inicial**. La única acción que sí abarca todo el hilo es el marcado automático de "leído" al abrir.

---

## 8. Paginación coherente: las páginas cuentan hilos

El listado sigue navegándose **por páginas numeradas** con su indicador "X–Y de Z" ([listado-de-correos.md](listado-de-correos.md) § 7), pero en modo conversación cambia **qué** se cuenta:

- **Z cuenta conversaciones (hilos), no mensajes sueltos.** El total exacto que muestra la barra es el número de hilos distintos de esa bandeja (en la copia local sincronizada).
- Cada página muestra un número fijo de **conversaciones** (el mismo tamaño de página del listado — la cifra está en [../limits/listado-de-correos.md](../limits/listado-de-correos.md)).
- **Una misma conversación nunca queda partida entre dos páginas.** El colapso por hilo se hace **antes** de cortar la página, así que cada página viene llena de hilos completos y el "X–Y de Z" cuadra.

El resto del comportamiento de paginación (la página en la URL, el reinicio a la página 1 al cambiar de contexto, el reencuadre a la última página válida si el total encoge) es idéntico al del listado general.

---

## 9. Casos particulares contemplados

- **Conversación repartida entre varias bandejas** (lo habitual: original en Entrada, respuesta en Enviados): cada bandeja muestra su parte del hilo con su propio contador; el visor reconstruye la conversación completa (§ 5, § 6).
- **Conversación repartida entre varias cuentas** (unificada / ficticia): la agrupación es **por cuenta**; dos cuentas distintas no se fusionan en un solo hilo (§ 4.1).
- **Mismo correo bajo dos buzones** (misma cuenta de proveedor conectada dos veces, solo posible en ficticias): se colapsa y se muestra el hilo **una sola vez** (§ 4.1).
- **Correo sin hilo reconocible**: fila individual de un solo mensaje, nunca fusionada con otros sueltos (§ 4.2).
- **Conversación de un solo mensaje**: se comporta como hoy — una fila sin contador, y el visor muestra ese único mensaje **sin** llamar al proveedor (no hay hilo que reconstruir).
- **La persistencia del hilo falla al abrir**: el visor se abre igual con el estado fresco del proveedor; solo se pierde la aceleración de la próxima apertura (§ 6).
- **La carga de la cadena falla al abrir** (fallo del proveedor al reconstruir el hilo): el **correo que abriste sigue visible** —su cuerpo ya se había pintado desde la caché— y solo aparece un **aviso discreto** de que no se pudo cargar el resto de la conversación, en lugar de reemplazar toda la ventana por un error como ocurría antes (§ 3). **El hilo se marca igualmente como leído**: como el cuerpo del correo abierto sí se ha leído, el marcado del § 7 se dispara con ese mensaje aunque la cadena no llegara — la fila no se queda en negrita por un fallo transitorio del proveedor.
- **Un borrador de respuesta en curso pertenece al hilo** (empezaste a responder desde la web de Gmail/Outlook y lo dejaste a medias): ese borrador **no aparece** como mensaje de la cadena. Los borradores viven en su propia superficie de la app y el hilo solo muestra mensajes reales (enviados o recibidos).
- **Un mensaje del hilo no se puede interpretar** al traerlo del proveedor: se omite ese mensaje y el resto de la cadena se muestra con normalidad (un mensaje corrupto no tumba la conversación).

---

## 10. Qué NO incluye esta versión

Para fijar expectativas (la lista completa con el porqué de cada límite está en [../limits/conversaciones.md](../limits/conversaciones.md)):

- **No hay acciones masivas sobre el hilo entero** (marcar toda la conversación con un botón, borrarla completa de una vez). Las acciones son por mensaje, dentro del visor.
- **No hay interruptor de usuario** para activar/desactivar la vista de conversación: está siempre activa en las bandejas indicadas.
- **No se re-sincroniza masivamente el histórico**: la conversación completa se trae **bajo demanda** al abrir cada hilo, no de golpe para todo el buzón.
- **No se fusionan cuentas distintas** en un mismo hilo, aunque participen en el mismo intercambio real.
- **Los borradores del hilo no se muestran** en la cadena del visor (§ 9): un borrador a medias se gestiona desde la superficie de borradores, no desde la conversación.
- **Favoritos no se agrupa** por conversación (§ 4).

---

## Resumen en una frase

> La vista de conversación colapsa todos los mensajes de un mismo hilo (por cuenta, nunca fusionando cuentas distintas) en **una sola fila de solo lectura** —asunto base sin prefijos, remitente y fecha del más reciente, contador de los mensajes del hilo en esa bandeja, y negrita / clip / estrella agregados— en la bandeja de cuenta, la unificada y las ficticias (Favoritos queda fuera y conserva su fila clásica con selección y estrella clicable); al abrirla, el visor pide al proveedor la cadena completa del hilo (incluidos mensajes de otras bandejas y no sincronizados), la muestra en orden cronológico ascendente con el mensaje que abriste expandido y pintado al instante desde la caché precalentada (los demás colapsados y con carga perezosa, rellenándose por debajo sin bloquear la ventana; si la cadena falla, el correo abierto sigue visible), marca todo el hilo como leído de golpe, y ofrece Responder/Reenviar sobre el mensaje más reciente más las acciones por mensaje (favorito, papelera, spam, no leído) dentro de la cadena, cada una enrutada al buzón real de su mensaje; las páginas cuentan **hilos** (nunca partidos entre páginas) y el contador de la fila puede ser menor que lo que enseña el visor —asimetría intencionada—. Las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/conversaciones.md](../limits/conversaciones.md).
