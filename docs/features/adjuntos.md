# Adjuntos — comportamiento (MVP)

Este documento describe **qué hace** la app respecto a los archivos adjuntos: cómo se reciben, cómo se ven, cómo se añaden a un borrador, cómo se envían y qué pasa cuando algo falla. No entra en código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

Los topes numéricos exactos (tamaños, cantidades, TTL, reintentos, concurrencia) y la lista de "lo que NO hace" viven en un documento aparte para no repetir cifras aquí: **[../limits/adjuntos.md](../limits/adjuntos.md)**. Este fichero solo menciona los límites de pasada y enlaza a ese catálogo cuando hace falta.

---

## 1. Qué soporta MailManager respecto a adjuntos

La app trata dos tipos de "archivo dentro de un correo":

- **Imágenes embebidas en el cuerpo HTML** (referenciadas con `cid:` desde un `<img>` o desde CSS). Ya estaban soportadas antes de esta feature: el correo se ve correctamente con su logo, su firma con foto, los banners del newsletter, etc. Esa lógica **no cambia** con los adjuntos.
- **Archivos adjuntos descargables** (PDFs, documentos de Word/Excel, imágenes sueltas, ZIPs, etc.). Esto es la parte que añade la feature.

La app cubre tres flujos completos:

1. **Recibir** correos con adjuntos y poder verlos / descargarlos.
2. **Componer** correos nuevos con adjuntos.
3. **Editar borradores** añadiendo, quitando y conservando adjuntos entre sesiones, incluido responder y reenviar.

Funciona igual para cuentas Gmail y para cuentas Outlook, salvo en los puntos donde un proveedor impone un comportamiento distinto (esos puntos están marcados a lo largo del documento — son la parte más interesante).

---

## 2. Recibir y ver adjuntos

Lo más importante de la recepción: **el binario de un adjunto solo se descarga del proveedor cuando el usuario lo clica explícitamente**, nunca antes. El proceso ocurre en tres momentos distintos y solo el último gasta cuota del proveedor.

### 2.1 Momento 1 — sincronizar la bandeja

Al sincronizar, la app baja únicamente la **metadata de los correos** (asunto, remitente, fecha y el flag "tiene adjuntos"). No descarga adjuntos, ni siquiera la lista de adjuntos de cada correo.

A nivel visual, la lista del buzón muestra un **icono de clip** junto al asunto cuando el correo tiene al menos un adjunto descargable. Un correo que solo trae imágenes inline (logos, firmas con foto) **no** muestra clip — igual que hacen Gmail y Outlook web. Esa distinción importa: si todos los newsletters mostrasen clip, el icono perdería su valor de señal.

Un matiz deliberado: ese flag arranca en `false` y solo pasa a `true` **la primera vez que alguien abre el correo** y la app descubre que tenía partes descargables (momento 2). Es decir, justo después de sincronizar, un correo con adjuntos que nadie ha abierto todavía puede no mostrar clip aún. Es una simplificación consciente del MVP (el "porqué" está en [../limits/adjuntos.md](../limits/adjuntos.md)).

### 2.2 Momento 2 — abrir un correo

Al hacer clic en un correo, la app descarga el contenido (HTML + texto) **junto con la lista detallada de adjuntos**: nombre, tipo, tamaño y si la parte es inline o descargable. Esa lista se persiste localmente, así que abrir el mismo correo una segunda vez es instantáneo.

Lo que **no** se descarga aquí son los binarios. Solo el "menú": *"factura.pdf · 1,2 MB · PDF"*. El PDF en sí sigue en el proveedor. Resultado: abrir un correo con cinco adjuntos pesados es igual de rápido que abrir uno sin adjuntos.

Debajo del cuerpo, el visor pinta una **lista de tarjetas**, una por adjunto descargable. Cada tarjeta muestra un icono según el tipo (PDF, imagen, Word, Excel, genérico, o un icono de aviso si el adjunto está marcado como no disponible), el nombre (truncado, con el nombre completo en tooltip), el tamaño formateado y un botón de descarga. Un clic en cualquier punto de la tarjeta dispara la descarga.

### 2.3 Momento 3 — clicar un adjunto concreto

Solo entonces la app pide el binario al proveedor, lo persiste localmente y se lo entrega al navegador como descarga al disco. Mientras llega, la tarjeta muestra un spinner en su botón; la primera descarga puede tardar uno o dos segundos (hay que ir al proveedor), las siguientes son instantáneas porque el binario ya está cacheado.

Si el usuario clica varios adjuntos seguidos, la app procesa unas pocas descargas en paralelo y deja el resto en cola visible como "En cola" (el tope concreto y su porqué están en [../limits/adjuntos.md](../limits/adjuntos.md)). Esta cola acotada existe para no acercarse a los límites de concurrencia de Microsoft Graph y mantener estable la respuesta del proveedor.

A partir de ahí ese adjunto queda cacheado: si el usuario lo vuelve a clicar mañana o la semana que viene, se sirve directo desde el almacenamiento local **sin volver a llamar al proveedor** — hasta que la purga por TTL libere el binario por falta de uso (sección 7.3).

#### Ejemplo

> El usuario abre un correo con `informe.pdf` (8 MB) y `logo.png` (12 KB, embebido en la firma). El visor renderiza el cuerpo con el logo ya visible dentro del HTML y pinta **una sola** tarjeta de adjunto: `informe.pdf`. El logo NO aparece como tarjeta (es inline). Al clicar `informe.pdf`, spinner ~1 s y descarga al disco. Segundo clic: descarga instantánea.

### 2.4 Qué se puede descargar: todo lo que llegue

**Todos los adjuntos que lleguen** se muestran y se pueden descargar, sin filtrado adicional. La app no esconde tipos "peligrosos" en correos recibidos: si el correo trajo un `.exe`, el usuario lo ve y decide. Es la postura de Gmail web; la protección frente a contenido malicioso se delega al sistema operativo y al navegador del usuario en el momento de abrir el fichero descargado. Filtrar la recepción solo introduciría falsos negativos (esconder cosas legítimas) sin valor real.

### 2.5 Cómo distingue la app entre "imagen inline" y "adjunto descargable"

La regla es **estricta** y difiere ligeramente entre proveedores. El objetivo es no caer en dos errores típicos de correos mal construidos: que una imagen marcada como inline pero no usada en el cuerpo "se pierda" sin que el usuario la vea, o que un fichero pensado para descargar (un PDF con `Content-ID`, por ejemplo) acabe escondido dentro del HTML.

En **Gmail**, una parte se considera inline (embebida, no descargable) solo si cumple **a la vez** dos cosas: está marcada como inline —bien por traer disposición `inline`, bien por ser una imagen con `Content-ID`— **y** ese `Content-ID` aparece referenciado en el HTML del cuerpo (en `src=`/`background=` o en `url(cid:…)` del CSS). La consecuencia útil: una imagen con `Content-ID` referenciado se trata como inline aunque no traiga disposición `inline`; basta con que sea imagen y esté referenciada.

En **Outlook** la regla añade dos guardas más: además de estar marcada inline y de que su `Content-ID` esté referenciado, la parte debe **tener bytes** y ser de tipo `image/…`. Cualquier otra combinación se promociona a adjunto descargable.

En ambos proveedores, cualquier parte que no cumpla su regla de inline se trata como **adjunto descargable**. Esta política es la misma que se documenta como D-13 a lo largo del repositorio.

---

## 3. Qué se puede adjuntar al enviar

Al enviar (no al recibir) la app aplica una **blocklist** de extensiones que **combina** las dos listas oficiales — la de Gmail y la de Microsoft Exchange Online. El mismo archivo se rechaza independientemente de si la cuenta de origen es Gmail u Outlook. La lista exacta y su motivación viven en [../limits/adjuntos.md](../limits/adjuntos.md).

### 3.1 Por qué la lista es combinada y no una por proveedor

Las dos listas oficiales no coinciden: Outlook bloquea una superlista que incluye casi todo lo de Gmail más extensiones extra (`.py`, `.pyc`, `.mht`, `.mdb`, `.url`, `.theme`, etc.). Si la app respetase la lista de cada proveedor por separado, el mismo `.py` se enviaría desde una cuenta Gmail y se rechazaría desde una Outlook: UX inconsistente y confusa. Aplicando la **unión** se garantiza que cualquier archivo que pase la validación llegará al destinatario sin que ningún proveedor lo bloquee. Es la regla más estricta de las dos, que es la postura segura.

### 3.2 Cuándo se valida y por qué en el cliente

La validación ocurre **en el cliente, antes de subir el archivo**. Quien arrastra `virus.exe` recibe un mensaje inmediato ("Este tipo de archivo no se puede enviar por correo") sin que el archivo viaje por la red. El backend vuelve a validar como segunda red de seguridad.

Hay un motivo concreto para validar en el cliente, y es una asimetría entre proveedores:

- **Gmail rechaza síncronamente**: si la app subiese el `.exe`, Gmail responde con un error inmediato.
- **Outlook NO rechaza síncronamente**: el envío devuelve `202 Accepted` aunque el adjunto sea de un tipo bloqueado. El usuario creería que el correo salió, pero minutos más tarde recibe un NDR (mensaje de error diferido).

La única forma de dar un error inmediato y consistente entre ambos proveedores es validar antes de subir. La revalidación del backend existe igualmente.

### 3.3 Lo que la app NO comprueba al enviar

No analiza los archivos buscando virus ni verifica que el contenido corresponde a la extensión (un `.pdf` que en realidad sea un ejecutable renombrado pasaría el filtro). La protección frente a malware queda fuera del MVP — ver [../limits/adjuntos.md](../limits/adjuntos.md).

---

## 4. Componer y editar borradores con adjuntos

El comportamiento difiere del de los recibidos porque aquí el usuario es quien aporta el archivo.

### 4.1 La zona de adjuntos del composer

El composer ofrece dos formas de añadir adjuntos, un listado visual y un contador de capacidad, y todo está disponible en los tres flujos en los que se abre: "Nuevo mensaje", "Nuevo borrador" y "Editar borrador". La zona está activa desde que el composer aparece con una cuenta de origen cargada; no espera a que el usuario guarde nada.

- **Botón "Adjuntar"** (icono clip) en la barra inferior, junto a "Enviar": abre el selector de archivos del sistema.
- **Arrastrar y soltar** sobre el composer: mientras se arrastra, aparece un overlay ("Suelta el archivo para adjuntarlo").
- **Listado**: cada adjunto es un **chip compacto** con icono según el tipo, nombre truncado, tamaño y un botón "✕" para quitarlo. Mientras se sube, el chip lleva spinner y, si el archivo es grande, una barra de progreso.
- **Contador de capacidad**: discreto, del tipo «usado / límite» (p. ej. «12,4 MB / límite»). El total es uniforme y **no cambia** al cambiar la cuenta de origen; se pinta en rojo si el siguiente archivo excedería el límite (el tope exacto vive en [../limits/adjuntos.md](../limits/adjuntos.md)).

### 4.2 Añadir un adjunto

Cuando el usuario arrastra (o selecciona) un archivo, pasan estas cosas en orden:

1. **Validación cliente-side instantánea**: extensión, tamaño individual, tamaño total acumulado y número de adjuntos. Si algo falla, aparece un chip en rojo con la razón concreta durante unos segundos y el archivo NO viaja al backend.
2. **Bootstrap silencioso del borrador** (solo la primera vez, en "Nuevo mensaje" o "Nuevo borrador"): si el composer todavía no tiene un borrador asociado en el proveedor, la app crea uno —con cuerpo, destinatarios y asunto vacíos— para tener un identificador real al que asociar el adjunto. Es invisible en la propia app (no aparece en la lista local hasta que el usuario pulse "Guardar borrador"), pero **sí queda creado en Gmail/Outlook web** durante la composición. Si la cuenta de origen aún no se ha cargado, los archivos arrastrados se encolan unos milisegundos y se procesan en cuanto la cuenta está lista. Dos arrastres simultáneos no crean dos borradores: la app colapsa las llamadas concurrentes en una sola.
3. **Subida al backend**: si pasa la validación, el archivo viaja al backend de MailManager y se guarda **en local** (en la base de datos de la app). El chip aparece al instante con su spinner.
4. **Fin de la subida**: el chip se asienta sin spinner. Ya está persistido.

Tras el bootstrap, el proveedor ya tiene un borrador "vacío", pero **el adjunto en sí no viaja al proveedor todavía**. Cuerpo, destinatarios, asunto y binarios siguen viviendo solo en la app hasta "Guardar borrador" o "Enviar" (lazy push, sección 4.8).

Un efecto colateral del bootstrap: a partir de ese punto, el **selector de cuenta de origen queda bloqueado**. Cambiar de cuenta implicaría mover los adjuntos a otra cuenta; la app prefiere forzar a descartar y empezar de cero.

### 4.3 Quitar un adjunto

Un clic en la "✕" del chip lo elimina al instante del composer y del backend, sin pedir confirmación (es una operación trivial). La UI es optimista: si el borrado en el backend fallara, el chip reaparece con una marca de error. El proveedor sigue sin saber nada hasta el siguiente "Guardar"/"Enviar".

### 4.4 Cerrar y volver al borrador

Si el usuario cierra el composer (o el navegador) sin enviar, **los adjuntos se mantienen** asociados al borrador en la app. Al reabrir el borrador, ve los mismos adjuntos que dejó. Esto aplica también a un "Nuevo mensaje" en cuanto se haya producido el bootstrap de 4.2: a partir de ese punto hay un borrador real y el cierre se comporta como con cualquier otro.

### 4.5 Cerrar con cambios pendientes

Si hay cambios no guardados —incluidos adjuntos no sincronizados con el proveedor— al intentar cerrar (✕, Esc o navegar fuera), la app muestra un **diálogo de confirmación** con tres opciones:

- **"Guardar y cerrar"** — guarda el borrador (lo que internamente sube los adjuntos al proveedor) y cierra. Si el guardado falla, el diálogo se queda abierto con el error.
- **"Descartar"** — borra el borrador entero, adjuntos incluidos. Si el cierre se produce tras un bootstrap silencioso desde "Nuevo mensaje", "Descartar" **también borra ese borrador del proveedor** para no dejar restos en Gmail/Outlook web.
- **"Cancelar"** — vuelve al composer.

Si no hay cambios respecto a la última versión guardada, el cierre es directo, sin preguntar.

### 4.6 Guardar o enviar el borrador

Al pulsar "Guardar borrador" o "Enviar", la app toma todos los adjuntos locales del borrador, construye el mensaje completo (cuerpo + adjuntos) y lo sube al proveedor con una estrategia distinta según el proveedor:

- **Gmail**: una **sola llamada atómica** que combina la actualización del borrador con el envío. Si falla, el borrador del proveedor no queda en un estado intermedio.
- **Outlook**: **no atómico**. Sube primero los adjuntos al borrador del proveedor (uno a uno) y después emite la orden de envío. Si la subida de un adjunto falla, los que ya estaban subidos **quedan asociados** al borrador en el proveedor; al reintentar, la app los detecta y **no los re-sube**. La operación es reanudable.

Si todo va bien, la app sincroniza el estado local. Si no, deja el borrador como estaba y muestra el error.

**Nota sobre "Nuevo mensaje" con adjuntos**: cuando el usuario pulsa "Enviar" desde un "Nuevo mensaje" que ya tiene adjuntos —y, por tanto, un borrador silencioso creado—, internamente la app reutiliza la operación de **envío de borrador** en lugar del envío directo de correo. Es transparente para el usuario y el destinatario, y garantiza que los adjuntos lazy-pushed viajan en el mismo envío atómico (Gmail) o reanudable (Outlook) que el resto del mensaje. Una regresión que se saltase este reencaminamiento enviaría un correo de cuerpo vacío y dejaría los adjuntos huérfanos.

### 4.7 Cuando el envío falla porque un adjunto no llega al proveedor

Si al enviar algún adjunto pendiente falla tras los reintentos automáticos, **el envío entero se aborta**: el correo NO se manda y el destinatario NO recibe nada parcial. La app muestra los archivos concretos que fallaron y ofrece dos opciones:

- **"Reintentar enviar"** — vuelve a intentar el envío completo.
- **"Quitar adjuntos fallidos y enviar"** — elimina del borrador los que fallaron y reintenta sin ellos.

El borrador queda intacto durante todo el proceso. El porqué de abortar en lugar de enviar a medias: enviar un correo sin uno de los adjuntos prometidos es peor que no enviarlo —el destinatario no sabe que faltó algo, el remitente cree que llegó completo—, así que la app fuerza la decisión explícita.

### 4.8 Por qué lazy push y no subir cada adjunto al instante

Hay un motivo concreto por proveedor:

- **Gmail** obliga a que cada actualización de un borrador **reconstruya el mensaje completo**: añadir el quinto adjunto a un borrador que ya tiene cuatro implica volver a subir los cinco. Subir cada adjunto al instante haría que redactar un correo con cinco adjuntos costara 5+4+3+2+1 = 15 subidas en lugar de una.
- **Outlook** tiene un techo de peticiones concurrentes por buzón y los reintentos por throttling se acumulan rápido cuando el usuario adjunta varios archivos seguidos.

Guardar todo en local y empujarlo de golpe al proveedor elimina ambos problemas y se comporta igual de bien con los dos proveedores. El precio aceptado es la inconsistencia temporal de la sección 4.9.

### 4.9 Edge case aceptado: edición simultánea desde dos sitios

Si el usuario edita un borrador en MailManager y a la vez lo mira en Gmail/Outlook web, **no verá los adjuntos** que ha añadido en MailManager hasta que pulse "Guardar borrador" o "Enviar". Los dos lados se sincronizan en ese momento. Es intencional: es el precio del composer rápido (cada arrastre no espera al proveedor) y de no gastar cuota durante la composición.

Una variante a tener clara tras el bootstrap silencioso (4.2): el borrador **vacío** sí es visible en Gmail/Outlook web durante la composición de un "Nuevo mensaje"/"Nuevo borrador" en el que ya se hayan adjuntado archivos, porque se crea al aceptar el primer adjunto. Sus adjuntos siguen invisibles desde el lado web hasta el "Guardar"/"Enviar". Si el usuario elige "Descartar", ese borrador silencioso se elimina por completo del proveedor.

---

## 5. Responder y reenviar (Reply / Reply All / Forward)

Responder (Reply / Reply All) **no** arrastra los adjuntos del original — es la semántica esperada. **Reenviar** (Forward) sí: el composer los pre-rellena como chips ya cargados, descartables como cualquier otro. El mecanismo difiere fuertemente por proveedor.

- **Outlook** hereda los adjuntos **en el lado del proveedor**: el borrador de reenvío se crea con un primitivo que ya copia los adjuntos del original. Los chips aparecen de inmediato sin descargar nada; el binario vive solo en el borrador del proveedor (no se duplica en local) y la app no lo re-sube al enviar.
- **Gmail** no tiene copia server-side, así que el composer llama a un endpoint dedicado que **descarga cada adjunto del original** (reutilizando el cache local si lo hay) y lo vuelve a adjuntar al borrador. Para un borrador de Outlook ese mismo endpoint es un **no-op** (no copia nada y devuelve la lista actual de chips).

El frontend llama a ese endpoint de copia **siempre** tras crear un borrador de reenvío, sea Gmail u Outlook — la asimetría queda escondida detrás de una llamada uniforme. El endpoint es:

- **Tolerante a fallos parciales**: responde siempre con éxito y reporta los adjuntos que no pudo copiar en una lista de "saltados" con su motivo (proveedor caído, cap de tamaño/cantidad alcanzado, ya copiado, fuente no disponible…), en lugar de fallar el reenvío entero.
- **Idempotente**: reintentarlo tras un corte de red no vuelve a copiar lo ya copiado.

#### Ejemplo

> El usuario reenvía un correo de Gmail con `contrato.pdf` (2 MB) y `anexo.docx` (500 KB). El composer se abre, llama al endpoint de copia, que descarga ambos del original y los re-adjunta: dos chips aparecen ya cargados. El usuario añade un tercer archivo desde su disco y envía: los tres viajan juntos en el envío atómico de Gmail.

### 5.1 Trampas conocidas del reenvío

- **Outlook + editar el asunto**: si el usuario edita el `Subject` del reenvío más allá del prefijo `Fwd:`, Graph reasigna la conversación al guardar y el mensaje enviado se "desengancha" visualmente del hilo original en Outlook web. Es comportamiento aceptado del MVP; Gmail no lo sufre porque a un reenvío se le permite iniciar un hilo nuevo.
- **Nombres duplicados en un mismo reenvío de Outlook**: si el original lleva dos adjuntos con el mismo nombre, la copia server-side puede dejar uno sin su identificador de proveedor y el envío lo trataría como "pendiente de subir" (re-subida redundante de bytes que el proveedor ya tiene). Es vanishingly raro y el peor caso es benigno.

---

## 6. Robustez ante errores en la descarga

### 6.1 Reintentos al descargar del proveedor

Cuando el usuario clica un adjunto que aún no está cacheado y la llamada al proveedor falla por una causa **transitoria** (caída momentánea, throttling, error de red), la app reintenta automáticamente con una espera creciente y respetando el `Retry-After` del proveedor cuando lo envía (típico en el throttling de Microsoft Graph). Los errores **permanentes** no se reintentan: se reportan de inmediato. Las cifras exactas (número de intentos, esperas, códigos retryables vs. permanentes) están en [../limits/adjuntos.md](../limits/adjuntos.md).

### 6.2 Errores irrecuperables

Tras agotar los reintentos, la app distingue tres casos y da un mensaje distinto a cada uno:

- **El adjunto ya no existe en el proveedor** (el remitente borró el correo, el admin purgó el contenido…): la app marca ese adjunto como "no disponible" en su base de datos y, la próxima vez que se clique, muestra *"Este adjunto ya no está disponible en el servidor"*. La fila no se borra (la metadata sigue), pero ese adjunto concreto queda inutilizable y su tarjeta aparece deshabilitada. A partir de ese momento, **ni siquiera se intenta** ir al proveedor: la app corta de raíz.
- **Sin permisos al adjunto** (tokens caducados, permisos del tenant): *"No se pudo acceder al adjunto"*, con registro detallado para investigación.
- **El proveedor está caído** (error de servidor persistente): *"Inténtalo de nuevo en unos minutos"*, con botón de reintento. NO se marca el adjunto como inutilizable: probablemente vuelva a funcionar al rato.

### 6.3 TTL del cache de descargados

Los binarios cacheados localmente tienen una vida útil **desde el último acceso** (el plazo concreto está en [../limits/adjuntos.md](../limits/adjuntos.md)). Si un adjunto no se abre durante ese tiempo, su binario se purga del almacenamiento; la metadata se queda. Si el usuario lo abre después, la app lo vuelve a descargar del proveedor y lo cachea de nuevo — transparente para el usuario. La purga es **manual en el MVP** (un endpoint de administración protegido por un token de variable de entorno, sin cron). Las foreign keys de la base de datos sí limpian automáticamente cuando se desconecta una cuenta o se borra un correo.

---

## 7. Seguridad

### 7.1 Saneamiento del nombre del archivo

El nombre que llega (de un correo recibido o del composer) puede contener cualquier cosa. La app aplica un saneamiento **mínimo** preservando lo que importa al usuario:

- Sustituye caracteres peligrosos y secuencias de path traversal (`..`) por `-`.
- **Preserva acentos y caracteres UTF-8 legítimos** (no es un slugify agresivo).
- **Preserva la extensión** (lo que va tras el último punto).
- **Neutraliza los nombres reservados de Windows** (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) prefijándolos con `_` (`CON.pdf` → `_CON.pdf`): en Windows esos nombres están prohibidos a nivel de sistema de ficheros y romperían la descarga.
- **Resuelve duplicados** dentro del mismo correo o borrador: dos adjuntos con el mismo nombre saneado se renombran añadiendo " (1)", " (2)"… antes de la extensión, igual que el explorador de Windows.

#### Ejemplo

> Un correo trae dos adjuntos llamados ambos `informe.pdf`. La app los persiste como `informe.pdf` y `informe (1).pdf`. Un adjunto llamado `..\..\secreto.pdf` se persiste como `-secreto.pdf`. Un adjunto llamado `NUL.txt` se persiste como `_NUL.txt`.

### 7.2 Cómo se sirve el binario al navegador

Al descargar un adjunto, el backend responde con cabeceras estrictas:

- **`Content-Disposition: attachment`** — fuerza la descarga, nunca el render inline. Importante: servir un HTML como inline permitiría que el navegador lo ejecutara — un agujero XSS de manual. El nombre de archivo se emite en doble forma (ASCII de respaldo + UTF-8 percent-encoded) para que todos los clientes muestren el nombre correcto con acentos.
- **`X-Content-Type-Options: nosniff`** — impide que el navegador "adivine" el tipo y lo trate como otra cosa.
- **`Cache-Control: private, no-cache`** — ningún proxy intermedio guarda el contenido.

### 7.3 Autenticación del endpoint de descarga

La descarga usa la **cookie de sesión** existente. No hay URLs firmadas con tokens en query string ni descargas anónimas. Antes de servir el binario, el backend valida que haya sesión activa, que el `mailbox_id`/`account_id` de la URL pertenezcan al usuario logueado y que el adjunto pertenezca a un correo de esa cuenta. Si cualquiera falla, la respuesta es `404` (no `403`) para no filtrar la existencia de adjuntos ajenos.

---

## 8. Cómo se almacenan por dentro (visión técnica resumida)

Aunque el usuario no se entera, conviene que el equipo lo tenga claro:

- **Los adjuntos recibidos** se cachean en PostgreSQL con el binario en una **tabla dedicada**, separada de la de metadatos. Eso permite listar adjuntos sin cargar megabytes en memoria y deja que la purga por TTL borre el binario conservando la metadata (el flag "descargado" vuelve a `false` y el siguiente clic lo re-descarga).
- **El identificador del cache depende del proveedor**, porque los IDs no son igual de estables: en Gmail no se confía en el `attachmentId` (no está declarado estable y se ha visto cambiar entre llamadas), así que se cachea por la parte MIME inmutable y se redescubre el `attachmentId` cuando hay que descargar; en Outlook los IDs cambian si el mensaje se mueve de carpeta, así que **todas** las llamadas a adjuntos piden el "ID inmutable" para estabilizarlos.
- **El flag denormalizado "tiene adjuntos"** evita un JOIN por fila al pintar la lista del inbox.
- **El orden de los adjuntos** se preserva: el orden en que el proveedor los devolvió (recibidos) o el orden en que el usuario los añadió (borradores).
- **Los adjuntos de borradores** viven en su **propia tabla**, con el binario inline; si el borrador se borra o se envía, sus adjuntos se van con él (cascade).
- **No se deduplica nada**: el mismo PDF en tres cuentas ocupa tres copias. Decisión consciente del MVP — ver [../limits/adjuntos.md](../limits/adjuntos.md).
- **Las tablas están preparadas para migrar** los binarios fuera de PostgreSQL (sistema de ficheros o almacenamiento externo) sin romper la API; hoy todos viven en la base de datos.

---

## 9. Resumen en una frase

> La app acepta un puñado de archivos por correo dentro de un techo de tamaño uniforme entre proveedores, bloquea ejecutables al enviar pero acepta cualquier cosa al recibir, descarga los binarios del proveedor solo cuando el usuario los clica (con una cola acotada de descargas concurrentes) y los cachea localmente con un TTL desde el último acceso; mantiene los adjuntos de borradores en su propia base de datos hasta que el usuario decide guardarlos o enviarlos, momento en el que se suben todos juntos al proveedor —atómicamente en Gmail, reanudable adjunto a adjunto en Outlook—; hereda los adjuntos al reenviar (server-side en Outlook, descarga-y-recopia en Gmail); aborta cualquier envío en el que falle algún adjunto dejando el borrador intacto para reintentar; y delega la protección frente a contenido malicioso al sistema operativo del usuario, igual que Gmail web. Los números exactos están en [../limits/adjuntos.md](../limits/adjuntos.md).
