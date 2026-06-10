# Composición y envío de correos nuevos — comportamiento (MVP)

Este documento describe **qué hace** la app cuando el usuario redacta un correo nuevo y lo manda, y **qué experimenta** delante de la pantalla. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

La frontera de este documento es deliberadamente estrecha:

- **Cubre**: el composer en modo "Nuevo mensaje" (campos, destinatarios, cuerpo con **texto enriquecido**), las validaciones que se aplican antes de mandar, y el **envío directo** de un correo recién escrito. El campo "Mensaje" es el mismo en los cinco modos de redacción, así que la descripción del editor (sección 3) es **la fuente canónica**: [borradores.md](./borradores.md) y [responder-y-reenviar.md](./responder-y-reenviar.md) enlazan aquí para no repetirla.
- **No cubre**: el ciclo de vida del **borrador** y su sincronización (en [borradores.md](./borradores.md)); **Responder / Responder a todos / Reenviar** (en [responder-y-reenviar.md](./responder-y-reenviar.md)); la gestión de **adjuntos** —cómo se añaden, validan, suben— (en [adjuntos.md](./adjuntos.md)).

Aun así, hay un punto donde estas piezas se entrelazan y **sí** se explica aquí porque cambia el comportamiento del envío: cuando el usuario adjunta un archivo a un "Nuevo mensaje", la app crea por debajo un **borrador silencioso** y el botón "Enviar" deja de mandar el correo "en directo" para mandar ese borrador. La sección 6 lo desarrolla.

Los topes concretos (longitudes mínimas, reintentos, qué NO se soporta) viven en un documento aparte: **[../limits/composicion-y-envio.md](../limits/composicion-y-envio.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están las cifras exactas.

---

## 1. Cómo se abre el composer y en qué modos

El composer es una única superficie de redacción que se abre flotando sobre la app (overlay). Puede abrirse en varios **modos**, y este documento se centra en uno: **"Nuevo mensaje"**, el que termina en un envío directo.

Los otros modos comparten la misma ventana pero cambian el comportamiento del botón principal y de la barra inferior:

- **Nuevo mensaje** — redacción desde cero pensada para **enviar**. El botón principal es "Enviar". Es el foco de este documento.
- **Nuevo borrador** / **Editar borrador** — pensados para **guardar** y, opcionalmente, enviar más tarde.
- **Responder / Responder a todos / Reenviar** — abren el composer ya rellenado a partir de un correo existente (su mecánica está en [responder-y-reenviar.md](./responder-y-reenviar.md); la herencia de adjuntos al reenviar, en [adjuntos.md](./adjuntos.md) § 5).

Un detalle de arquitectura visible para el usuario: el composer es **único y global**. Se monta una sola vez dentro del área del buzón y se invoca desde cualquier sitio (el botón "Redactar" de la barra lateral, las acciones de un correo, etc.). Esto tiene una consecuencia práctica: **no se pueden tener dos composers abiertos a la vez**. Abrir uno nuevo reemplaza al anterior; por eso, si hay cambios sin guardar, la app pregunta antes de descartar (sección 5).

### 1.1 Qué muestra el composer en "Nuevo mensaje"

- Un **selector de cuenta de origen** ("desde qué cuenta envío"). Si el mailbox tiene varias cuentas, aparece un desplegable; por defecto se selecciona la primera. Mientras no haya un borrador asociado, se puede cambiar libremente.
- El campo **"Para"** (destinatarios principales).
- Un enlace **"Añadir CC/BCC"** que despliega los campos de copia (**"CC"**) y copia oculta (**"BCC"**). Empiezan plegados salvo que ya traigan contenido.
- El campo **"Asunto"**.
- El **cuerpo**, que es un **editor con formato** (negrita, cursiva, subrayado, listas y enlaces) con una barra de herramientas justo encima (ver sección 3).
- Una barra inferior con el botón **"Adjuntar"** (clip) y el botón **"Enviar"**.

---

## 2. Destinatarios: cómo se escriben y se validan

### 2.1 Formato de entrada

Los tres campos de destinatarios ("Para", "Cc", "Cco") aceptan **varias direcciones separadas por comas**. Los espacios alrededor de cada dirección se ignoran y las entradas vacías (comas sueltas, espacios) se descartan. Es decir, `ana@x.com,  ,  luis@y.com ,` se interpreta como dos destinatarios limpios.

### 2.2 Validación de forma en el cliente

Antes de permitir el envío, la app comprueba que **cada** dirección escrita en "Para", "Cc" y "Cco" tenga una forma de email plausible (algo parecido a `local@dominio.tld`). La comprobación es **deliberadamente permisiva**: solo busca atajar los errores obvios —falta la `@`, falta el dominio o el TLD— sin rechazar direcciones legítimas que el proveedor sí aceptaría. No valida nombres internacionalizados ni partes locales entrecomilladas.

El comportamiento que ve el usuario:

- Si alguna dirección de cualquiera de los tres campos no pasa la comprobación, el botón "Enviar" se **desactiva** y aparece un aviso neutro ("Dirección de correo no válida.").
- En cuanto corrige la dirección, el botón vuelve a habilitarse.

> El motivo de validar la forma en el cliente: si una dirección malformada llegara al proveedor, este la rechazaría con un error técnico (un 400) que en la app se traduciría a un error de servidor genérico y confuso. Atajarlo en el composer mantiene el fallo local y el mensaje comprensible.

### 2.3 Cuándo está habilitado "Enviar"

En "Nuevo mensaje", el botón "Enviar" solo se habilita cuando se cumplen a la vez: hay una cuenta de origen seleccionada, hay **al menos un** destinatario en "Para", ninguna dirección de los tres campos es inválida, y no hay ya un envío en curso. Pulsar "Enviar" dos veces seguidas no manda dos correos: mientras el primero está en vuelo, el segundo clic se ignora.

### 2.4 Ejemplos

> El usuario escribe `Para: ana@empresa.com, jefe`. El botón "Enviar" queda gris y aparece "Dirección de correo no válida." (la entrada `jefe` no tiene forma de email). Corrige a `jefe@empresa.com` y el botón se reactiva.

> El usuario deja "Para" vacío y solo rellena "Cc". El botón "Enviar" sigue desactivado: en un correo nuevo se exige al menos un destinatario principal.

---

## 3. El cuerpo es un editor con formato (texto enriquecido)

> Esta sección es la **fuente canónica** del editor de cuerpo. El mismo editor se usa al guardar/editar un borrador y al responder/reenviar; esos documentos enlazan aquí en lugar de repetir la mecánica.

El cuerpo del correo se escribe en un **editor con formato** (WYSIWYG: lo que se ve es lo que se envía). Justo encima del área de escritura hay una **barra de herramientas** con, en este orden:

- **Negrita** (también con el atajo Ctrl/Cmd + B).
- **Cursiva** (Ctrl/Cmd + I).
- **Subrayado** (Ctrl/Cmd + U).
- **Lista con viñetas**.
- **Lista numerada**.
- **Insertar enlace**: el usuario selecciona un texto, pulsa el botón, escribe la dirección en un pequeño popover y ese texto queda convertido en hipervínculo (texto visible distinto de la URL). La dirección debe llevar un esquema reconocido (`http`, `https` o `mailto`); si no, el popover avisa y no crea el enlace. Sobre un enlace ya existente, el mismo botón ofrece editarlo o quitarlo.
- **Quitar formato**: devuelve la selección a texto normal.

El correo viaja **con ese formato**. Para los destinatarios cuyo cliente no muestre HTML, la app adjunta automáticamente una **versión en texto plano equivalente** (las listas se renderizan como `- ` / `1.`, los enlaces como `texto (url)` y las citas con `> `), de modo que el mensaje sigue siendo legible. El detalle de cómo se transporta a cada proveedor está en la sección 4.

### 3.1 Qué formato sobrevive y qué se limpia

El editor está restringido a propósito: solo entiende negrita, cursiva, subrayado, listas y enlaces. Esto importa al **pegar** contenido con formato (desde una página web, Word, Excel…): se conserva únicamente el formato soportado y **todo lo demás se limpia** (colores, tamaños de letra, tablas, imágenes incrustadas, estilos de Office…). El usuario no ve un error; simplemente el contenido no soportado desaparece y queda el texto con el formato que sí se admite.

Además, antes de enviarse y de guardarse, el contenido pasa por un **saneamiento de seguridad** en el servidor que vuelve a recortar a esa misma lista de elementos permitidos y endurece los enlaces (se abren en una pestaña nueva, sin filtrar el referente). Es invisible para el usuario salvo, otra vez, en que cualquier elemento no soportado desaparece. Lo que NO admite ese saneamiento (imágenes inline, tablas, scripts…) y el listón exacto de etiquetas están en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md).

### 3.2 Tamaño máximo del cuerpo

Hay un **tope holgado** para el tamaño del cuerpo con formato (del orden de ~1 MB de contenido; sin imágenes incrustadas, un correo normal nunca se acerca). Si se supera, aparece un **aviso de error en línea** con el mismo estilo de error que el resto del composer y **se bloquean** "Enviar", "Guardar borrador" y "Enviar borrador" hasta que el usuario reduzca el texto. La cifra exacta y dónde se hace cumplir están en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md).

El asunto exige contenido (no se puede mandar un correo con asunto totalmente vacío) y el cuerpo también (en el envío directo); los mínimos exactos están en el catálogo de límites.

---

## 4. Qué pasa al pulsar "Enviar"

Cuando el usuario pulsa "Enviar" en un "Nuevo mensaje" **sin adjuntos**, la app manda el correo directamente al proveedor (Gmail u Outlook) usando la cuenta de origen seleccionada. Mientras la operación está en vuelo, el botón muestra un estado de envío.

- **Si el envío tiene éxito**: el composer se cierra y la vista del buzón se refresca, de modo que el correo recién enviado aparece en "Enviados" en cuanto el listado se actualiza.
- **Si el envío falla**: el composer **permanece abierto** con el contenido intacto y se muestra un mensaje de error. Nada se pierde; el usuario puede corregir y reintentar manualmente.

### 4.1 La cuenta de origen decide el proveedor

El correo sale por la cuenta seleccionada, y eso determina si se usa la API de Gmail o la de Microsoft Graph. Para el usuario el resultado es el mismo (el correo se manda), pero por debajo hay una asimetría que conviene conocer porque explica algún comportamiento de los errores:

- **Gmail** manda el correo en **una sola operación**. El cuerpo con formato viaja en un sobre con **dos versiones del mensaje**: la de texto plano (derivada del HTML) y la de HTML, en ese orden; cada cliente de correo muestra la más rica que entienda.
- **Outlook** no tiene un "enviar directo" equivalente para un correo redactado al vuelo: por debajo crea un borrador en el servidor, lo envía y, **si el envío falla, borra ese borrador** para no dejar restos en Outlook web. El cuerpo se entrega marcado como HTML. El usuario no ve nada de esto; solo nota que el correo se manda (o no).

### 4.2 El envío directo no se reintenta automáticamente

Importante y poco obvio: el **envío directo** de un correo nuevo (la ruta de la sección 4, sin adjuntos) es **de un solo intento**. Si el proveedor falla por una causa transitoria (un hipo momentáneo, throttling), la app **no** reintenta sola: muestra el error y deja que el usuario vuelva a pulsar "Enviar".

Esto contrasta con el envío **de un borrador** —y, por tanto, con el envío de un "Nuevo mensaje" que llevaba adjuntos (sección 6)—, que **sí** reintenta varias veces ante fallos transitorios. La diferencia no es un capricho: el camino con adjuntos reconstruye y reenvía un mensaje más pesado y permanece "bajo carga" más tiempo, donde un reintento automático aporta más. El número exacto de intentos y las esperas están en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md).

### 4.3 Errores típicos y qué ve el usuario

- **Destinatario rechazado / faltante**: si la lista de destinatarios llega vacía al proveedor (caso de borde, normalmente atajado en el cliente) o el proveedor rechaza un destinatario, la app muestra "Destinatario no encontrado" en lugar de un error técnico.
- **Cuenta no conectada / sesión caducada**: si los permisos de la cuenta de origen han caducado, el envío falla con un error de conexión de cuenta; la app lo distingue de un fallo genérico (mensaje distinto).
- **Proveedor caído / error inesperado**: el correo no sale; el detalle técnico queda en los logs del servidor, no en pantalla. Atención a una simplificación del MVP: en el envío directo, este fallo de proveedor comparte el **mismo** mensaje "Destinatario no encontrado" que el caso anterior (el cliente colapsa ambos), aunque no tenga nada que ver con los destinatarios. Las situaciones, sus códigos y los mensajes exactos están en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md) § 3.

### 4.4 Tras enviar, lo registramos "en segundo plano"

Después de que el proveedor confirme el envío, la app intenta guardar localmente la metadata del correo enviado (para que aparezca en "Enviados" sin esperar a la siguiente sincronización completa). Ese guardado es **best-effort**: si fallara, **no** se considera que el envío fracasó —el usuario ya tiene lo que le importa, que el correo salió— y, como mucho, el correo enviado tardará un poco más en aparecer en el listado (hasta la siguiente sincronización). Esta es la misma filosofía "fire-and-forget" que se aplica al registro posterior de un borrador enviado.

---

## 5. Cerrar el composer con cambios sin guardar

Como el composer es único, cerrarlo (con la "✕", con Esc o navegando fuera) cuando hay contenido a medias podría perder trabajo. Para evitarlo:

- Si en "Nuevo mensaje" **hay algún contenido** (cualquier campo con texto), al intentar cerrar aparece un **diálogo de confirmación** con tres opciones: **Guardar y cerrar** (convierte lo escrito en un borrador y lo conserva), **Descartar** (tira el contenido) y **Cancelar** (vuelve al composer).
- Si **no hay nada escrito**, el composer se cierra directamente sin preguntar.

El detalle fino de este diálogo —especialmente cuando ya se ha creado un borrador silencioso por haber adjuntado archivos, en cuyo caso "Descartar" también borra ese borrador del proveedor— vive en [adjuntos.md](./adjuntos.md) (cierre con cambios pendientes). Aquí basta con saber que un "Nuevo mensaje" con texto no se pierde por accidente.

---

## 6. El entrelazado con adjuntos: el "borrador silencioso" y el reencaminamiento del envío

Este es el punto donde "Nuevo mensaje" y "adjuntos" se cruzan, y cambia el comportamiento de "Enviar". Conviene entenderlo aunque el grueso de los adjuntos esté documentado en [adjuntos.md](./adjuntos.md).

### 6.1 Por qué aparece un borrador al adjuntar

Un correo "en directo" no tiene dónde guardar un archivo mientras se redacta. Para poder asociar adjuntos, la app necesita un **borrador real en el proveedor** al que colgarlos. Por eso, **la primera vez** que el usuario adjunta un archivo en un "Nuevo mensaje", la app crea por debajo —de forma silenciosa— un borrador (con cuerpo, destinatarios y asunto todavía vacíos) solo para tener ese identificador.

Efectos visibles de ese bootstrap:

- El **selector de cuenta de origen queda bloqueado**: a partir de ese momento no se puede cambiar la cuenta (cambiarla implicaría mover los adjuntos a otra cuenta; la app prefiere forzar a descartar y empezar de cero).
- El borrador **vacío** se vuelve visible en Gmail/Outlook web durante la composición (aunque los adjuntos en sí no se suben hasta "Guardar"/"Enviar" — eso es el *lazy push* de [adjuntos.md](./adjuntos.md)).
- Si la cuenta de origen aún no había terminado de cargar, los archivos arrastrados se encolan unos milisegundos y se procesan en cuanto la cuenta está lista. Dos arrastres simultáneos no crean dos borradores: la app colapsa las llamadas en una sola.

### 6.2 "Enviar" se reencamina a "enviar borrador"

Aquí está el comportamiento clave: cuando el usuario pulsa "Enviar" en un "Nuevo mensaje" que **ya tiene un borrador silencioso** (porque adjuntó algo), la app **no** usa el envío directo de la sección 4. En su lugar, reutiliza internamente la operación de **enviar borrador**: primero vuelca al borrador del proveedor el cuerpo, el asunto y **todos** los destinatarios (incluidos Cc y Cco), luego sube los adjuntos pendientes y finalmente lo envía.

Es transparente para el usuario y para el destinatario, pero tiene tres consecuencias observables:

1. **Cc y Cco sí viajan** en este camino (ver la trampa de 6.4).
2. El envío **se reintenta** ante fallos transitorios (a diferencia del envío directo — sección 4.2).
3. La atomicidad depende del proveedor: en Gmail el "actualizar + enviar" es una sola operación atómica; en Outlook es por pasos (sube cada adjunto, persiste lo que ya subió y, si algo falla a mitad, un reintento no re-sube lo ya subido). El detalle completo está en [adjuntos.md](./adjuntos.md).

> Una regresión que se saltara este reencaminamiento mandaría un correo de **cuerpo vacío** (el envío directo no sabe nada del borrador) y dejaría los adjuntos huérfanos. Por eso el reencaminamiento es parte del contrato, no un detalle de implementación.

### 6.3 Si falla la subida de un adjunto al enviar

Si al enviar (por el camino de borrador) algún adjunto pendiente no llega al proveedor tras los reintentos, **el envío entero se aborta**: el correo NO se manda y el destinatario NO recibe nada parcial. La app muestra los archivos que fallaron y ofrece "Reintentar enviar" o "Quitar adjuntos fallidos y enviar". El borrador queda intacto. El porqué de abortar en lugar de mandar a medias está en [adjuntos.md](./adjuntos.md).

### 6.4 Trampa: Cc/Cco en un "Nuevo mensaje" SIN adjuntos

Hay una asimetría sutil que el equipo debe conocer. El composer **siempre** muestra los campos Cc/Cco (tras desplegar "Añadir CC/BCC"), en cualquier modo. Pero el **envío directo** de un "Nuevo mensaje" (sección 4, sin adjuntos) solo transporta el campo **"Para"**: los destinatarios escritos en Cc y Cco **no se incluyen** en ese envío directo.

En cambio, en cuanto interviene un borrador —porque el usuario adjuntó un archivo (6.2), o porque guardó el borrador explícitamente— el envío pasa por el camino de borrador, que **sí** vuelca Cc y Cco.

Dicho de otro modo: en el MVP, **para garantizar que Cc/Cco lleguen en un correo nuevo, debe existir un borrador** (basta con adjuntar algo o con guardar el borrador antes de enviar). Es una limitación conocida; está catalogada en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md) bajo "lo que NO soporta".

#### Ejemplo

> El usuario redacta un "Nuevo mensaje", pone `Para: ana@x.com`, despliega Cc y añade `luis@x.com`, y pulsa "Enviar" **sin adjuntar nada**. El correo le llega a Ana, pero **no** a Luis (el envío directo solo lleva "Para"). Si el mismo usuario hubiera arrastrado un PDF antes de enviar, el envío habría pasado por el borrador y Luis habría recibido la copia.

---

## 7. Recorrido completo de un envío (resumen rápido)

Para ver el flujo de "Nuevo mensaje" de un vistazo:

1. El usuario abre "Nuevo mensaje" → el composer aparece con la primera cuenta seleccionada.
2. Rellena "Para" (y opcionalmente Cc/Cco), "Asunto" y el cuerpo (con el editor de formato).
3. La app valida la forma de cada dirección en vivo; el botón "Enviar" se habilita cuando todo es válido y hay al menos un destinatario.
4. **Sin adjuntos** → al pulsar "Enviar", el correo sale en directo por la cuenta elegida (Gmail en una llamada; Outlook crea-envía-y-limpia por debajo). Un solo intento.
5. **Con adjuntos** → el primer adjunto creó un borrador silencioso; "Enviar" vuelca todo al borrador (incluidos Cc/Cco), sube los adjuntos y lo manda (con reintentos; atómico en Gmail, por pasos en Outlook).
6. Éxito → se cierra el composer y se refresca el buzón. Fallo → el composer sigue abierto con el contenido y muestra el error.
7. Tras el éxito, la metadata del enviado se registra en segundo plano (best-effort).

---

## 8. Resumen en una frase

> En "Nuevo mensaje" el usuario redacta con un **editor de texto enriquecido** (negrita, cursiva, subrayado, listas y enlaces, con tope de tamaño y saneamiento en el servidor), destinatarios separados por comas con validación de forma en el cliente, y al pulsar "Enviar" el correo sale **en directo y de un solo intento** por la cuenta elegida (Gmail en una llamada, con el cuerpo en HTML + texto plano equivalente; Outlook crea-envía-y-limpia por debajo, con el cuerpo en HTML) llevando **solo "Para"**; pero en cuanto el correo tiene adjuntos —que crean un **borrador silencioso**— el envío se **reencamina a enviar borrador**, que sí transporta Cc/Cco, **reintenta** los fallos transitorios y sube los adjuntos (atómico en Gmail, por pasos reanudables en Outlook), abortando el envío entero si algún adjunto no llega; las cifras exactas y lo que deliberadamente no soporta viven en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md).
