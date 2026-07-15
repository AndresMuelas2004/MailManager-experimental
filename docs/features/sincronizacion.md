# Sincronización de correos, borradores y favoritos — comportamiento (MVP)

Este documento describe **qué hace** MailManager cuando sincroniza con Gmail y Outlook: qué información se baja del proveedor y en qué momento, cómo se comporta la primera sincronización de una cuenta frente a las siguientes, y cómo se reconcilian borradores y favoritos. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la app cuando se siente delante de ella.

La sincronización es el latido de la aplicación. Casi todo lo que el usuario ve en un listado — el inbox, enviados, spam, papelera, borradores, favoritos — sale de una copia local que la app mantiene al día sincronizando con el proveedor. Entender qué entra en esa copia y cuándo es entender por qué la app se siente rápida y por qué a veces un correo recién llegado tarda un instante en aparecer.

Los topes numéricos exactos (cuántos correos baja la primera vez, cuántos eventos tolera antes de rehacer el trabajo, cuántos borradores trae, reintentos, concurrencia) y la lista de "lo que NO hace" viven en un documento aparte para no repetir cifras aquí: **[../limits/sincronizacion.md](../limits/sincronizacion.md)**. Este fichero solo menciona los límites de pasada y enlaza a ese catálogo cuando hace falta.

El detalle fino de la **sincronización de favoritos** (qué marca y qué deja intacto, por qué no importa correos nuevos) se trata en su propia feature: **[favoritos.md](favoritos.md)**. Aquí se cubre solo cómo encaja dentro del modelo general de sincronización.

---

## 1. El principio central: copia local primero, proveedor solo cuando hace falta

MailManager **no** pregunta al proveedor cada vez que el usuario abre un listado. En su lugar mantiene una copia local (en su base de datos) de la **metadata** de los correos, y los listados se pintan exclusivamente desde esa copia. El proveedor solo entra en juego en dos situaciones:

1. **Durante una sincronización**, para traer novedades y actualizar la copia local.
2. **Al abrir un correo concreto**, para bajar su cuerpo y sus adjuntos bajo demanda (eso pertenece a otra feature — ver [visualizacion-de-correos.md](visualizacion-de-correos.md) y [adjuntos.md](adjuntos.md)).

Esta separación es la razón de que abrir el inbox sea instantáneo y de que la búsqueda (la lupa) funcione aunque Gmail u Outlook estén caídos: todo lo que se lista ya está guardado localmente. El precio es que un correo recién llegado al proveedor no aparece **hasta** que ocurre una sincronización que lo baje.

### Qué es "metadata" y qué no lo es

Lo que la sincronización baja de cada correo es su **cabecera**: asunto, remitente (email y nombre), primer destinatario, fecha de recepción, si está leído, **si está marcado como favorito** (estrella de Gmail / bandera de Outlook), y en qué bandeja vive (principal, enviados, archivados, spam o papelera). Eso es suficiente para pintar una fila del listado.

> **Los borradores no son "correo" a estos efectos.** La sincronización de metadata **excluye los borradores**: no se escriben en la copia local de correos, así que **nunca aparecen mezclados** en la bandeja de entrada, enviados ni archivados. Los borradores tienen su propia tabla, su propia sincronización y su propia sección (ver § 5 y [borradores.md](borradores.md)). En Gmail se filtran por la etiqueta `DRAFT`; en Outlook, la carpeta de borradores queda fuera de las que se recorren y cualquier mensaje marcado como borrador se descarta. Antes podían colarse como si fueran correos recibidos o archivados; ya no.

Lo que la sincronización **nunca** baja es el **cuerpo** del correo ni sus adjuntos. Esos se descargan solo cuando el usuario abre el correo (cuerpo) o clica un adjunto (binario), y se cachean a partir de ese momento. Por eso sincronizar diez mil cabeceras es barato y rápido, mientras que abrir un correo pesado tiene su pequeño coste la primera vez.

> **Ejemplo.** El usuario sincroniza su cuenta y ve aparecer 300 correos en el listado en un par de segundos. Ninguno de esos 300 cuerpos se ha descargado todavía: la app solo tiene las cabeceras. Cuando hace clic en el tercero, *entonces* se baja su HTML y se renderiza. Los otros 299 siguen sin cuerpo hasta que los abra.

### El indicador "tiene adjuntos" llega tarde a propósito

Un matiz heredado de la estrategia anterior: el icono de clip que marca "este correo tiene adjuntos descargables" **no** se calcula durante la sincronización, aunque ambos proveedores exponen ese dato en la cabecera. El flag arranca en `false` y solo pasa a `true` la primera vez que alguien abre el correo y la app descubre sus partes descargables. Es decir, justo después de sincronizar, un correo con adjuntos que nadie ha abierto puede no mostrar clip todavía. Es una simplificación consciente documentada en [adjuntos.md](adjuntos.md).

---

## 2. Cuándo se sincroniza

El usuario casi nunca pulsa "sincronizar" a mano. La app lo hace por él en los momentos naturales:

### 2.1 Al entrar en un listado

Cada vez que el usuario abre la **bandeja unificada**, **enviados**, **spam**, **papelera** o **borradores**, la app dispara una sincronización de ese ámbito en segundo plano y, cuando termina, repinta el listado con lo que haya llegado. Mientras tanto, el usuario ya ve la copia local previa (no espera en blanco). El listado se actualiza solo cuando la sincronización aporta novedades.

Esto vale tanto para la **vista unificada del mailbox** (sincroniza todas las cuentas del mailbox) como para la **vista de una cuenta concreta** (sincroniza solo esa cuenta). El ámbito de la sincronización hereda el ámbito del listado que se está mirando.

### 2.2 Al conectar una cuenta nueva

Cuando el usuario añade y autoriza una cuenta, la app **no** lo bloquea esperando una descarga: en cuanto la autorización termina con éxito, la tarjeta de la cuenta aparece **de inmediato** y ya es usable. En segundo plano arranca la **primera carga** de esa cuenta — una **descarga masiva** de su histórico reciente que corre sin que el usuario espere y va rellenando la bandeja poco a poco, con un contador en vivo del progreso. Es el cambio grande respecto al modelo anterior (que bajaba una tanda pequeña de forma **síncrona**, con el usuario esperando frente a un "Sincronizando correos…"); su funcionamiento completo está en la sección 3.5. Los **borradores** también se sincronizan al conectar, pero por una vía separada de la carga de correos: el propio **servidor encola su sincronización de borradores** al conectar (con reintento automático si falla), así que llegan al día sin depender de que el usuario abra la sección de Borradores. Es **best-effort**: si fallan, no rompen el alta de la cuenta. El detalle está en la sección 5 y en [borradores.md](borradores.md).

### 2.3 A mano, cuando el usuario quiere

Varios listados ofrecen además un botón explícito. El más visible es el botón **"Refrescar"** en la cabecera de la bandeja unificada, la vista por cuenta y la bandeja ficticia: dispara a demanda la **misma** sincronización que la apertura, acotada a esa vista, y junto a él un texto muestra **cuándo** fue la última sincronización ("Última actualización: hace X"). Esa capa de control y visibilidad tiene su propia feature: **[refrescar-y-estado-sincronizacion.md](refrescar-y-estado-sincronizacion.md)**. La otra acción manual es la **re-sincronización de borradores** desde su listado. (El antiguo botón "Sincronizar favoritos" **se retiró**: los favoritos ya se capturan solos en la sincronización general — sección 6.) En ambos casos el botón muestra un spinner mientras trabaja y se deshabilita para evitar dobles clics.

### 2.4 Lo que NO hay: sincronización automática en background

No existe un proceso programado (cron, push del proveedor, websockets) que sincronice solo el correo **del día a día** mientras la app está cerrada o en otra pantalla. La llegada de novedades se sincroniza **reactivamente**, atada a que el usuario abra un listado o conecte una cuenta. Si el usuario deja el inbox abierto y llega un correo nuevo al proveedor, no aparece hasta que vuelva a entrar al listado (o navegue y regrese). Es una decisión de alcance del MVP — ver [../limits/sincronizacion.md](../limits/sincronizacion.md).

La **única** excepción es la primera carga: la descarga masiva inicial de una cuenta recién conectada (sección 3.5) **sí** es un trabajo en segundo plano que continúa aunque el usuario navegue a otra pantalla, y que incluso se reanuda si el servidor se reinicia a mitad. Pero es una carga **inicial y de una sola vez**, no un temporizador que traiga el correo nuevo de cada día: una vez completada, la cuenta vuelve al modelo reactivo de arriba.

---

## 3. Primera sincronización (bootstrap) vs. sincronizaciones siguientes (incremental)

Aquí está la parte más interesante, y donde Gmail y Outlook se comportan de forma distinta por debajo aunque el resultado para el usuario sea el mismo.

La app distingue dos modos según si tiene o no un cursor válido para la cuenta (el usuario no elige):

- **Bootstrap (carga completa).** Es la primera vez que se sincroniza una cuenta: no hay punto de partida, así que la app baja una **tanda de los correos más recientes** abarcando **todas las bandejas** y guarda un "marcador de posición" (un cursor) para la próxima vez. En una cuenta recién conectada, esa carga inicial la hace hoy la **descarga masiva en segundo plano** (sección 3.5), que baja un tope por cuenta **mucho mayor** sin que el usuario espere. Sobrevive además una variante **síncrona y pequeña** del bootstrap que actúa solo de **reserva** en dos casos: cuando la descarga en segundo plano está desactivada en el despliegue, y cuando un cursor guardado caduca más tarde y hay que volver a anclarlo (sección 3.3). Las dos cifras — la de la descarga masiva y la de la reserva síncrona — están en [../limits/sincronizacion.md](../limits/sincronizacion.md).
- **Incremental.** A partir de la segunda sincronización, la app no rebaja todo: usa el cursor guardado para preguntarle al proveedor **solo qué ha cambiado desde la última vez** — correos nuevos, borrados y cambios de estado (leído/no leído, movido de bandeja). Es mucho más barato y casi instantáneo.

### 3.1 Qué trae un incremental

Un incremental no solo añade correos nuevos. Reconcilia tres tipos de cambio:

- **Altas**: correos que han llegado desde la última sincronización → se añaden al listado.
- **Bajas**: correos que han desaparecido del proveedor (borrados de verdad, no movidos a papelera) → se quitan de la copia local.
- **Cambios de etiqueta/estado**: un correo que pasó a leído, o que se movió de bandeja → se actualiza su fila sin rebajar toda la cabecera.

> **Ejemplo.** El usuario tenía 500 correos sincronizados. Entra al inbox al día siguiente. Han llegado 3 correos nuevos, ha leído 2 desde el móvil y ha archivado 1. El incremental trae exactamente eso: 3 altas, 2 actualizaciones de "leído" y 1 cambio de bandeja. No vuelve a tocar los otros 494.

Hay una excepción deliberada al reconciliar el estado de bandeja: un correo que el usuario borró desde la app (marcado localmente como "eliminado") **no resucita** aunque el proveedor lo siga reportando en la papelera. La sincronización respeta ese borrado local y no lo devuelve a la bandeja de papelera; solo lo movería a otra bandeja distinta si el proveedor lo sacara realmente de la papelera. El porqué de este borrado lógico se explica en [acciones-sobre-correos.md](acciones-sobre-correos.md).

### 3.2 La asimetría Gmail vs. Outlook

Ambos proveedores ofrecen un mecanismo de "dame solo lo que cambió", pero son tecnologías distintas:

- **Gmail** usa su **History API**: guarda un identificador de historial (`historyId`) como cursor y, en cada incremental, pide la lista de eventos ocurridos desde ese punto. Un detalle deliberado: en el bootstrap, Gmail captura el `historyId` **antes** de listar los correos, de modo que cualquier correo que llegue durante esa ventana de listado se replica en el siguiente incremental en lugar de perderse.
- **Outlook** usa **delta queries** de Microsoft Graph, pero con una limitación importante: Graph **no** soporta delta a nivel de buzón completo, solo por carpeta. Así que la app sincroniza carpeta por carpeta (entrada, enviados, eliminados, correo no deseado, archivo — la de **borradores queda fuera**, porque los borradores se sincronizan por su propia vía, ver la nota de la sección 1) y guarda un cursor compuesto con un marcador por cada carpeta. El bootstrap de Outlook, además, baja los correos recientes de forma transversal (todas las carpetas ordenadas por fecha) y **por separado** inicializa los marcadores delta de cada carpeta para los futuros incrementales.

Esta diferencia tiene una consecuencia de robustez: en Outlook, si una carpeta concreta falla durante un incremental, la app **conserva el marcador anterior de esa carpeta** y sigue con las demás, en vez de abortar todo. Solo si fallan **todas** las carpetas se considera el incremental fracasado y se recurre al bootstrap. Nunca se acepta un "incremental con cero cambios" cuando en realidad todas las carpetas dieron error — eso enmascararía un fallo real.

Hay una segunda medida de robustez, compartida por ambos proveedores: dentro de una misma tanda, el proveedor puede repetir **legítimamente** el mismo correo varias veces — un delta de Outlook trae una entrada por cada cambio, así que un correo que cambió dos veces desde la última sincronización aparece dos veces; y un listado paginado puede repetir un correo si el buzón cambia entre página y página. La app **colapsa esas repeticiones antes de guardar**, quedándose con la entrada más reciente de cada correo (el estado más nuevo). Sin este colapso la tanda entera fallaría al persistirse y, como el marcador solo avanza tras un guardado con éxito, la misma tanda defectuosa se repetiría en cada actualización: la cuenta quedaría permanentemente incapaz de sincronizar.

### 3.3 El umbral de fallback: cuando rehacer todo sale más barato

Hay un caso límite en el incremental de Gmail que conviene conocer. Si entre dos sincronizaciones se han acumulado **demasiados** eventos (porque el usuario llevaba mucho tiempo sin abrir la app, o hubo un movimiento masivo de correos), procesar ese aluvión de eventos uno a uno puede salir **más caro** que simplemente rehacer un bootstrap completo. Cuando el número de eventos supera un umbral de seguridad (ver [../limits/sincronizacion.md](../limits/sincronizacion.md)), la app **descarta el incremental y cae a bootstrap**: rebaja la tanda de correos recientes y reinicia el cursor.

El mismo principio de "caer a bootstrap" se aplica cuando el cursor guardado ha **caducado** o es inválido: si el proveedor rechaza el cursor (por ejemplo, un `historyId` demasiado viejo que Gmail ya purgó, o un cursor de Outlook en formato antiguo), la app no falla — silenciosamente rehace un bootstrap. Para el usuario el resultado es transparente: ve su listado actualizado; solo que por dentro se rebajó la tanda completa en lugar de un puñado de cambios.

### 3.4 Reconciliación de "fantasmas" tras un bootstrap

Cuando una sincronización es un bootstrap completo, la app aprovecha para limpiar **fantasmas**: correos que siguen en la copia local pero que ya no aparecen en la tanda recién bajada del proveedor. Antes de borrarlos a ciegas, la app **verifica** contra el proveedor si esos correos sospechosos siguen existiendo; solo elimina de la copia local los que el proveedor confirma que ya no están. Es una limpieza best-effort: si la verificación falla por cualquier motivo, no borra nada (prefiere conservar de más a borrar de menos). Este paso solo ocurre en bootstrap, no en incremental.

Hay una excepción **crítica** ligada a la descarga masiva (sección 3.5). Una cuenta gestionada por la descarga masiva puede tener en local decenas de miles de correos, pero su bootstrap **de reserva** solo rebajaría la tanda pequeña reciente. Si a esa cuenta se le caduca el cursor y cae a ese bootstrap de reserva, la reconciliación de fantasmas vería casi toda su copia local como "sospechosa" por no estar en esa tanda pequeña, y podría **borrar en masa el histórico** que tanto costó descargar. Por eso la reconciliación de fantasmas se **omite para cualquier cuenta que tenga una descarga masiva asociada** — no solo las que **completaron**, sino también las que **fallaron a mitad** (que ya pueden tener decenas de miles de correos parciales guardados) y las que aún están en curso: en ninguna de ellas el bootstrap de reserva purga la copia local, solo re-ancla el cursor. Ésta es justamente la corrección de un fallo por el que, **tras una carga incompleta**, una sincronización posterior podía **borrar por error parte del histórico ya descargado**.

### 3.5 La primera carga: descarga masiva en segundo plano

La carga inicial de una cuenta recién conectada ya no es una espera: es una **descarga masiva que corre en segundo plano** y va llenando la bandeja poco a poco. Antes la app bajaba solo una tanda pequeña de correos recientes de forma síncrona (el usuario esperaba) y ese corte dejaba fuera casi todo el histórico. Ahora baja **hasta un tope por cuenta mucho más alto** (la cifra exacta y la variable de configuración que la gobierna están en [../limits/sincronizacion.md](../limits/sincronizacion.md)), suficiente para contener el grueso del histórico reciente del buzón, como haría un cliente de correo serio.

**Qué vive el usuario.** Nada más conectar, la tarjeta de la cuenta aparece y es usable. Mientras la descarga avanza, un **contador en vivo** — *«Cargando… 12.340 correos»* — se muestra en dos sitios: en la tarjeta de la cuenta (dentro de «Cuentas conectadas», ver [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) § 2.2) y en la cabecera de la bandeja de esa cuenta (ver [refrescar-y-estado-sincronizacion.md](refrescar-y-estado-sincronizacion.md) § 2). Si el usuario abre la bandeja, ve los correos aparecer **en oleadas** conforme el contador sube. Cuando la descarga alcanza el tope o agota el buzón, el contador desaparece y la cuenta queda como una cuenta normal ya sincronizada.

**Cuánto tarda depende del proveedor**, y la diferencia es grande por cómo funcionan sus APIs. **Outlook** es rápido — cuestión de minutos — porque entrega la cabecera de muchos correos en cada página. **Gmail** es mucho más lento — puede tardar horas para un buzón grande — porque su API obliga a una petición por correo y limita el ritmo por usuario; por eso precisamente la descarga es en segundo plano y progresiva. Los órdenes de magnitud están en [../limits/sincronizacion.md](../limits/sincronizacion.md).

**Es robusta por diseño**, y ahí está el porqué del cambio:

- **A oleadas y con ritmo controlado.** La descarga pagina el buzón en tandas y **regula su propio ritmo** para no saturar al proveedor. Cuando Gmail u Outlook rechazan una petición por exceso de peticiones, la app **reintenta** en lugar de darla por perdida. La carga anterior perdía mensajes justamente porque no trataba bien esos rechazos por saturación; esta sí.
- **Se reanuda tras un corte.** El progreso se guarda por el camino (cuántos correos lleva y por dónde iba). Si el servidor se reinicia o el proceso se cae a mitad, la descarga **retoma desde donde estaba** en el siguiente arranque, sin volver a empezar de cero y sin duplicar correos.
- **Todas las cuentas en paralelo.** Si el usuario conecta varias cuentas, **todas** sus descargas corren a la vez —ya no se serializan de dos en dos como antes— hasta el máximo de cuentas que un usuario puede conectar (ver [../limits/sincronizacion.md](../limits/sincronizacion.md)); cada una muestra su propio contador y avanza a la vez. Como cada cuenta tiene sus **propios** límites frente a Gmail/Outlook, no hay razón para serializarlas. (Antes, con cuatro cuentas conectadas solo dos progresaban y dos quedaban en cola; ese cuello de botella desapareció.)
- **Solo cabeceras, como siempre.** La descarga masiva baja únicamente las **cabeceras** (sección 1), nunca cuerpos ni adjuntos. Por eso caben decenas de miles de correos por cuenta sin llenar la base de datos.

**Reconectar no vuelve a descargar todo.** La descarga masiva es exclusivamente la **primera** carga de una cuenta **nueva**. Si a una cuenta ya cargada se le caduca el token y el usuario la **reconecta** (ver [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) § 2.7), la app solo refresca el token y sigue con la sincronización incremental — no relanza la descarga de todo el histórico.

**Mientras descarga, la app no rehace el trabajo por su cuenta.** Abrir la bandeja de una cuenta que está en plena descarga masiva **no** dispara el viejo bootstrap síncrono en paralelo (la descarga masiva es la dueña de la carga inicial); la cuenta se sigue viendo como "cargando" a través de su contador. Si en una bandeja unificada **todas** las cuentas están descargando a la vez, la sincronización de apertura simplemente no trae nada nuevo todavía.

**El relevo al modo incremental es limpio.** Al empezar la descarga, la app captura un marcador del estado del buzón (sección 3.2) y lo guarda como el cursor incremental **solo cuando la descarga termina**. Así, cualquier correo que llegue durante las horas que dura la descarga se recoge en la **primera** sincronización incremental posterior, en vez de perderse. A partir de ahí la cuenta hace incrementales normales.

**Si una descarga falla, se recupera sola.** Un tropiezo puntual (un límite temporal del proveedor, un corte de red, un token caducado un instante) **ya no deja la cuenta parada para siempre** — ése era el otro motivo de "cuentas que no sincronizaban". La recuperación tiene dos niveles: primero se **reintenta la propia oleada** varios veces (respetando el tiempo de espera que el proveedor indique); y si aun así la cuenta queda marcada como fallida, un **proceso de recuperación la revive automáticamente**, un número acotado de veces y tras una breve espera, **retomándola desde donde se quedó** (sin volver a empezar ni duplicar correos). Solo cuando se agotan esos reintentos automáticos la cuenta queda **fallida de forma visible**: el contador desaparece (en el MVP no hay un aviso de error específico en la tarjeta) y la forma de recuperarla es **reconectar** la cuenta, que la vuelve a poner en cola. El usuario no tiene ningún otro control sobre la descarga: no puede pausarla, reanudarla a mano ni elegir cuántos correos bajar. Las cifras exactas (reintentos por oleada, número de reintentos automáticos de la cuenta y la espera entre ellos) están en [../limits/sincronizacion.md](../limits/sincronizacion.md).

---

## 4. El alcance "histórico": qué NO se recupera

Conviene ser explícito sobre una limitación que sorprende a quien espera un cliente de correo "completo": **MailManager no baja todo el histórico de la cuenta**. La descarga masiva inicial (sección 3.5) trae los correos **más recientes** hasta un tope por cuenta (el valor exacto está en [../limits/sincronizacion.md](../limits/sincronizacion.md)) y se detiene ahí. Los correos más antiguos que ese corte **no** entran en la copia local y, por tanto, **no aparecen** en los listados ni en la búsqueda. Ese tope es hoy **mucho más alto** que antes — la mayoría de buzones caben enteros —, pero sigue siendo un tope: un buzón que lo supere se trunca a los más recientes.

No hay ningún mecanismo de "cargar correos más antiguos" ni scroll infinito hacia el pasado en el MVP. La app es una vista de la actividad reciente del buzón, no un archivo histórico exhaustivo. El "porqué" (coste de cuota, foco del MVP) está en [../limits/sincronizacion.md](../limits/sincronizacion.md).

> **Ejemplo.** Una cuenta con 300.000 correos conecta por primera vez. Tras la descarga masiva, el usuario ve sus correos más recientes hasta el tope por cuenta, no los 300.000. Un correo lo bastante antiguo existe en Gmail pero no en MailManager, y buscarlo en la lupa no lo encuentra — porque la lupa solo mira la copia local (ver [lupa.md](lupa.md)). En cambio, una cuenta con 40.000 correos hoy **cabe entera** (antes solo entraba una pequeña tanda reciente).

---

## 5. Sincronización de borradores: reemplazo, no fusión

Los borradores se sincronizan con una semántica distinta a la de los correos. Mientras que la metadata de correos es incremental (altas, bajas, cambios), los borradores se sincronizan por **reemplazo completo por cuenta**: la app pide al proveedor la lista actual de borradores de la cuenta (hasta un tope, ver [../limits/sincronizacion.md](../limits/sincronizacion.md)) y **sustituye** los borradores locales de esa cuenta por esa lista — añade los que falten, actualiza los existentes y **borra los locales que ya no estén** en el proveedor. Todo en una sola operación atómica por cuenta.

La razón de no usar incremental aquí es simple: los proveedores no exponen un "delta de borradores" fiable, y los borradores son pocos y volátiles. Rebajar la lista entera es barato y elimina cualquier deriva entre lo local y lo del proveedor.

**Cuándo se dispara.** La sincronización de borradores se lanza en tres momentos: al **conectar** una cuenta (el **servidor** la encola de forma fiable, con reintento automático, e independiente de la carga de correos — así los borradores llegan pronto sin depender del navegador, a diferencia del modelo anterior), al **entrar** en la sección de Borradores, y **a mano** con su botón "Sincronizar". El tope de cuántos borradores se bajan por cuenta **subió** respecto al modelo anterior (la cifra exacta está en [../limits/sincronizacion.md](../limits/sincronizacion.md)). El ciclo de vida completo vive en [borradores.md](borradores.md).

### 5.1 La trampa silenciosa: preservar la metadata de respuesta

Hay un cuidado importante en este reemplazo. Cuando un borrador es una **respuesta** o un **reenvío**, MailManager guarda localmente datos de hilo (a qué mensaje responde, identificadores de threading) que el proveedor **no** devuelve al leer el borrador. Si el reemplazo sobrescribiera esos campos con los nulos que llegan del proveedor, cada sincronización **rompería el hilo** de los borradores de respuesta. Por eso el reemplazo **conserva** la metadata de respuesta local cuando lo que llega del proveedor viene vacío en esos campos. Esta sutileza es la que permite que un borrador de respuesta siga enganchado a su hilo aunque se sincronice mil veces. El detalle de esa metadata de respuesta y de cómo viaja al enviar está en [composicion-y-envio.md](composicion-y-envio.md).

### 5.2 Lo que el reemplazo NO trae

El reemplazo de borradores trae destinatarios, asunto, cuerpo y fechas, pero **no** rebaja los **adjuntos** de cada borrador en esta operación. Los adjuntos de un borrador se gestionan por su propia vía (ver [adjuntos.md](adjuntos.md)): para borradores creados o editados en la propia app viven en local hasta guardar/enviar, y para reenviados de Outlook se heredan en el lado del proveedor. Un borrador que aparece por sincronización muestra sus chips de adjunto leyéndolos de la copia local, no rebajando binarios.

---

## 6. Favoritos: llegan con el correo (la reconciliación quedó vestigial)

El estado de favorito **llega con la sincronización general de metadata** (es una cabecera más del correo — ver la sección "Qué es metadata"), así que los destacados se pueblan solos con el correo **en todos los casos**: tanto un correo nuevo que ya llega marcado como un des/marcado hecho fuera de MailManager sobre un correo **ya sincronizado**, y en **ambos** proveedores. Antes quedaba un hueco en Gmail (los cambios de estrella sobre correo existente llegaban por una ruta que ignoraba la estrella); ese hueco **se cerró**.

Por eso el botón **"Sincronizar favoritos" se retiró de la interfaz**: el usuario ya no pulsa nada. El mecanismo de reconciliación completa **sigue existiendo en el backend** pero **ninguna pantalla lo invoca** (queda vestigial). Su trabajo, si se llamara directamente, es **reconciliar la columna de favoritos** de la copia local contra la verdad del proveedor: pregunta al proveedor qué correos están marcados como favoritos (estrella en Gmail, bandera en Outlook) y, en una sola operación por cuenta, marca como favorito todo lo que el proveedor diga y **desmarca todo lo demás** de esa cuenta.

La característica deliberada y que sorprende: esta reconciliación **solo toca correos que ya existen en la copia local**. Un correo que está marcado como favorito en el proveedor pero que **no** está en la copia local de MailManager (porque cayó fuera del tope de bootstrap, por ejemplo) se **ignora silenciosamente** — la reconciliación **no importa** correos nuevos. Esa responsabilidad es exclusiva de la sincronización general de metadata. Mezclar ambas cosas duplicaría coste de cuota y recuento de filas.

El detalle completo de favoritos (la asimetría de conteos en la respuesta, por qué `total_synced` y `favorites_synced` no coinciden, la ortogonalidad del flag respecto a la bandeja) vive en su feature: **[favoritos.md](favoritos.md)** y **[../limits/favoritos.md](../limits/favoritos.md)**.

---

## 7. Multi-cuenta: una sincronización, varias cuentas, fallos aislados

Cuando se sincroniza la **vista unificada** de un mailbox con varias cuentas, la app sincroniza **todas** sus cuentas en la misma operación. Lo importante es cómo trata los fallos: el fracaso de una cuenta **no** tumba a las demás. Cada cuenta se sincroniza y reporta su propio resultado; los errores por cuenta se recogen y se evalúan **al final**, en vez de abortar el lote completo al primer tropiezo. La autenticación se refresca silenciosamente cuando es posible (tokens renovados se vuelven a guardar), y solo se marca como fallida la cuenta cuyo problema no se puede resolver solo (típicamente, un token caducado o revocado que exige reconexión).

La distinción que gobierna el resultado es **cuántas cuentas lograron sincronizar**:

- **Éxito parcial — al menos una cuenta va bien.** Las cuentas sanas persisten y el listado se actualiza con su correo; las cuentas que fallaron **no** tumban la operación: se devuelven **identificadas** (para que la vista pueda nombrarlas e invitar a reconectarlas) como parte de una respuesta **correcta**, sin escalar ningún error bloqueante. Esto es lo que permite que una sola cuenta desconectada ya **no** deje sin actualizar a toda la bandeja unificada — el fallo histórico que esta funcionalidad corrige.
- **Fallo total — ninguna cuenta pudo sincronizar.** Ahí sí se escala el error, porque de verdad no se pudo traer nada. Este caso cubre también la **vista de una sola cuenta** cuya única cuenta está rota: al no haber ninguna otra que salvar, el fallo es total por definición.

Cómo se traduce esto en la cabecera de la vista —el aviso rojo bloqueante del fallo total frente al aviso sutil que nombra la cuenta a reconectar en el éxito parcial— es una capa de presentación que vive en su propia feature: **[refrescar-y-estado-sincronizacion.md](refrescar-y-estado-sincronizacion.md) § 5.1**.

> **Ejemplo.** Vista unificada con tres cuentas. Una sincroniza 12 correos nuevos, otra 0, y la tercera falla porque su autorización expiró. El listado **se actualiza** con los 12 nuevos de la primera y refleja que la tercera tuvo un problema; la app **no** muestra un error global bloqueante por culpa de la tercera. Solo si las **tres** fallaran, la operación entera se daría por fallida.

---

## 8. Qué experimenta el usuario, de un vistazo

Para cerrar, el flujo completo tal como se vive delante de la pantalla:

1. El usuario abre un listado → la app **dispara una sincronización** en segundo plano y, mientras, muestra la copia local previa. Al **conectar una cuenta nueva**, en cambio, la carga inicial la hace la **descarga masiva en segundo plano** (sección 3.5): la tarjeta aparece al instante y el histórico se baja poco a poco, con contador en vivo, sin que el usuario espere.
2. La app decide sola **bootstrap** (primera vez / cursor inválido / demasiados eventos acumulados) o **incremental** (hay cursor válido y pocos cambios). En una cuenta nueva ese bootstrap es la descarga masiva de fondo; una variante síncrona y pequeña queda solo de reserva (sección 3).
3. Baja **solo cabeceras** (nunca cuerpos ni adjuntos), actualiza la copia local (altas, bajas, cambios de estado) y, si fue bootstrap, limpia fantasmas (salvo en una cuenta con descarga masiva ya completada, sección 3.4).
4. El listado **se repinta** con las novedades. Si no hubo novedades, no cambia nada visible.
5. Los **borradores** se sincronizan por reemplazo completo por cuenta (conservando la metadata de respuesta local).
6. Los **favoritos** llegan ya marcados con la sincronización general (la estrella es una cabecera más del correo) en **todos** los casos —correo nuevo y des/marcado fuera de banda sobre correo ya sincronizado, ambos proveedores—; el antiguo botón "Sincronizar favoritos" se **retiró de la UI** (la reconciliación pervive en el backend, vestigial).
7. Si una cuenta del lote falla pero **al menos otra** se sincroniza, las demás se actualizan igual y el fallo se reporta aparte, **sin** tumbar la operación (éxito parcial); solo si **todas** fallan se escala como error.

Y lo que **no** pasa: no se baja el histórico completo (hay un tope por cuenta, aunque hoy muy alto), no se sincroniza en background el correo del día a día con la app cerrada (la única tarea de fondo es **terminar** una descarga masiva inicial ya arrancada, sección 3.5), no se descargan cuerpos ni adjuntos "por si acaso", y un correo recién llegado no aparece hasta la siguiente sincronización del listado donde vive.

---

## Resumen en una frase

> MailManager mantiene una copia local de la **cabecera** de los correos (nunca el cuerpo ni los adjuntos, que se bajan bajo demanda) y la actualiza sincronizando reactivamente al abrir cada listado: la primera carga de una cuenta nueva es una **descarga masiva en segundo plano** (hasta un tope por cuenta alto, a oleadas con ritmo controlado, **todas las cuentas en paralelo**, **con auto-reintento** y reanudable tras un corte, sin bloquear al usuario y con contador en vivo) que abarca todas las bandejas —**menos los borradores**— y al terminar guarda el cursor incremental — con un *bootstrap* síncrono y pequeño solo de reserva; después hace *incrementales* baratos (altas, bajas y cambios de estado) vía History API en Gmail y delta queries por carpeta en Outlook, cayendo a bootstrap si el cursor caduca o se acumulan demasiados eventos; los borradores se sincronizan por reemplazo total por cuenta (disparado de forma fiable desde el **servidor** al conectar) preservando su metadata de hilo, y los favoritos **llegan ya con el correo** en la sincronización general en **todos** los casos —correo nuevo y des/marcado fuera de banda, ambos proveedores— (el proveedor es autoritativo), con el antiguo botón de reconciliación **retirado de la UI** — sin recuperación histórica más allá del tope ni sincronización periódica en segundo plano del correo del día a día. Las cifras exactas están en [../limits/sincronizacion.md](../limits/sincronizacion.md).
