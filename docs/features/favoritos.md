# Características de favoritos — comportamiento (MVP)

Este documento describe **qué hace** la app cuando un usuario marca correos como favoritos: cómo se marca y desmarca un correo, cómo se ve la bandeja de Favoritos, qué pasa cuando se reconcilia el estado con el proveedor y qué casos borde están aceptados. No entra en código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

Para los topes exactos y la lista de "qué NO soporta", consulta [`../limits/favoritos.md`](../limits/favoritos.md).

---

## 1. Qué es un favorito en MailManager

Un favorito es una **marca personal** que el usuario pone sobre un correo concreto para encontrarlo rápido más tarde. Visualmente es la **estrella** que aparece al principio de cada fila del listado: rellena en ámbar cuando el correo es favorito, contorneada en gris cuando no lo es.

MailManager no inventa este concepto: lo mapea sobre lo que cada proveedor ya ofrece.

- En **Gmail**, un favorito es la etiqueta **`STARRED`** (la estrella de Gmail).
- En **Outlook**, un favorito es la **bandera de seguimiento** del mensaje (`flag`, con estado "marcado").

El usuario no tiene que saber nada de esto: marca la estrella en MailManager y el cambio se refleja tanto en MailManager como en Gmail/Outlook web, y al revés.

### 1.1 El favorito es ortogonal a la ubicación del correo

Esta es la decisión de diseño más importante de la feature, y conviene entenderla bien.

Cada correo tiene una **ubicación** (`box`): bandeja de entrada, enviados, archivados, spam o papelera. El estado de favorito es **independiente** de esa ubicación. Un correo puede estar a la vez en la papelera y marcado como favorito; puede moverse de la bandeja de entrada a spam, o **archivarse**, sin perder la estrella (un favorito archivado sigue apareciendo en Favoritos — ver [acciones-sobre-correos.md](acciones-sobre-correos.md) § 6-bis).

Esto se hizo así porque **los dos proveedores lo modelan exactamente igual**:

- En Gmail, la etiqueta `STARRED` convive con cualquier otra etiqueta (`INBOX`, `SENT`, `SPAM`, `TRASH`). Marcar una estrella no mueve el correo de carpeta.
- En Outlook, la bandera viaja con el mensaje aunque éste cambie de carpeta.

**Por qué no se modeló como un valor más de `box`**: si "favorito" fuese una ubicación más, marcar un correo como favorito borraría su ubicación real (perderíamos el dato de que ese correo estaba, por ejemplo, en Enviados). Mantenerlos separados respeta la realidad de ambos proveedores y permite la bandeja de Favoritos que mezcla correos de cualquier ubicación.

### 1.2 Ejemplo

- El usuario marca como favorito un correo que está en su bandeja de entrada → aparece con estrella ámbar en la bandeja **y** en la pestaña Favoritos **y** en Gmail web (con su estrella) o en Outlook web (con su bandera).
- Ese mismo correo se mueve a la papelera → sigue siendo favorito (la estrella se conserva), pero **deja de aparecer en la bandeja de Favoritos** por defecto (ver § 4.2).

---

## 2. Dónde puede el usuario marcar y desmarcar favoritos

La forma de marcar/desmarcar **depende de si la bandeja agrupa por conversación**:

- En la **pestaña de Favoritos** (la única que **no** agrupa), la estrella aparece al principio de cada fila y es **clicable directamente desde la lista**: un clic alterna el estado, aislado del resto de la fila (pulsar la estrella nunca abre el correo, y abrir el correo no toca la estrella).
- En la **bandeja de una cuenta**, la **unificada** y las **bandejas ficticias** —que se presentan agrupadas por conversación (ver [conversaciones.md](conversaciones.md))— la estrella de la fila es un **indicador agregado de solo lectura** (rellena si **algún** mensaje del hilo es favorito) y **no** se puede pulsar. Para marcar/desmarcar, el usuario **abre la conversación** y usa el botón **"Favorito"** que cada mensaje expandido trae en el visor: el favorito se alterna **por mensaje**, no por hilo.

Es decir, el toggle de favorito ahora vive en **dos** sitios según el contexto: la estrella clicable de la fila en Favoritos, y el botón por mensaje dentro del visor de la conversación en el resto de bandejas. (En la versión anterior la estrella era clicable en todas las filas y el visor no tenía botón de favorito; eso cambió con la vista de conversación.)

---

## 3. Cómo se comporta la estrella al marcar (respuesta instantánea + confirmación con el proveedor)

Cuando el usuario pulsa la estrella, la app aplica un **cambio optimista**: la estrella se rellena (o se vacía) **al instante**, sin esperar a que el proveedor confirme. El usuario no percibe la latencia de Gmail/Outlook.

Esta respuesta instantánea cubre **las dos formas de marcar** por igual (§ 2): la estrella clicable de la fila en la pestaña de Favoritos **y** el botón "Favorito" de cada mensaje del visor de la conversación. Este último es el punto donde más se nota la mejora: antes la estrella del visor **no daba ningún feedback** hasta recargar la vista (parecía que el clic "no hacía nada"), y ahora se rellena/vacía en el acto como cualquier otra. (Mientras tanto, en segundo plano la app reconcilia con el estado real del servidor, de modo que no hay parpadeo entre el cambio optimista y el refresco.)

Por debajo, la app sigue la **Regla Provider-First**: primero llama al proveedor para aplicar la etiqueta/bandera y **solo si el proveedor confirma** persiste el cambio en la base de datos local. El proveedor es la fuente de verdad; nunca guardamos un favorito que el proveedor rechazó.

### 3.1 Qué ve el usuario en cada desenlace

- **Todo va bien** (lo normal): la estrella ya estaba puesta optimistamente; al confirmar el proveedor, el listado se refresca con el dato real y la estrella se queda como está. Transparente.
- **El proveedor falla** (cuenta sin permisos, proveedor caído, correo que ya no existe en el proveedor): la app **revierte** la estrella a su estado anterior y muestra el error. El usuario ve que su marca "no cuajó" y puede reintentar. La base de datos local nunca llega a cambiar.

### 3.2 La marca es idempotente

Marcar como favorito un correo que **ya** era favorito (o desmarcar uno que ya no lo era) no produce ningún error: ambos proveedores tratan la operación como un no-op. El usuario puede pulsar dos veces seguidas sin romper nada.

### 3.3 Correo que ya no existe localmente

Si el usuario intenta marcar un correo que ya no está en la base de datos local (caso de carrera raro: el correo se borró entre que se pintó el listado y el clic), la app responde **"correo no encontrado"** sin gastar siquiera una llamada al proveedor. La comprobación de existencia local ocurre **antes** de tocar Gmail/Outlook, igual que en el resto de acciones por correo. Si la fila desaparece justo en el instante intermedio (entre la comprobación y la escritura), también se reporta como "no encontrado" en lugar de fingir un éxito.

---

## 4. La pestaña de Favoritos

Existe una pestaña dedicada que lista **únicamente** los correos marcados como favoritos. Es el equivalente a "Destacados" de Gmail o a la vista de elementos marcados de Outlook.

Favoritos aparece en **dos sitios** según desde dónde se mire, y los dos se comportan igual salvo por el alcance del listado:

- **A nivel de mailbox** (la entrada de "Favoritos" del menú lateral): lista los favoritos de **todas** las cuentas del mailbox.
- **A nivel de una cuenta concreta** (la entrada "Favoritos" de la barra lateral cuando el selector de cuentas está en una cuenta): lista solo los favoritos de **esa** cuenta. Su botón de sincronizar reconcilia solo la cuenta actual (ver § 5).

La vista de favoritos por cuenta es el cierre de la segunda mitad de § 4.1 de este mismo documento: la afirmación *"en una cuenta concreta, solo los de esa cuenta"* ya estaba escrita pero el código nunca la había implementado; ahora sí existe. No hubo cambios de backend ni nuevas llamadas al proveedor — reutiliza el mismo listado de favoritos filtrando por la cuenta.

### 4.1 Qué muestra

- Una cabecera "Favoritos" con su subtítulo (a nivel de mailbox, "Correos marcados con estrella en Gmail o con bandera en Outlook"; a nivel de cuenta, "Favoritos de" la cuenta).
- Un botón **"Sincronizar favoritos"** (ver § 5).
- Una **lupa de búsqueda** que filtra dentro de los favoritos (mismas reglas que la lupa general: literal, sin tildes/mayúsculas, mínimo 2 caracteres, debounce; ver [`lupa.md`](lupa.md)).
- La tabla de correos favoritos, ordenados por fecha de recepción descendente.

La pestaña hereda el modo del listado: en vista unificada de un mailbox lista los favoritos de **todas** las cuentas del mailbox; en una cuenta concreta, solo los de esa cuenta.

Como la pestaña mezcla correos recibidos y enviados, **cada fila resuelve por sí misma** qué columna mostrar ("De" para los recibidos, "Para" para los enviados) en lugar de asumir un único sentido para toda la tabla. Igual que la pestaña de mailbox, la de cuenta **no agrupa por conversación**, así que la estrella de cada fila es clicable directamente (no el indicador agregado de solo lectura de las bandejas agrupadas; ver § 2).

### 4.2 Spam y papelera quedan fuera por defecto

La bandeja de Favoritos **excluye** los correos que están en spam o en la papelera, aunque sigan marcados como favoritos. **Los archivados sí se muestran**: un favorito archivado sigue apareciendo en Favoritos (como en Gmail, un correo destacado y archivado sigue en "Destacados"). Es una asimetría deliberada — archivar no saca al correo del "flujo de favoritos" igual que sí lo hace mandarlo a spam o a la papelera.

**Por qué**: cuando el usuario manda un favorito a la papelera, implícitamente está diciendo "esto ya no está en mi flujo activo". La vista de Favoritos respeta esa decisión y no se los vuelve a poner delante. La estrella se conserva (no se pierde el favorito), simplemente no se lista aquí.

Una matización: las **bandejas ficticias** cuyo filtro no especifica ubicación excluyen por defecto spam, papelera **y archivados** — un superconjunto de la exclusión de Favoritos, que **no** descarta los archivados. Ver [bandejas-ficticias.md](bandejas-ficticias.md).

**Cómo verlos igualmente**: si el usuario quiere ver los favoritos que están en spam o en la papelera, tiene que pedir explícitamente esa ubicación. El listado solo trae favoritos de spam/papelera cuando se selecciona esa caja de forma explícita; en cualquier otro caso se queda en "todo menos spam y papelera".

### 4.3 Ejemplo del comportamiento de la caja

- Vista de Favoritos por defecto (ancla "todo menos spam/papelera") → trae los favoritos de la bandeja de entrada y de enviados, **no** los de spam/papelera.
- Vista de Favoritos pidiendo explícitamente "Enviados" → trae **solo** los favoritos que están en Enviados (no se cuelan los del resto de cajas). Esta separación estricta evita un bug sutil en el que la vista de "favoritos enviados" acababa mostrando también favoritos de la bandeja general.

---

## 5. Sincronizar favoritos con el proveedor

El botón **"Sincronizar favoritos"** existe porque MailManager y el proveedor pueden desincronizarse: el usuario pudo marcar una estrella desde Gmail web, desde el móvil, o desde otra app, sin pasar por MailManager. La sincronización **reconcilia** el estado local con lo que el proveedor considera la verdad.

### 5.1 Qué hace exactamente

1. Pregunta al proveedor **qué correos tiene marcados como favoritos** ahora mismo. El alcance depende de desde dónde se pulse el botón: desde la pestaña de Favoritos del **mailbox** pregunta a **cada cuenta** del mailbox; desde la pestaña de Favoritos de una **cuenta concreta** pregunta **solo a esa cuenta** (es lo coherente con una vista ya filtrada a una sola cuenta, y no toca el estado de las demás). La etiqueta del botón es la misma en ambos casos: "Sincronizar favoritos".
2. Para cada cuenta implicada, en una sola operación: marca como favoritos en la base de datos local **todos** los correos que el proveedor reporta como favoritos, y marca como **no favoritos** absolutamente todos los demás correos de esa cuenta.

Es una **reconciliación completa**, no un "añadir lo nuevo": si el usuario desmarcó una estrella en Gmail web, tras sincronizar ese correo deja de ser favorito también en MailManager.

### 5.2 La sincronización NO importa correos nuevos (decisión deliberada)

Esta es una asimetría importante y consciente. Si el proveedor reporta como favorito un correo que **MailManager todavía no tiene** en su base de datos local, la sincronización lo **ignora en silencio**. No crea una fila nueva.

**Por qué** (se evaluaron dos opciones; se eligió la "Opción A"):

- La llamada que respalda la sincronización solo devuelve **identificadores** de correos favoritos, no su contenido ni su metadata (asunto, remitente, fecha). Importar esos correos obligaría a una segunda ronda de llamadas por cada identificador, convirtiendo una reconciliación barata de etiquetas en una sincronización de metadata encubierta.
- Importar metadata nueva ya es responsabilidad de **otra** operación (la sincronización general de la bandeja). Hacerlo desde dos sitios distintos arriesga divergencias.
- El efecto visible para el usuario —"marqué un favorito en Gmail web que MailManager aún no había descargado"— es simplemente "el favorito aparece tras la próxima sincronización de la bandeja", que es aceptable para una reconciliación manual.

**Ejemplo**: el usuario marca en Gmail web un correo muy antiguo que MailManager nunca llegó a descargar. Pulsa "Sincronizar favoritos" en MailManager → ese correo **no** aparece en Favoritos todavía. Tiene que sincronizar primero la bandeja (que baja la metadata) y luego ya aparecerá como favorito.

### 5.3 Dos números que cuentan cosas distintas

La sincronización informa de dos cantidades que **casi nunca coinciden** y conviene no confundir:

- El total de **filas reconciliadas**, que es un agregado de **todo el mailbox**: la suma, sobre todas las cuentas sincronizadas, de cuántas filas se han tocado en cada una. Como la operación recorre y reescribe el estado de favorito de **todas** las filas de cada cuenta (poniendo unas a verdadero y el resto a falso en la misma transacción), este número es esencialmente "cuántos correos en total se han tocado".
- Los **favoritos que el proveedor reportó**, que sí se desglosa **por cuenta** (cada cuenta lleva su propio recuento).

**Ejemplo**: una cuenta con 100 correos almacenados de los cuales 3 están marcados en el proveedor → la sincronización reporta 100 filas reconciliadas y 3 favoritos del proveedor. Leer el primer número como "favoritos sincronizados" es el error natural: es el recuento de filas reconciliadas, no de favoritos. (Con varias cuentas en el mailbox, el total de filas es la suma de las filas de todas; el desglose por cuenta solo acompaña al segundo número.)

**Caso borde**: que el proveedor no reporte **ningún** favorito es válido y significa "esta cuenta no tiene favoritos" → la sincronización pone a no-favorito todas las filas de la cuenta y aun así informa del recuento completo de filas tocadas.

### 5.4 Qué ve el usuario

El botón muestra "Sincronizando…" con un icono girando mientras dura, y al terminar el listado se refresca solo. Si una cuenta falla la autenticación o el proveedor responde con error, la sincronización completa se aborta y se muestra el error. No hay confirmación de éxito con números en pantalla: el usuario percibe el resultado en el propio listado actualizado.

Tanto el listado de favoritos del proveedor (en la sincronización, ambos proveedores) como la propia marca/desmarca en Outlook **reintentan automáticamente ante fallos temporales** del proveedor (throttling "demasiadas peticiones", caídas momentáneas, hipos de red), respetando el tiempo de espera que el proveedor indique. El efecto para el usuario es que **fallan muchas menos veces** por un problema puntual: solo se ve el error cuando el fallo persiste tras los reintentos. (En Gmail la marca ya reintentaba; esta revisión cerró la asimetría llevando los reintentos también al lado Outlook y al listado de ambos proveedores. Los topes exactos de intentos están en [`../limits/favoritos.md`](../limits/favoritos.md).)

---

## 6. Multi-cuenta y bandejas ficticias

El estado de favorito se gestiona **por cuenta y por correo**, no por mailbox. Esto tiene una consecuencia práctica importante en vistas que mezclan cuentas de varios mailboxes reales (las bandejas ficticias):

- Cada correo lleva la información de **a qué mailbox real pertenece**. Cuando el usuario marca un favorito —sea con la estrella clicable de la fila en Favoritos, sea con el botón por mensaje dentro del visor de la conversación—, la app dirige la llamada al mailbox real que posee la cuenta de ese correo, **no** al mailbox de la URL. Esto es crítico para un hilo abierto desde una bandeja ficticia, cuyos mensajes pueden vivir en mailboxes distintos (ver [conversaciones.md](conversaciones.md)).
- Si no lo hiciera así, marcar como favorito un correo cuya cuenta vive en otro mailbox fallaría con "cuenta no encontrada", porque el backend valida que la cuenta pertenezca al mailbox indicado.

La sincronización funciona por mailbox o por cuenta: la pestaña de Favoritos del mailbox reconcilia todas las cuentas del mailbox actual; la pestaña de Favoritos de una cuenta reconcilia solo esa cuenta (§ 5.1). En ninguno de los dos casos hay reconciliación de un tirón de cuentas repartidas entre varios mailboxes reales (caso de una bandeja ficticia que abarca varios): eso requeriría disparar una sincronización por cada mailbox implicado.

---

## 7. No hay marca de favoritos en bloque

El MVP **no** tiene ninguna operación de favorito multi-selección. El favorito **solo** se alterna correo a correo —con la estrella clicable de su fila en Favoritos, o con el botón "Favorito" de cada mensaje dentro del visor de la conversación en el resto de bandejas (ver [conversaciones.md](conversaciones.md))—: no existe un botón de "marcar como favorito" en la barra de acciones en bloque (esa barra, presente solo en Favoritos, cubre papelera, leído/no leído y spam, pero no favoritos) ni hay un endpoint de marca por lotes en el backend.

**Por qué se dejó así**: las dos APIs (Gmail y Outlook) ofrecen operaciones de modificación por lotes que serían una mejora trivial de añadir más adelante y que no bloquean ninguna otra funcionalidad. No tenerlas mantiene la superficie de la API pequeña y el modelo de errores simple: no hay que diseñar un contrato de "éxito parcial" (qué pasa si 3 de 5 se marcan y 2 fallan). Para el volumen del MVP, alternar la estrella de una en una es suficiente.

Las implicaciones cuantitativas (ausencia de batch y de acción multi-selección) están en [`../limits/favoritos.md`](../limits/favoritos.md).

---

## 8. Robustez y casos borde

- **Cambio optimista con reversión**: como se explica en § 3, la estrella se actualiza al instante y se revierte sola si el proveedor rechaza el cambio. El usuario nunca se queda con una estrella "mentirosa" de forma permanente.
- **Reconciliación tras el fallo**: tras cualquier marca (tenga éxito o se revierta), la app vuelve a pedir al servidor el estado real de los listados afectados, de modo que la pantalla siempre acaba reflejando la verdad del backend.
- **Favorito que se mueve de caja**: marcar un favorito y luego moverlo a spam/papelera conserva la estrella pero lo saca de la vista de Favoritos por defecto (§ 4.2). Restaurarlo a la bandeja lo devuelve a la vista.
- **Idempotencia**: doble clic accidental, reenvío de la misma marca, o sincronizaciones repetidas no producen estados inconsistentes.

---

## 9. Qué pasa "por debajo" de un vistazo

### 9.1 Al marcar una estrella

1. El usuario pulsa la estrella → se rellena al instante (cambio optimista).
2. La app comprueba que el correo existe localmente; si no, corta con "no encontrado" sin llamar al proveedor.
3. La app pide al proveedor aplicar la etiqueta `STARRED` (Gmail) o la bandera (Outlook).
4. Si el proveedor confirma → se guarda el favorito en local y el listado se reconcilia.
5. Si el proveedor falla → la estrella se revierte y se muestra el error; la base de datos local no cambia.

### 9.2 Al sincronizar

1. El usuario pulsa "Sincronizar favoritos".
2. La app pregunta a cada cuenta del mailbox qué correos están marcados ahora mismo.
3. Por cada cuenta, en una sola transacción, marca como favoritos los que el proveedor reporta y desmarca todos los demás.
4. Los correos que el proveedor reporta pero que MailManager no tiene en local se ignoran (§ 5.2).
5. El listado se refresca con el estado reconciliado.

---

## 10. Resumen en una frase

> Un favorito es la estrella de Gmail (`STARRED`) o la bandera de Outlook proyectada como un estado **ortogonal a la ubicación** del correo, que el usuario marca y desmarca con respuesta instantánea (optimista, con reversión si el proveedor falla) desde cualquier listado; la pestaña de Favoritos lista los favoritos ordenados por fecha excluyendo por defecto spam y papelera, y existe en dos sitios —a nivel de mailbox (todas sus cuentas) y a nivel de una cuenta concreta (solo esa, pestaña entre "Enviados" y "Spam")—; el botón de sincronizar reconcilia por completo el estado local con el del proveedor sin importar correos nuevos (Opción A), con el mismo alcance que la vista desde la que se pulsa (todo el mailbox o solo la cuenta); y todo se aplica Provider-First, alternando la estrella correo a correo (sin acción multi-selección ni marca en bloque en el MVP), respetando idénticamente cómo modelan el favorito ambos proveedores.

Eso es todo lo que necesita saber un programador (o cualquier persona del equipo) para entender cómo se va a comportar la gestión de favoritos en el MVP.
