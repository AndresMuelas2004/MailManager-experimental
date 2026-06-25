# Acciones sobre correos — comportamiento (MVP)

Este documento describe **qué hace** la app cuando el usuario actúa sobre uno o varios correos ya recibidos: marcarlos como leídos o no leídos, moverlos a la papelera, marcarlos como spam (o sacarlos de spam), **archivarlos** (o desarchivarlos), restaurarlos, y borrarlos definitivamente. También cubre cómo se comportan estas mismas acciones cuando se aplican **en bloque** a una selección de correos. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

El toggle de **favorito** es una acción por correo aparte, con su propia semántica (Provider-First estricto, ortogonal al buzón y al estado de lectura); vive en su propio documento: [favoritos.md](favoritos.md). Aquí solo aparece de pasada porque comparte la fila y la barra de la tabla.

Los topes numéricos exactos (cuántos correos abarca una selección "todos", cómo se trocean las llamadas, qué reintenta cada proveedor, qué NO se soporta) viven en un documento aparte para no repetir cifras aquí: **[../limits/acciones-sobre-correos.md](../limits/acciones-sobre-correos.md)**. Este fichero solo los menciona de pasada y enlaza a ese catálogo cuando hace falta.

> **Importante — dónde se disparan estas acciones hoy.** El **qué** hace cada acción (mover a papelera, spam, archivar, leído/no leído, restaurar, borrar) y su lógica Provider-First **no cambian** y son el grueso de este documento. Sobre el **dónde**: las bandejas reales y la unificada se agrupan por conversación pero **conservan la selección múltiple y la barra de acciones masivas** (que actúan sobre el mensaje más reciente de cada hilo — ver [conversaciones.md](conversaciones.md)); la pestaña de **Favoritos** no agrupa y también la conserva. Las **bandejas ficticias** son la excepción: su fila es de **solo lectura** (no hay selección ni barra), y allí estas acciones se disparan **por mensaje desde el visor de la conversación** (cada mensaje expandido trae sus botones Favorito / No leído / Spam / Papelera y, según dónde esté, Archivar o Desarchivar). Ese visor por mensaje existe como vía complementaria en **todas** las bandejas que agrupan.

---

## 1. Qué acciones existen y dónde aparecen

La app ofrece varias acciones de estado sobre correos, más sus inversas:

- **Marcar como leído / no leído** — alterna el estado de lectura.
- **Mover a la papelera** — saca el correo de su bandeja y lo deja en la papelera (recuperable).
- **Marcar como spam / restaurar de spam** — mueve el correo a la carpeta de no deseados, o lo devuelve a la bandeja.
- **Archivar / desarchivar** — saca el correo de la bandeja de entrada sin borrarlo y lo lleva a "Archivados", o lo devuelve a la bandeja de entrada. Tiene su propia sección (§ 6-bis) por ser la acción nueva de esta familia.
- **Restaurar** (desde la papelera) — devuelve el correo a la bandeja de la que vino.
- **Eliminar definitivamente** — borra el correo de MailManager. Es la única acción con una semántica especial que merece su propia sección (§ 6): **no borra el correo del buzón real del proveedor**.

### 1.1 Dónde se invocan (según la bandeja)

1. **Abrir un correo / conversación** dispara automáticamente "marcar como leído" (§ 2.1): en una conversación se marcan de golpe **todos** los mensajes no leídos del hilo.
2. **Por mensaje, dentro del visor de la conversación** (bandejas reales, unificada y ficticias): cada mensaje expandido de la cadena ofrece **Favorito**, **No leído**, **Spam**, **Papelera** y —según dónde esté el mensaje— **Archivar** o **Desarchivar**, aplicados a ese mensaje concreto y enrutados a su buzón real. Es el camino principal para estas acciones en las bandejas que agrupan, y la **única** vía para archivar/desarchivar desde una bandeja ficticia (cuyas filas son de solo lectura). El botón **Archivar** solo aparece en mensajes que están en la bandeja de entrada (`ALL_MAIL`); el botón **Desarchivar**, solo en mensajes ya archivados. No hay "marcar leído" (abrir ya lo hizo) ni borrado definitivo en este visor. Ver [conversaciones.md](conversaciones.md).
3. **Selección múltiple y barra de acciones masivas** (en la **bandeja de cuenta**, la **unificada** y la pestaña de **Favoritos**; **no** en las bandejas ficticias): al marcar una o más casillas de la tabla, la cabecera se transforma en una **barra de acciones masivas** con los botones aplicables. Es el camino para mover a papelera, marcar spam, restaurar y borrar en bloque. Una selección de un solo correo usa exactamente la misma barra — no hay un menú "por fila" separado para estas acciones. En las bandejas que agrupan por conversación (cuenta y unificada), la selección de una fila actúa sobre el **mensaje más reciente** del hilo; ver [conversaciones.md](conversaciones.md).

### 1.2 Qué acciones se ofrecen según el buzón

La barra **no muestra siempre los mismos botones**: cada bandeja ofrece solo las acciones que tienen sentido en ella. Esto evita ofertas absurdas como "marcar spam" dentro de la propia carpeta de spam.

| Buzón | Acciones ofrecidas en la barra |
|---|---|
| Bandeja unificada / principal | Marcar leído·no leído, **Archivar**, Mover a papelera, Marcar spam |
| Enviados | Marcar leído·no leído, Mover a papelera |
| **Archivados** | Marcar leído·no leído, **Desarchivar**, Mover a papelera |
| Spam | Marcar leído·no leído, Mover a papelera, Restaurar de spam |
| Papelera | Marcar leído·no leído, Restaurar, Eliminar definitivamente |

Cuatro detalles deliberados de esta matriz:

- **Enviados no ofrece "marcar spam"**: marcar como spam un correo que tú mismo enviaste no tiene sentido.
- **"Archivar" solo se ofrece desde la bandeja de entrada** (principal / unificada / cuenta / Favoritos), nunca desde Enviados, Spam, Papelera ni desde la propia vista de Archivados —igual que "Marcar spam" no se ofrece dentro del propio Spam—. En Archivados la acción que aparece es **"Desarchivar"**; en Spam y Papelera, la forma de sacar un correo sigue siendo "Restaurar".
- **La papelera no ofrece "mover a papelera"** (ya está ahí) y es el **único** sitio donde aparece "Eliminar definitivamente".
- **"Marcar como leído" está en todas las bandejas**, incluida Archivados, la papelera y spam: el estado de lectura es ortogonal a dónde esté el correo.

---

## 2. Marcar como leído / no leído

### 2.1 Lectura automática al abrir

Cuando el usuario abre un correo que estaba **no leído**, la app lo marca como leído automáticamente, una sola vez, sin botón ni confirmación. Si el correo ya estaba leído, no se hace ninguna llamada. Es la semántica universal de cualquier cliente de correo.

> El usuario tiene tres correos en negrita (no leídos). Hace clic en el primero: se abre el contenido y, en segundo plano, ese correo pasa a leído. Al cerrar el visor, la fila ya no aparece en negrita. Los otros dos siguen en negrita.

### 2.2 Marcado manual en bloque

Desde la barra de acciones, el botón de leído/no leído **decide su sentido según la selección**: si en lo seleccionado hay tantos o más correos no leídos que leídos, el botón propone "Marcar como leídos"; si predominan los leídos, propone "Marcar como no leídos". Así, un clic hace lo que el usuario casi siempre quiere sobre un grupo mixto, sin obligarle a elegir dirección.

> El usuario selecciona cinco correos: tres no leídos y dos leídos. La barra muestra "Marcar como leídos". Un clic deja los cinco como leídos. Si vuelve a seleccionarlos, ahora el botón mostrará "Marcar como no leídos".

### 2.3 Reflejo en ambos lados

El estado de lectura se cambia **también en el proveedor** (Gmail / Outlook), no solo en MailManager. Si el usuario marca un correo como leído en MailManager y luego abre Gmail web, lo verá leído allí también. El cambio sigue la regla Provider-First: primero el proveedor, después la base de datos local (§ 7).

---

## 3. Mover a la papelera

Mover a la papelera saca el correo de su bandeja actual y lo deja en la papelera, **recordando de dónde vino** para poder devolverlo a su sitio exacto si se restaura (§ 5). Es una operación recuperable y no destructiva.

- Se aplica desde la barra de acciones en la bandeja principal, en Enviados y en Spam.
- El cambio se propaga al proveedor: el correo aparece en la papelera de Gmail / Outlook también.
- Es **idempotente**: volver a "mover a papelera" un correo que ya está en la papelera no hace nada raro ni lo duplica.

> El usuario está en la bandeja principal, selecciona dos correos y pulsa "Mover a papelera". Desaparecen de la bandeja principal y aparecen en la sección "Papelera". En Gmail web, esos dos correos también están ahora en la papelera.

### 3.1 Asimetría Gmail vs Outlook por debajo (transparente para el usuario)

El resultado visible es idéntico en ambos proveedores, pero el mecanismo difiere y conviene que el equipo lo sepa:

- En **Gmail**, mover a la papelera es un cambio de etiqueta y el identificador del correo **no cambia**.
- En **Outlook**, mover a la papelera es un movimiento de carpeta y Graph **reescribe el identificador** del mensaje. La app captura el nuevo identificador y lo propaga a su base de datos; si no lo hiciera, las acciones siguientes sobre ese correo fallarían. Esta reescritura de ID en cada movimiento es una particularidad de Outlook que afecta a papelera, spam y restauración por igual.

---

## 4. Marcar como spam y restaurar de spam

- **Marcar como spam** mueve el correo a la carpeta de no deseados del proveedor. Se ofrece únicamente en la bandeja unificada / principal: dentro de la propia carpeta de spam el botón "Marcar como spam" no aparece (ya están ahí), solo "Restaurar de spam" (ver la matriz de § 1.2).
- **Restaurar de spam** devuelve el correo a la bandeja principal y se ofrece solo dentro de la carpeta de spam.

Igual que la papelera, ambas son cambios de etiqueta en Gmail (sin cambio de ID) y movimientos de carpeta en Outlook (con reescritura de ID). El resultado se refleja en el proveedor.

Un matiz de alcance: **marcar como spam siempre devuelve el correo a la bandeja principal al restaurarlo**, no a la carpeta original concreta de la que salió. Es una simplificación aceptada; la papelera sí recuerda el origen exacto (§ 5) porque el caso de uso "deshacer un borrado" lo pide más a menudo.

---

## 5. Restaurar desde la papelera

Restaurar un correo de la papelera lo devuelve **a la bandeja de la que salió**, no a un destino genérico. La app guardó ese origen ("venía de la bandeja principal", "venía de Enviados"…) en el momento de moverlo a la papelera.

Hay un caso que la app resuelve con elegancia: correos que estaban en la papelera **antes** de que existiera el seguimiento de origen, o que llegaron ahí por una sincronización en vez de por un "mover a papelera" explícito. En esos correos no hay un origen guardado, así que al restaurarlos la app **pregunta al proveedor en qué carpeta han quedado** tras sacarlos de la papelera y persiste ese destino real, en lugar de inventarse uno. El resultado para el usuario es el mismo: el correo reaparece donde le corresponde.

> El usuario abre la papelera, selecciona un correo que había borrado ayer desde la bandeja principal y pulsa "Restaurar". El correo desaparece de la papelera y reaparece en la bandeja principal, su origen original.

### 5.1 Protección frente a carreras

Todas las operaciones de papelera (mover, restaurar, eliminar) solo tocan correos que **realmente están en el estado esperado** en el momento de ejecutarse. Si entre que el usuario seleccionó el correo y pulsó el botón una sincronización lo sacó de la papelera, la operación lo ignora silenciosamente en vez de corromper su estado. Es una salvaguarda invisible pero importante para un cliente multi-dispositivo.

---

## 6. Eliminar definitivamente — la excepción a Provider-First

Esta es la acción con el comportamiento menos obvio de toda la feature, y la **única excepción documentada** a la regla Provider-First del repositorio (que en todo lo demás obliga a tocar primero el proveedor y solo después la base de datos local).

### 6.1 Qué hace realmente

"Eliminar definitivamente" (disponible solo dentro de la papelera) borra el correo **únicamente de MailManager**. Concretamente:

- **No se llama al proveedor.** Ni Gmail ni Outlook reciben ninguna orden de borrado. El correo **sigue existiendo en la papelera real** del proveedor.
- En MailManager, el correo se marca internamente como eliminado y **deja de aparecer en cualquier vista** de la app: no está en la papelera, ni en la bandeja, ni en ningún buzón. Para el usuario, ha desaparecido.

Es decir: desde la app, el correo se ha ido para siempre; desde Gmail / Outlook web, sigue en la papelera hasta que la política de retención del proveedor lo purgue por su cuenta (Gmail lo limpia solo a los ~30 días; Outlook según la configuración del tenant).

> El usuario abre la papelera, selecciona un correo y pulsa "Eliminar". La app pide confirmación: *"¿Eliminar permanentemente este correo? Esta acción no se puede deshacer."* Al confirmar, el correo desaparece de MailManager por completo. Pero si el usuario abre Gmail web y mira la papelera, el correo **sigue ahí**.

### 6.2 Por qué es un no-op en el proveedor (y por qué es uniforme en ambos)

La razón nace de una limitación de Gmail: el permiso (scope) que la app usa para gestionar el correo **no autoriza el borrado permanente** de mensajes — eso requeriría un permiso mucho más amplio y sensible que el MVP no pide. Como uno de los dos proveedores no puede borrar de forma permanente sin una ampliación de permisos, la app adopta el **mismo no-op en ambos** para que el comportamiento sea consistente y predecible: un correo "eliminado" se comporta igual venga de una cuenta Gmail o de una Outlook. Aplicar borrado real solo en Outlook crearía una asimetría confusa.

### 6.3 El correo puede "reaparecer" si lo restauras en el cliente original

Como el borrado es solo local y el correo sigue vivo en la papelera del proveedor, hay una consecuencia que conviene tener clara:

- Un correo eliminado en MailManager queda marcado de forma **"pegajosa"**: una sincronización normal **no lo resucita**. Mientras el proveedor siga reportando ese correo como "en la papelera", MailManager respeta la decisión del usuario y lo mantiene oculto. Sin esta protección, el siguiente sync traería de vuelta justo lo que el usuario acababa de borrar.
- **Pero si el usuario lo restaura en el cliente original** (lo saca de la papelera en Gmail / Outlook web, devolviéndolo a la bandeja), el correo deja de estar "en la papelera" en el proveedor. En la siguiente sincronización, MailManager detecta que ya no está en la papelera, lo interpreta como *"el usuario lo ha restaurado manualmente"* y **lo vuelve a mostrar** en la bandeja correspondiente.

En una frase: borrar en MailManager oculta el correo localmente, pero el verdadero interruptor de "existe / no existe" sigue siendo el proveedor; restaurarlo allí lo trae de vuelta.

> El usuario elimina un correo desde la papelera de MailManager: desaparece de la app. Una semana después, desde Gmail web, mueve ese mismo correo de la papelera a su bandeja de entrada. En el siguiente refresco de MailManager, el correo reaparece en la bandeja principal.

### 6.4 La confirmación es obligatoria

A diferencia del resto de acciones (que son recuperables y no preguntan), eliminar definitivamente **siempre pide confirmación** con un diálogo del navegador, redactado en plural o singular según cuántos correos haya seleccionados. Es la única acción de esta feature que lo hace, precisamente porque desde la perspectiva de la app es irreversible.

---

## 6-bis. Archivar y desarchivar

Archivar es la acción estrella del flujo *Inbox Zero*: **sacar un correo de la bandeja de entrada sin borrarlo**, para dejar la bandeja limpia conservando el mensaje intacto y recuperable. Existe en Gmail, Outlook y Apple Mail con la misma semántica que el usuario ya conoce, y rellena un hueco real: hasta ahora la única forma de despejar la bandeja era mandar a la papelera (con riesgo de borrado) o marcar como spam (semánticamente erróneo).

### 6-bis.1 Qué hace

- **Archivar** mueve el correo de la bandeja de entrada a una nueva sección **"Archivados"** (un buzón más en el menú lateral, junto a Bandeja de entrada, Enviados, Favoritos, Spam, Borradores y Papelera). El correo **desaparece de la bandeja de entrada** pero no se borra: sigue accesible en "Archivados".
- **Desarchivar** hace lo inverso: devuelve el correo a la **bandeja de entrada**. Siempre vuelve a la bandeja de entrada, no "a la carpeta exacta de la que vino" — como archivar solo se hace desde la bandeja de entrada, el origen siempre es ese.

### 6-bis.2 Cómo se comporta

- **No destructivo y recuperable.** El correo nunca se borra; se puede desarchivar cuando se quiera. Por eso **no pide confirmación** (a diferencia de "Eliminar definitivamente"): es una acción inmediata y reversible.
- **Provider-First**, igual que papelera y spam (§ 7): primero se aplica el cambio en Gmail / Outlook y solo después en MailManager. Una operación masiva puede ser **parcial**: si de cinco correos el proveedor acepta cuatro, se archivan cuatro y el quinto queda como estaba.
- **Idempotente.** Archivar algo ya archivado, o desarchivar algo que ya está en la bandeja, no produce duplicados ni errores.
- **El favorito es ortogonal.** Un correo favorito que se archiva **sigue siendo favorito** y sigue apareciendo en la vista de Favoritos (igual que en Gmail un correo destacado y archivado sigue en "Destacados"). Archivar no quita el favorito.
- **El estado de leído es ortogonal.** Archivar no marca como leído ni cambia ese estado.

### 6-bis.3 La vista "Archivados"

Se comporta como las demás bandejas: lee de la copia local, agrupa por conversación, permite seleccionar y buscar con la lupa, y se pagina igual (ver [listado-de-correos.md](listado-de-correos.md)). No muestra contador de no leídos en el menú, igual que la Papelera (ver [contador-no-leidos.md](contador-no-leidos.md)).

### 6-bis.4 Reflejo real en el proveedor (y un cambio colateral en la bandeja principal)

Archivar en MailManager **archiva de verdad** en la cuenta del proveedor, y a la inversa: lo que el usuario archive en Gmail / Outlook web aparecerá archivado en MailManager tras la siguiente sincronización. El mecanismo difiere por proveedor (transparente para el usuario): en **Gmail** archivar es quitar la etiqueta de la bandeja de entrada y el identificador del correo **no cambia**; en **Outlook** es mover a la carpeta "Archivo" y Graph **reescribe el identificador**, que la app captura y propaga (la misma asimetría de papelera/spam — § 3.1).

Esto trae un **cambio de comportamiento colateral en la bandeja principal** que conviene conocer: antes, la bandeja de entrada de MailManager mostraba todo el correo recibido que no estuviera en Enviados, Spam ni Papelera, así que se "colaban" los correos que el usuario ya había archivado directamente en Gmail / Outlook. A partir de ahora la bandeja de entrada muestra **solo la bandeja de entrada real**; los correos archivados en el proveedor pasan a "Archivados" en la siguiente sincronización de cada cuenta. Es un cambio deseable (la bandeja de entrada pasa a ser de verdad la bandeja de entrada) pero visible: un usuario con correo archivado previo verá su bandeja "encoger" hacia su inbox real, de forma **gradual por cuenta** a medida que cada una se sincroniza. El detalle de esta reclasificación está en [listado-de-correos.md](listado-de-correos.md).

### 6-bis.5 Búsqueda y bandejas ficticias

- **Búsqueda (lupa).** Se añade el operador `in:archive`, que permite buscar entre los correos archivados desde cualquier bandeja, igual que `in:sent`, `in:trash`, etc. Ver [lupa.md](lupa.md).
- **Bandejas ficticias.** Los correos archivados **no aparecen** por defecto en una bandeja ficticia, igual que tampoco aparecen los de Papelera ni Spam (al archivar, el correo sale de las vistas "activas"). Si el usuario quiere verlos dentro de una ficticia, puede escribir `in:archive` en su lupa. Ver [bandejas-ficticias.md](bandejas-ficticias.md).

> El usuario, en la bandeja principal, selecciona tres correos y pulsa "Archivar". Desaparecen de la bandeja y aparecen en "Archivados"; en Gmail web ya no están en la bandeja de entrada. Uno de ellos era favorito: sigue marcado como favorito y sigue apareciendo en Favoritos. Más tarde abre "Archivados", selecciona ese correo y pulsa "Desarchivar": vuelve a la bandeja de entrada.

---

## 7. Cómo se aplican los cambios: Provider-First (y su excepción)

Salvo el borrado definitivo (§ 6), todas estas acciones siguen la **regla Provider-First**: la app llama primero al proveedor y solo persiste el cambio en su base de datos local **para los correos cuya llamada al proveedor tuvo éxito**. Si el proveedor rechaza o falla la operación de un correo, ese correo **no** se actualiza localmente. Así, la base de datos de MailManager siempre refleja el estado real del buzón del usuario en el proveedor.

Esto tiene un efecto observable: una operación sobre varios correos puede ser **parcial**. Si de cinco correos el proveedor acepta cuatro y falla uno, la app aplica el cambio a los cuatro que funcionaron y deja el quinto como estaba. Las operaciones reportan cuántos correos se vieron afectados realmente, no cuántos se pidieron.

El borrado definitivo es la excepción: al no llamar al proveedor, el cambio es puramente local desde el principio (§ 6).

---

## 8. Acciones masivas (en bloque)

### 8.1 Seleccionar correos

La tabla permite marcar correos uno a uno con sus casillas, o usar la casilla de la cabecera para **seleccionar de golpe toda la página actual** (la casilla de cabecera marca los correos de la página visible — la cifra está en [../limits/acciones-sobre-correos.md](../limits/acciones-sobre-correos.md), y coincide con el tamaño de página). No es "seleccionar todos los de todas las páginas": es el comportamiento más predecible y seguro para acciones masivas. En cuanto hay al menos un correo seleccionado, la cabecera de la tabla se reemplaza por la barra de acciones masivas, que muestra cuántos correos hay seleccionados y los botones aplicables al buzón actual (§ 1.2).

La casilla de cabecera es de tres estados: vacía (nada seleccionado), marcada (todos los de la página visible seleccionados) o intermedia (selección parcial).

### 8.1.1 La selección se conserva al cambiar de página

Con la lista paginada (ver [listado-de-correos.md](listado-de-correos.md) § 7), la selección **sobrevive a la navegación entre páginas**: si el usuario marca tres correos en la página 1 y pasa a la página 2, esos tres siguen seleccionados, y una acción en bloque los afecta a **todos** aunque estén en páginas distintas. El contador de la barra refleja el total seleccionado, no solo lo visible. Por eso un usuario puede acumular una selección **mayor que una página** marcando correos en varias páginas, aunque ninguna pulsación de "seleccionar todo" abarque más de la página actual.

La selección **se limpia** en dos situaciones: tras ejecutar cualquier acción (y entonces la lista se refresca), y al **cambiar de búsqueda** (cambia el contexto, así que una selección hecha antes de filtrar no debe arrastrarse a un resultado que ya no muestra esas filas). Cambiar de bandeja o de cuenta también reinicia la selección porque la pantalla se vuelve a montar.

### 8.2 El reparto por buzón real: una llamada por grupo

Aquí está la decisión de diseño más importante de las acciones masivas, y nace de cómo funcionan las **bandejas que mezclan varias cuentas**.

Cada acción de estado en el backend está acotada a **un mailbox** (el identificador del mailbox viaja en la URL). Pero una selección puede contener correos de **varios mailboxes reales a la vez** — esto ocurre dentro de una bandeja unificada o, sobre todo, dentro de una bandeja ficticia cuyas cuentas pertenecen a mailboxes distintos. Cada correo "sabe" a qué mailbox real pertenece, dato que la app trae en el propio listado.

Por eso, ante una acción masiva, la app **agrupa los correos seleccionados por su mailbox real** y lanza **una llamada por grupo**, cada una con el par correcto (mailbox + sus correos). Si los 10 correos seleccionados pertenecen al mismo mailbox, es una sola llamada; si están repartidos entre tres mailboxes, son tres llamadas en paralelo.

La razón de fondo: el backend valida que cada cuenta pertenezca al mailbox de la URL. Una única llamada con el mailbox de la ruta fallaría con "cuenta no encontrada" para todos los correos cuya cuenta vive en otro mailbox. Agrupar por mailbox real es lo que hace que las acciones masivas funcionen sin fallos dentro de una bandeja multi-mailbox.

> Dentro de una bandeja ficticia que reúne cuentas de dos buzones distintos, el usuario selecciona seis correos (cuatro de un buzón, dos del otro) y pulsa "Mover a papelera". La app dispara **dos** llamadas a papelera, una por buzón, cada una con sus correos. Las seis filas acaban en la papelera correctamente. Una implementación ingenua de una sola llamada habría fallado las dos filas del segundo buzón.

### 8.3 Por qué importa no romper este reparto

Si una futura refactorización colapsara las acciones masivas en una sola llamada con el mailbox de la ruta, las selecciones que cruzan varios buzones fallarían **en silencio** (cada fila "ajena" devolvería un 404 de cuenta no encontrada). Es un comportamiento fácil de romper sin darse cuenta, por eso queda explícito aquí.

---

## 9. Interacción con la búsqueda, los favoritos y las bandejas ficticias

- **Búsqueda (lupa)**: las acciones operan sobre los correos seleccionados, filtrados o no (incluso si la selección abarca varias páginas — § 8.1.1). Si el usuario filtró con la lupa y selecciona resultados, las acciones se aplican exactamente a esa selección; cambiar el texto buscado limpia la selección. La lupa se documenta en [lupa.md](lupa.md).
- **Favoritos**: marcar/desmarcar favorito es ortogonal a estas acciones. Un correo favorito que se mueve a la papelera **o se archiva sigue siendo favorito**; el favorito viaja con el correo entre bandejas. El toggle de favorito tiene su propio documento ([favoritos.md](favoritos.md)) y su propia regla (Provider-First estricto, sin la excepción del borrado).
- **Bandejas ficticias**: las acciones funcionan igual sobre los correos listados en una bandeja ficticia, con el reparto por mailbox real de § 8.2 haciendo el trabajo pesado. Las bandejas ficticias se documentan en [bandejas-ficticias.md](bandejas-ficticias.md).

---

## 10. Resumen en una frase

> Las acciones sobre correos (leído·no leído, papelera, spam, **archivar/desarchivar** y sus inversas) siguen Provider-First —tocan primero Gmail / Outlook y solo persisten en local lo que el proveedor aceptó, de forma posiblemente parcial— y se ofrecen contextualmente según el buzón; **archivar** saca el correo de la bandeja de entrada sin borrarlo (recuperable, idempotente, sin confirmación, ortogonal a favorito y a leído) y lo lleva a la nueva vista "Archivados" desde la que se "Desarchiva" de vuelta a la bandeja de entrada; el **borrado definitivo es la única excepción** a Provider-First: es un no-op uniforme en ambos proveedores que solo oculta el correo localmente (sigue vivo en la papelera real del proveedor y reaparece si el usuario lo restaura en el cliente original y se re-sincroniza); y las acciones masivas agrupan la selección por su mailbox real y disparan una llamada por grupo para no fallar dentro de bandejas que mezclan varias cuentas. Las cifras exactas y lo que deliberadamente no se soporta están en [../limits/acciones-sobre-correos.md](../limits/acciones-sobre-correos.md).
