# Borradores — comportamiento (MVP)

Este documento describe **qué hace** MailManager con los borradores y **qué experimenta** el usuario: cómo se crean, se editan, se guardan, se listan, se sincronizan con el proveedor, se envían y se borran (incluido el borrado en bloque). No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

Los topes numéricos exactos (cuántos borradores se bajan por sincronización, reintentos, longitudes, paginación) y la lista de "lo que NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/borradores.md](../limits/borradores.md)**. Este fichero solo menciona los límites de pasada y enlaza a ese catálogo cuando hace falta.

### Qué NO cubre este documento (fronteras)

El borrador es la pieza central de un ecosistema más amplio; para no duplicar, varias partes viven en otros documentos:

- **El composer en sí** (la ventana de redacción: campos, validación de destinatarios, envío directo de un "Nuevo mensaje") está en **[composicion-y-envio.md](composicion-y-envio.md)**. Aquí se describe el borrador como **entidad persistida** y su ciclo de vida.
- **Responder / Responder a todos / Reenviar** (cómo se prerrellena el borrador a partir de un correo original, el threading, la herencia de adjuntos) está en **[responder-y-reenviar.md](responder-y-reenviar.md)**. Aquí solo se menciona que esos flujos crean borradores por el mismo camino.
- **Los adjuntos de un borrador** (añadir, quitar, lazy push, subida atómica vs. reanudable al enviar) están en **[adjuntos.md](adjuntos.md)**. Aquí se trata el borrador *sin* sus adjuntos, salvo para señalar dónde se entrelazan los ciclos de vida.

---

## 1. Qué es un borrador en MailManager

Un borrador es un correo a medio escribir que vive **en dos sitios a la vez**: en el proveedor (Gmail u Outlook) y en la base de datos local de MailManager. La regla que gobierna esa duplicidad es la **Regla Provider-First**: cualquier operación que cambie el estado del borrador se hace **primero en el proveedor** y solo si el proveedor la acepta se aplica en la base local. Nunca al revés. Así, lo que MailManager muestra siempre refleja lo que de verdad existe en el buzón del usuario.

Esto tiene una consecuencia importante y deliberada: un borrador creado, editado o borrado desde MailManager **aparece, cambia o desaparece también en Gmail/Outlook web**, igual que si el usuario lo hubiera tocado allí. No es una copia privada de la app; es el mismo borrador.

La app cubre el ciclo de vida completo:

1. **Crear** un borrador (en blanco, o como respuesta/reenvío).
2. **Editar** su contenido y volver a guardarlo.
3. **Listar** los borradores de una cuenta o de todo un mailbox.
4. **Sincronizar** los borradores desde el proveedor hacia la base local.
5. **Enviar** un borrador (se convierte en correo enviado y deja de ser borrador).
6. **Borrar** uno o varios borradores.

Funciona igual para cuentas Gmail y Outlook salvo en los puntos donde un proveedor impone un comportamiento distinto; esas asimetrías son la parte más interesante y están marcadas a lo largo del documento.

---

## 2. Crear un borrador

### 2.1 Las tres formas de que nazca un borrador

Un borrador puede nacer de tres maneras, pero **todas pasan por la misma puerta** (la creación en el proveedor seguida de la persistencia local):

- **Explícitamente, con "Guardar borrador"**: el usuario abre el composer ("Nuevo borrador" o "Nuevo mensaje"), escribe, y pulsa "Guardar borrador". La primera vez se crea; las siguientes se actualiza (sección 3).
- **Silenciosamente, al adjuntar el primer archivo**: si el usuario está en "Nuevo mensaje"/"Nuevo borrador" y arrastra un adjunto antes de haber guardado nada, la app crea un borrador "vacío" en el proveedor para tener un identificador real al que asociar el adjunto. Es invisible dentro de MailManager (no sale en la lista de borradores hasta que el usuario pulse "Guardar"), pero **sí queda creado en Gmail/Outlook web**. El detalle completo de este "bootstrap silencioso" está en [adjuntos.md](adjuntos.md) y [composicion-y-envio.md](composicion-y-envio.md).
- **Como respuesta o reenvío**: abrir Responder / Responder a todos / Reenviar crea el borrador de inmediato (prerrellenado con destinatarios, asunto citado y cuerpo). El detalle vive en [responder-y-reenviar.md](responder-y-reenviar.md).

### 2.2 Un borrador puede estar completamente vacío

A diferencia del envío directo (que exige asunto, cuerpo y al menos un destinatario), **un borrador admite todos los campos vacíos**: sin destinatarios, sin asunto, sin cuerpo. Es deliberado y coincide con lo que hacen Gmail y Outlook de forma nativa: guardar una idea a medias es justamente para lo que sirve un borrador.

### 2.3 Qué campos guarda un borrador

Un borrador conserva los destinatarios "Para", "Cc" y "Cco", el asunto y el cuerpo. El cuerpo es **HTML con formato**: se redacta con el editor enriquecido descrito en [composicion-y-envio.md](composicion-y-envio.md) § 3 (negrita, cursiva, subrayado, listas, enlaces). El formato **se conserva al guardar el borrador y volver a abrirlo**, incluido el viaje de ida y vuelta a través del proveedor: lo que se guardó se ve igual al reabrirlo. Antes de guardarse, el cuerpo pasa por el mismo saneamiento de seguridad del servidor que el envío directo (el contenido persistido ya está limpio).

**Borradores heredados** (creados cuando el cuerpo era texto plano, o desde otro cliente): al abrirlos ahora se ven **correctamente, con sus saltos de línea respetados**, sin romperse ni mostrar etiquetas en crudo. La app convierte ese texto plano a HTML al leerlo (escapando el marcado y traduciendo los saltos de línea), de modo que el editor lo recibe como contenido con formato. Esa conversión cubre tanto los borradores que ya estaban en la base local (migrados de una vez) como cualquiera que vuelva a entrar desde el proveedor en una sincronización.

Además, si el borrador es una respuesta o un reenvío, guarda en silencio la información de threading necesaria para que el correo, al enviarse, se enganche al hilo original. Esa información no se muestra en ninguna parte del composer hoy, pero se conserva (el porqué y la trampa asociada están en la sección 6.4 y en [responder-y-reenviar.md](responder-y-reenviar.md)).

#### Ejemplo

> El usuario pulsa "Nuevo borrador", escribe solo el asunto "Pendiente: revisar contrato" y cierra. La app crea el borrador en Gmail con cuerpo y destinatarios vacíos. Al abrir Gmail web, ese borrador aparece con su asunto y el resto en blanco. Al volver a MailManager y sincronizar, sigue ahí.

---

## 3. Editar un borrador

Al hacer clic en un borrador de la lista, el composer se abre **prerrellenado** con todo su contenido (destinatarios, asunto, cuerpo) y sus adjuntos como chips ya cargados. A partir de ahí el usuario edita y vuelve a guardar.

> **La firma no se reinserta al editar un borrador existente.** El cuerpo se muestra **tal cual se guardó** (incluida la firma que llevara al crearse); reabrirlo no añade una firma encima. Esto evita duplicarla cada vez que se reabre el borrador. La firma sí se inserta al **crear** correos/borradores nuevos y al responder/reenviar — ver [firma.md](firma.md).

### 3.1 Guardar es un reemplazo completo, no un parche

Cuando el usuario guarda un borrador existente, la app **sustituye el borrador entero** por la versión actual del composer: manda todos los campos y el proveedor sobrescribe el borrador con exactamente esos valores. No es una actualización campo a campo. Si el usuario borró el asunto, el borrador se queda sin asunto. Esto vale para los dos proveedores.

### 3.2 La cuenta de origen no se puede cambiar en un borrador existente

Una vez que un borrador existe (tiene identificador de proveedor), el **selector de cuenta de origen queda bloqueado**. Cambiar de cuenta implicaría mover el borrador —y sus posibles adjuntos— a otra cuenta del proveedor, algo que la app no hace. Si el usuario quiere mandar desde otra cuenta, descarta y empieza de cero. El mismo bloqueo aplica tras el bootstrap silencioso de un "Nuevo mensaje" (sección 2.1).

### 3.3 Guardar solo si hace falta

La app distingue si el contenido del composer ha cambiado respecto a la última versión guardada. Al enviar un borrador existente, solo lo reescribe en el proveedor **si está "sucio"** (hay cambios sin guardar); si no, manda directamente sin un guardado redundante. Es una optimización transparente para el usuario.

### 3.4 Cerrar el composer con cambios pendientes

El comportamiento del cierre (diálogo "Guardar / Descartar / Cancelar", qué hace "Descartar" con el borrador del proveedor) es **común con el composer** y está descrito en [composicion-y-envio.md](composicion-y-envio.md) y [adjuntos.md](adjuntos.md). Lo relevante aquí: "Descartar" sobre un borrador existente ejecuta un **borrado real** en el proveedor (sección 7), de modo que no queden restos en Gmail/Outlook web.

---

## 4. Listar borradores

### 4.1 Dos alcances: por cuenta o por mailbox

MailManager muestra los borradores en dos vistas:

- **Borradores de una cuenta concreta**: solo los de esa cuenta.
- **Borradores de todo el mailbox** (vista unificada): los de todas las cuentas del mailbox juntos, mezclados.

En ambos casos la tabla muestra, por fila, el remitente (proveedor/cuenta), el primer destinatario "Para", la cuenta de origen ("De"), el asunto y la fecha de última modificación. Un borrador sin destinatario se muestra como "(Sin destinatario)" y uno sin asunto como "(Sin asunto)".

### 4.2 El listado es una lectura puramente local

Pintar la lista de borradores **no llama al proveedor**: lee solo de la base local. Por eso es instantáneo. La parte que sí habla con el proveedor es la **sincronización** (sección 5), que es un paso separado.

### 4.3 Orden y ausencia de tope en la lectura

Los borradores se listan **del más reciente al más antiguo** por fecha de creación. La lectura local **no impone un tope** propio de cuántos borradores devuelve: muestra todos los que haya en la base local. El único tope que existe es el de la **sincronización** (cuántos se bajan del proveedor), no el de la visualización — ver [../limits/borradores.md](../limits/borradores.md).

### 4.4 Selección y acciones en bloque

La tabla permite seleccionar borradores con casillas, incluida una casilla de cabecera que selecciona los más recientes hasta un tope (ver [../limits/borradores.md](../limits/borradores.md)). Con una selección activa aparece una barra de acciones para **borrar en bloque** (sección 7.2).

---

## 5. Sincronizar borradores con el proveedor

### 5.1 Qué hace la sincronización

Sincronizar trae los borradores **desde el proveedor** y los vuelca en la base local. Es lo que mantiene a MailManager al día con cambios hechos fuera de la app: borradores creados o editados en Gmail/Outlook web, en el móvil, etc.

La sincronización se dispara de dos formas:

- **Automáticamente al entrar** en la vista de borradores (al montar la pantalla).
- **Manualmente** con el botón "Sincronizar" de la cabecera de la tabla (que gira mientras trabaja).

Si se entra en la vista de una cuenta concreta, sincroniza solo esa cuenta; si se entra en la vista del mailbox, sincroniza **todas** las cuentas del mailbox.

### 5.2 Es una sincronización "espejo" (replace), no un merge

La sincronización **reemplaza** el estado local de cada cuenta por el del proveedor: marca/actualiza todos los borradores que el proveedor devuelve y **borra de la base local los que el proveedor ya no tiene**. Es decir, si un borrador se eliminó en Gmail web, la siguiente sincronización lo hace desaparecer también en MailManager. No es una fusión que acumule; es un espejo del proveedor.

Esto se hace de forma atómica por cuenta: la actualización y el borrado de los ausentes ocurren en la misma transacción, así que la lista nunca queda en un estado intermedio raro.

#### Ejemplo

> En MailManager hay 3 borradores de una cuenta Gmail. El usuario entra en Gmail web, borra uno y crea dos nuevos. Al volver a MailManager y sincronizar, la lista pasa a mostrar 4: los 2 que quedaban, más los 2 nuevos; el borrado desaparece.

### 5.3 Solo los más recientes (tope por cuenta)

La sincronización **no baja borradores ilimitados**: hay un tope de cuántos borradores por cuenta se traen, y son siempre **los más recientes**. La cifra exacta y sus matices están en [../limits/borradores.md](../limits/borradores.md). En la práctica, si una cuenta tiene cientos de borradores antiguos, la app trabaja con los más nuevos.

### 5.4 Asimetría Gmail vs Outlook en "los más recientes"

Los dos proveedores respetan el mismo tope, pero definen "reciente" de forma distinta:

- **Outlook** ordena explícitamente por fecha de última modificación descendente, así que el tope significa, sin ambigüedad, "los N borradores editados más recientemente".
- **Gmail** no ofrece un parámetro de ordenación en su API de borradores; devuelve los borradores en orden cronológico inverso **por convención** (comportamiento observado, no garantizado por la documentación). Mientras esa convención se mantenga, el tope significa lo mismo que en Outlook. Es una dependencia frágil documentada como tal.

### 5.5 Errores por cuenta no tumban toda la sincronización

En la vista de mailbox, si la sincronización de una cuenta falla (token caducado, proveedor caído), el fallo se acumula por cuenta en lugar de abortar las demás. La app surfacea el problema al usuario, pero el resto de cuentas sí se sincronizan.

---

## 6. Enviar un borrador

### 6.1 El borrador se convierte en correo enviado

Al enviar un borrador, la app lo manda **primero en el proveedor** y solo si el envío tiene éxito aplica los cambios locales: borra la fila del borrador (porque ya no es un borrador) y guarda la metadata del correo enviado en la bandeja de enviados. Si el envío falla, el borrador se queda intacto y se muestra el error.

El borrado de la fila local y el guardado de la metadata del enviado son **best-effort**: si fallan después de un envío exitoso, se registran pero no convierten un envío correcto en un error de cara al usuario (el correo ya salió). La siguiente sincronización reconcilia cualquier desajuste.

### 6.2 Reintentos del envío

El envío de un borrador **reintenta automáticamente** ante fallos transitorios (hasta un número fijo de intentos con esperas crecientes). Las cifras exactas y la asimetría de qué errores se reintentan en cada proveedor están en [../limits/borradores.md](../limits/borradores.md).

### 6.3 Asimetría del identificador tras enviar

Un detalle que importa a quien depure el sistema:

- **Gmail** devuelve un **identificador nuevo** para el correo enviado (distinto del identificador del borrador) y borra el borrador automáticamente por su cuenta.
- **Outlook** conserva el **mismo identificador** del borrador para el mensaje enviado, gracias a que todas las llamadas usan el "ID inmutable".

Por eso el código y las pruebas nunca asumen que el identificador del enviado coincide con el del borrador: depende del proveedor.

### 6.4 El threading de respuestas/reenvíos se decide en el servidor, no en el cliente

Cuando el borrador es una respuesta o un reenvío, la información de threading que viaja en el envío se lee **de la fila local del borrador**, no de lo que mande el cliente en ese momento. El frontend, de hecho, **omite a propósito** esos campos al guardar/enviar: la fila es la única fuente de verdad. Esto evita que un cliente manipulado pueda reescribir el hilo en el momento del envío. El detalle completo (y por qué Gmail los inyecta en el envío mientras Outlook ya tiene el hilo resuelto) está en [responder-y-reenviar.md](responder-y-reenviar.md).

### 6.5 "Enviar" desde un "Nuevo mensaje" con adjuntos

Cuando el usuario pulsa "Enviar" en un "Nuevo mensaje" que ya tiene adjuntos (y, por tanto, un borrador silencioso creado), la app reencamina internamente la acción al **envío de borrador** en lugar del envío directo, para que los adjuntos viajen en el mismo envío. Es transparente para el usuario; el detalle está en [adjuntos.md](adjuntos.md) y [composicion-y-envio.md](composicion-y-envio.md).

---

## 7. Borrar borradores

### 7.1 Borrado individual

Borrar un borrador lo elimina **primero en el proveedor** y solo entonces en la base local (Provider-First). Si el borrador tenía adjuntos, se van con él (el almacenamiento local los limpia en cascada). Un borrado sobre un borrador que ya no existe localmente se trata como "no encontrado" (404), no como un error de servidor: borrar algo que ya no está no es un fallo.

### 7.2 Borrado en bloque

Desde la lista, con varios borradores seleccionados, "Eliminar" pide **confirmación** ("¿Eliminar N borradores? Esta acción no se puede deshacer.") y lanza el borrado de todos. Cada borrado es independiente: la app no aborta el lote si uno falla. Al terminar:

- Si **todos** se borraron, limpia la selección y refresca la lista.
- Si **algunos** fallaron, informa de cuántos de cuántos no se pudieron borrar (y, si fallaron todos, muestra el motivo del primer fallo). Los que sí se borraron quedan borrados.

#### Ejemplo

> El usuario selecciona 5 borradores y pulsa "Eliminar". Confirma. Cuatro se borran y uno falla porque su cuenta perdió el token. La app muestra "1 de 5 borradores no se pudieron eliminar"; los otros 4 desaparecen de la lista.

### 7.3 "Descartar" en el composer también borra

Como se adelantó en 3.4, elegir "Descartar" al cerrar un borrador existente ejecuta un borrado real en el proveedor, no solo un cierre local. Es la forma de no dejar borradores huérfanos —ni vacíos del bootstrap silencioso— en Gmail/Outlook web.

---

## 8. Asimetrías Gmail vs Outlook (resumen)

Las diferencias de proveedor están repartidas por el documento; este es el mapa rápido:

| Aspecto | Gmail | Outlook |
|---|---|---|
| Identificador del borrador | Cambia al enviar (el enviado tiene id nuevo); Gmail borra el borrador solo | Estable gracias al "ID inmutable"; el enviado conserva el id |
| "Borradores más recientes" en la sincronización | Orden por convención (no garantizado por la API) | Orden explícito por fecha de modificación |
| Cabecera de identidad inmutable en cada llamada | No aplica | Obligatoria en **todas** las llamadas (crear, editar, borrar, enviar) o el id deja de resolver |
| Threading de respuestas/reenvíos | Se inyecta en el envío (cabeceras + hilo) | Lo resuelve el servidor al crear el borrador de respuesta/reenvío |
| Herencia de adjuntos al reenviar | Descarga y recopia | Copia en el servidor | 

Los dos últimos puntos se detallan en [responder-y-reenviar.md](responder-y-reenviar.md) y [adjuntos.md](adjuntos.md).

---

## 9. Resumen en una frase

> Un borrador en MailManager es el **mismo** borrador que existe en Gmail/Outlook —no una copia privada—, gobernado por la Regla Provider-First: se crea, edita, envía y borra primero en el proveedor y luego en la base local; admite estar completamente vacío, guarda el cuerpo como **HTML con formato** (que sobrevive al ida y vuelta del proveedor, y rehidrata los borradores antiguos de texto plano sin romperlos), se guarda por reemplazo total, se lista de forma instantánea y local del más reciente al más antiguo, y se sincroniza como un espejo del proveedor (trae solo los más recientes hasta un tope y borra en local los que el proveedor ya no tiene); al enviarlo se convierte en correo enviado —con identificador nuevo en Gmail y estable en Outlook— y deja de ser borrador; y se puede borrar de uno en uno o en bloque tolerando fallos parciales. Las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/borradores.md](../limits/borradores.md).
