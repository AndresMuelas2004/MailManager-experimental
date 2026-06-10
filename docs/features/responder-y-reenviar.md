# Responder, Responder a todos y Reenviar — comportamiento (MVP)

Este documento describe **qué hace** la app cuando el usuario responde o reenvía un correo, y **qué experimenta** delante de la pantalla: cómo se abre el composer ya relleno, a quién va dirigida la respuesta, cómo se cita el original, cómo se "engancha" la respuesta al hilo, qué pasa con los adjuntos al reenviar, y dónde están las trampas. No entra en cómo está cableado el código: es una guía de comportamiento para el equipo y para el futuro despliegue.

Las tres acciones —**Responder** (Reply), **Responder a todos** (Reply All) y **Reenviar** (Forward)— comparten la mayor parte del flujo y se diferencian en tres cosas: a quién se dirige el correo, cómo queda el asunto, y si arrastran o no los adjuntos del original.

Esta funcionalidad reutiliza la maquinaria de borradores y de adjuntos. Lo específico de adjuntos (chips, validación, lazy push, envío atómico vs. reanudable) vive en **[adjuntos.md](adjuntos.md)**; aquí solo se cubre lo propio de responder/reenviar y se enlaza. Los topes concretos, los códigos de error y la lista de "lo que NO soporta" están en **[../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md)** — aquí se mencionan de pasada y se explica el *porqué*.

---

## 1. Qué ocurre al pulsar Responder / Responder a todos / Reenviar

Las tres acciones se disparan desde un correo abierto y abren **el mismo composer** que se usa para un borrador normal, pero ya relleno y enganchado al correo original. El usuario ve un breve indicador de carga (típicamente unos centenares de milisegundos) mientras la app prepara el contexto, y a continuación el composer aparece con destinatarios, asunto y cuerpo precargados.

Por dentro ocurren, en orden, estos pasos (transparentes para el usuario):

1. **Se calcula el contexto de respuesta** a partir del correo original: a quién hay que escribir, cómo queda el asunto, el cuerpo citado y las "señales de hilo" (los identificadores que permiten que la respuesta se agrupe con el original). Esto implica una única lectura al proveedor para recuperar las cabeceras y el cuerpo del original.
2. **Se crea el borrador en el proveedor de inmediato** (igual que hace Gmail/Outlook web al pulsar Responder). El composer necesita un borrador real desde el primer momento para poder aceptar adjuntos, así que no se espera a "Guardar".
3. **Se rellena el composer** con lo calculado y se marca como "borrador existente": el selector de cuenta queda bloqueado y el botón pasa a "Enviar" sobre ese borrador.
4. **Solo en Reenviar**: se copian los adjuntos del original al borrador (ver § 5).

A partir de ahí el usuario edita lo que quiera y pulsa Enviar (o Guardar, o cierra). El comportamiento de guardado, cierre con cambios pendientes, adjuntos y envío es el de cualquier borrador — descrito en [adjuntos.md](adjuntos.md) (§ 4 y § 5).

> **Por qué se crea el borrador tan pronto.** Adjuntar un archivo necesita un borrador con identificador real al que asociarlo. En un "Nuevo mensaje" en blanco ese borrador se crea de forma perezosa (solo si el usuario adjunta algo); en Responder/Reenviar se crea siempre y de inmediato, porque la acción ya nace ligada a un correo concreto y el coste es el mismo que en Gmail/Outlook web. Como consecuencia, desde el primer instante el composer se comporta como "editar borrador": el selector de cuenta de origen está bloqueado y, al cerrar con cambios, se ofrece el diálogo Guardar / Descartar / Cancelar (en Descartar se elimina el borrador también del proveedor).

---

## 2. A quién se dirige la respuesta

El destinatario lo calcula la app automáticamente según la acción y según quién envió el original. El usuario siempre puede editarlo después.

### 2.1 Responder

- **Para** = el remitente del original. Pero si el original pedía explícitamente que se respondiera a otra dirección (cabecera *Reply-To*, típica de listas de correo o de remitentes "no-reply"), se respeta esa dirección en lugar del remitente. Esta regla (R-10) evita que una respuesta a una newsletter caiga en un buzón que no la lee.
- **CC** = vacío.

### 2.2 Responder a todos

- **Para** = igual que Responder (remitente, o Reply-To si lo hay).
- **CC** = todos los demás implicados del original (los destinatarios "Para" más los "CC"), **menos** la propia cuenta del usuario y menos quien ya está en "Para". Se eliminan duplicados ignorando mayúsculas/minúsculas.

El objetivo es el esperado: "responder a todos" mantiene en copia a todo el grupo original sin incluirte a ti mismo ni duplicar al destinatario principal.

> **Caso límite — la app no conoce tu propia dirección.** El filtro "quítame a mí del CC" necesita saber la dirección de la cuenta de origen. Si esa dirección no se llegó a capturar al conectar la cuenta, el filtro se desactiva silenciosamente y el usuario puede aparecer en su propio CC. Es un caso menor y editable a mano; el detalle está en [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md).

### 2.3 Reenviar

- **Para** y **CC** = vacíos. Reenviar va dirigido a destinatarios nuevos que el usuario teclea; la app no presupone ninguno.

### 2.4 Responder a un correo que tú enviaste

Si el usuario responde a un mensaje que está en **Enviados** (es decir, lo escribió él mismo), invertir "responder al remitente" no tendría sentido (el remitente es él). En ese caso el "Para" se rellena con los destinatarios originales del mensaje, de modo que la respuesta continúa la conversación con la otra parte en lugar de volver a uno mismo.

#### Ejemplo

> Ana (`ana@x.com`) escribe a `juan@y.com` con copia a `eva@z.com`. Juan, desde su cuenta, pulsa **Responder a todos**: la app rellena **Para: ana@x.com** y **CC: eva@z.com** (Juan no se incluye a sí mismo). Si Ana hubiera puesto `Reply-To: soporte@x.com`, el "Para" sería `soporte@x.com`.

---

## 3. Cómo queda el asunto

- **Responder / Responder a todos**: se antepone `Re:` al asunto original, salvo que ya empiece por un prefijo de respuesta reconocido (incluye variantes de otros idiomas como `AW:` alemán o `SV:` sueco, y tolera el `Re :` con espacio extra). No se acumulan `Re:` redundantes.
- **Reenviar**: se antepone `Fwd:`, salvo que ya empiece por un prefijo de reenvío reconocido (`Fw:`, `Fwd:`, y los españoles `RV:` / `Reenv:`).

El prefijo original se preserva tal cual (mayúsculas y espaciado incluidos) en lugar de reescribirse a una forma canónica, para que el usuario no vea cambiar la forma del asunto al re-responder — igual que hace Gmail web.

---

## 4. Cómo se cita el correo original

El cuerpo del composer llega precargado **con formato (HTML)**: el editor enriquecido (ver [composicion-y-envio.md](composicion-y-envio.md) § 3) se abre con una **línea de atribución** y, debajo, el original metido dentro de un **bloque de cita diferenciado** (un recuadro con barra lateral a la izquierda, al estilo de una cita), de modo que se distingue de un vistazo lo que escribe el usuario de lo que es el original citado.

Un matiz importante y poco obvio: aunque la cita se muestra **con formato**, el original **no se ingiere como HTML**. La app **degrada primero el original a texto legible** (descartando scripts, estilos y metadatos, conservando los saltos como líneas) y luego envuelve ese texto en el bloque de cita. Es decir, el recuadro de cita es la única "decoración" HTML; el contenido citado en sí es el texto del original, no su marcado. Esto evita arrastrar HTML arbitrario del remitente al editor restringido.

El cursor queda **encima** de la cita, sobre dos líneas en blanco, para que el usuario escriba su mensaje sin pisar lo citado.

- **Responder / Responder a todos**: una línea de atribución del tipo *"El 23 de mayo de 2026 a las 14:32, Ana López &lt;ana@example.com&gt; escribió:"* y, debajo, el cuerpo original dentro del recuadro de cita.
- **Reenviar**: un bloque *"---------- Mensaje reenviado ----------"* con las líneas *De / Fecha / Asunto / Para / Cc* y, debajo, el cuerpo original dentro del recuadro de cita.

> **Detalle de idioma y zona horaria.** La fecha de la cita se redacta en español y se renderiza en UTC (la internacionalización y la zona horaria local quedan fuera del MVP). El formato exacto, el recorte del original citado y sus límites están en [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md).

---

## 5. Adjuntos: Responder no los arrastra, Reenviar sí

Esta es una de las diferencias clave entre las acciones:

- **Responder / Responder a todos NO arrastran los adjuntos** del original. Es la semántica esperada: cuando respondes a un correo no reenvías sus ficheros. El composer abre con la lista de adjuntos vacía (el usuario puede añadir los suyos).
- **Reenviar SÍ arrastra los adjuntos** del original: aparecen como chips ya cargados en el composer, descartables uno a uno como cualquier otro adjunto.

El **mecanismo** por el que un reenvío hereda los adjuntos difiere fuertemente entre proveedores, y esa asimetría es la parte más interesante. El comportamiento de los chips, su validación, los topes de tamaño/cantidad y el envío están en [adjuntos.md](adjuntos.md); aquí se explica solo cómo llegan los adjuntos al reenvío.

### 5.1 Outlook: herencia en el lado del proveedor

Al crear un borrador de reenvío en Outlook, el propio proveedor **copia los adjuntos del original** como parte de la creación del borrador. La app descubre esos adjuntos ya copiados y los muestra como chips de inmediato, **sin descargar ningún binario**: las bytes viven solo en el borrador del proveedor. Al enviar, la app **no los re-sube** (ya están allí).

### 5.2 Gmail: descargar y volver a adjuntar

Gmail no tiene una primitiva de "copia server-side" para reenviar. Así que la app, tras crear el borrador, llama a un endpoint dedicado que **descarga cada adjunto del original** (reutilizando el cache local si ya estaba descargado) y lo **vuelve a adjuntar** al borrador, con sus bytes. Los chips aparecen cuando termina esa copia.

### 5.3 Una llamada uniforme que esconde la asimetría

El frontend llama a ese endpoint de copia **siempre** tras crear un borrador de reenvío, sea Gmail u Outlook. Para Outlook es un **no-op**: no copia nada y devuelve la lista actual de chips (los que el proveedor ya heredó). De este modo el código del composer es único y la asimetría queda escondida detrás de una sola llamada.

Ese endpoint de copia tiene dos propiedades importantes para la robustez:

- **Tolerante a fallos parciales**: responde siempre con éxito (incluso si algún adjunto no se pudo copiar) y reporta los que falló en una lista de "saltados", cada uno con su motivo (fuente no disponible, proveedor caído, tope de tamaño/cantidad alcanzado, ya copiado…). Un fallo en un adjunto no aborta el reenvío entero ni impide abrir el composer.
- **Idempotente**: reintentarlo tras un corte de red no vuelve a copiar lo ya copiado. La app recuerda de qué adjunto-origen viene cada copia y salta los que ya tiene.

Si la copia falla por completo (error de red en la llamada), el composer **se abre igualmente**: es un fallo blando y el usuario puede re-adjuntar a mano lo que necesite. Los códigos de "saltado" y los topes están en [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md).

#### Ejemplo

> El usuario reenvía un correo de Gmail con `contrato.pdf` (2 MB) y `anexo.docx` (500 KB). El composer se abre, llama al endpoint de copia, que descarga ambos del original y los re-adjunta: dos chips aparecen ya cargados. El usuario teclea un destinatario, añade `nota.txt` desde su disco y envía: los tres viajan juntos en el envío atómico de Gmail. Si el mismo reenvío fuera desde una cuenta Outlook, los dos chips habrían aparecido sin descargar nada (herencia server-side) y la llamada de copia habría sido un no-op.

---

## 6. Cómo se "engancha" la respuesta al hilo (threading)

Que una respuesta aparezca **dentro de la conversación** del original (y no como un correo suelto) es lo que aquí llamamos *threading*. Los dos proveedores lo resuelven de forma radicalmente distinta, y esa asimetría condiciona todo el flujo.

### 6.1 Gmail: triple requisito validado en local

Gmail exige **tres cosas a la vez** para meter un mensaje en un hilo existente:

1. El **identificador de hilo** (`threadId`) del mensaje coincide con el del original.
2. Las cabeceras **In-Reply-To / References** referencian el identificador del mensaje original (encadenamiento RFC 5322).
3. El **asunto coincide** (tras quitar los prefijos `Re:` / `Fwd:` y normalizar).

Si cualquiera de las tres falla, Gmail puede rechazar la operación con un error opaco. Para no depender de ese comportamiento, la app **verifica las tres condiciones en local sobre los datos del original ya leídos (paso 1), antes de crear el borrador en el proveedor**. Es decir, la única llamada al proveedor que precede a la guarda es la lectura del original; la validación se hace sin ninguna llamada *mutante* y corta antes de que se intente enganchar nada al hilo. Si algo no cuadra, el usuario recibe un error determinista con una razón concreta (desajuste de hilo, identificador no referenciado, o asunto que no coincide) en lugar de un fallo críptico del servidor. Las tres razones exactas están en [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md).

Esa validación del asunto se aplica **solo a Responder / Responder a todos**, no a Reenviar: un reenvío cambia legítimamente el asunto (`Fwd:`) y Gmail no exige que coincida, porque un reenvío puede iniciar un hilo nuevo. Aplicar la guarda de asunto al reenvío rompería todos los reenvíos contra hilos reales de Gmail.

Cuando el usuario envía, la app inyecta `In-Reply-To` / `References` en el correo saliente y le pasa a Gmail el `threadId`, de modo que tanto Gmail como cualquier cliente de destino (Apple Mail, Thunderbird) re-enhebran correctamente.

### 6.2 Outlook: el proveedor lo resuelve por su cuenta

Outlook no usa cabeceras para enhebrar: tiene endpoints dedicados de "responder" / "responder a todos" / "reenviar" que, al crear el borrador, **fijan la conversación en el servidor**. La app crea el borrador de respuesta a través de esos endpoints en **una sola llamada** (subject, cuerpo y destinatarios viajan en la misma petición — no hace falta un segundo paso para ajustar nada).

Como Outlook ya deja la conversación fijada en la creación, los identificadores de hilo y las cabeceras `In-Reply-To` / `References` **se aceptan en el contrato** (para que la firma sea simétrica con Gmail) **pero se descartan en el envío**: Graph no ofrece forma de inyectar esas cabeceras ni en la creación ni en el envío, y volver a mandarlas sería redundante. Persistirlas localmente es solo una previsión por si en el futuro hiciera falta re-hidratar el contexto.

### 6.3 Por qué esta asimetría importa

La consecuencia práctica: para Gmail la coherencia del hilo es responsabilidad de la app (de ahí la validación local); para Outlook es responsabilidad del proveedor (de ahí que la app no valide nada de threading en Outlook). Un cambio que aplicara la validación de Gmail a Outlook haría fallar todos los reenvíos; uno que dejara de inyectar las cabeceras en Gmail rompería el enhebrado en el cliente de destino cuando el destinatario no esté en Gmail.

---

## 7. Trampas conocidas

### 7.1 Outlook + editar el asunto de un reenvío

Si el usuario edita el `Asunto` de un reenvío de Outlook más allá del prefijo `Fwd:`, Graph **reasigna la conversación** al guardar el borrador, y el mensaje enviado se "desengancha" visualmente del hilo original en Outlook web. Es comportamiento **aceptado del MVP** y la app deliberadamente **no avisa** de ello. Gmail no lo sufre porque a un reenvío se le permite iniciar un hilo nuevo de todos modos.

### 7.2 Nombres de adjunto duplicados en un reenvío de Outlook

Si el correo original lleva dos adjuntos con el **mismo nombre**, la herencia server-side de Outlook puede dejar uno de ellos sin su identificador de proveedor en los registros locales; al enviar, ese adjunto se trataría como "pendiente de subir" y se re-subiría (bytes que el proveedor ya tiene). Es **vanishingly raro** y el peor caso es benigno: una re-subida redundante, sin pérdida de datos.

### 7.3 El "Forwarded" de Gmail no se ve en la app

Gmail web muestra un indicador de "Reenviado" en su interfaz, pero ese dato es **solo de UI** y no lo expone su API. Outlook tampoco ofrece un equivalente fiable. Por eso la app no puede pintar un distintivo "este correo fue reenviado" en el inbox sin recurrir a heurísticas sobre la cadena de cabeceras del original. Queda fuera del MVP.

### 7.4 Permisos OAuth necesarios

El flujo completo (crear el borrador de respuesta y luego enviarlo) requiere **dos permisos distintos** en la cuenta de Outlook: uno para crear/modificar (lectura-escritura de correo) y otro para enviar. Si falta uno, el fallo aparece en momentos diferentes (al crear el borrador o al enviarlo). Los nombres exactos de los scopes y la nota equivalente de Gmail están en [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md).

---

## 8. Validaciones y errores que ve el usuario

- **Destinatario obligatorio en Responder / Responder a todos**: como el borrador se crea de inmediato (§ 1) con los destinatarios ya precargados (§ 2), el "Para" nunca llega vacío a la creación. Si el usuario lo borra después, es el **botón Enviar** el que se bloquea (igual que en cualquier composición); la creación no se vuelve a intentar. En Outlook hay además una barrera en la propia creación —su endpoint `createReply` / `createReplyAll` exige al menos un destinatario—, pero por el orden del flujo eso solo se notaría si se intentara crear el borrador con el "Para" ya vacío. Reenviar no tiene esta barrera (ver siguiente punto y [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md)).
- **Reenviar admite destinatarios vacíos al crear**: el borrador de reenvío se crea sin destinatarios (el usuario los teclea después). Al **enviar**, en cambio, sí hace falta al menos uno, como en cualquier correo.
- **Dirección con formato inválido**: si el usuario teclea una dirección mal formada (sin `@`, sin dominio…), el botón Enviar se bloquea y el composer muestra un aviso neutro, sin llegar a llamar al proveedor.
- **La cuenta del correo ya no existe**: si al abrir Responder/Reenviar la cuenta de ese correo ya no pertenece al usuario, la app lo indica y no abre el composer.
- **El correo original ya no está**: si el original fue borrado entre que se listó y que se pulsa Responder, la preparación del contexto devuelve "no encontrado" sin gastar una llamada al proveedor.

Un matiz de seguridad relevante para Reenviar entre cuentas: aunque hoy el reenvío está **bloqueado a la misma cuenta** (la del correo original), la copia de adjuntos valida la propiedad de la cuenta-origen y, si no pertenece al usuario, responde "no encontrado" (404, no 403) para no filtrar qué cuentas existen.

---

## 9. Interacción con buzones unificados y bandejas ficticias

Responder/Reenviar funciona también cuando el correo se está viendo dentro de una **vista unificada** o una **bandeja ficticia** que agrega cuentas de varios buzones reales. En ese caso, todas las llamadas del composer (crear borrador, copiar adjuntos, enviar) se dirigen al **buzón real del correo**, no al buzón de la URL. Sin esto, responder a un correo cuya cuenta vive en otro buzón fallaría con "cuenta no encontrada". El detalle de por qué el correo lleva su buzón real es transversal y está documentado en [listado-de-correos.md](listado-de-correos.md) (§ 8) y [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) (§ 5); aquí basta saber que responder/reenviar respeta el origen real del correo aunque se vea en una vista agregada.

---

## 10. Resumen en una frase

> Responder / Responder a todos / Reenviar abren el composer de borradores ya relleno y enganchado al correo original —calculando destinatarios (con Reply-To y "quítame del CC" en Responder a todos), prefijo de asunto (`Re:` / `Fwd:`) y cita **con formato** (línea de atribución + el original, degradado a texto, dentro de un recuadro de cita diferenciado)—, creando el borrador en el proveedor de inmediato; el enhebrado al hilo es asimétrico (Gmail exige el triple requisito `threadId` + `In-Reply-To`/`References` + asunto, validado en local antes de tocar el proveedor; Outlook lo fija server-side en una sola llamada y descarta las cabeceras en el envío); Responder no arrastra adjuntos y Reenviar sí (heredados server-side en Outlook, descargados y recopiados de forma idempotente y tolerante a fallos en Gmail, tras una llamada uniforme que es no-op en Outlook); y arrastra consigo trampas aceptadas como el reenvío de Outlook que se desengancha del hilo si editas el asunto. Las cifras exactas, los códigos de error y todo lo que deliberadamente no soporta viven en [../limits/responder-y-reenviar.md](../limits/responder-y-reenviar.md).
