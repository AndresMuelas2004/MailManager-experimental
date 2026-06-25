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

Lo que la sincronización baja de cada correo es su **cabecera**: asunto, remitente (email y nombre), primer destinatario, fecha de recepción, si está leído, y en qué bandeja vive (principal, enviados, archivados, spam o papelera). Eso es suficiente para pintar una fila del listado.

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

Cuando el usuario añade y autoriza una cuenta, la app encadena automáticamente: conecta → sincroniza la metadata de correos → sincroniza los borradores → muestra una previsualización de los primeros correos. Esta es la "primera sincronización" de esa cuenta (el bootstrap descrito en la sección 3). La sincronización de borradores en este punto es **best-effort**: si falla, no rompe el alta de la cuenta; los correos sí se consideran la parte importante.

### 2.3 A mano, cuando el usuario quiere

Varios listados ofrecen además un botón explícito. El más visible es el botón **"Refrescar"** en la cabecera de la bandeja unificada, la vista por cuenta y la bandeja ficticia: dispara a demanda la **misma** sincronización que la apertura, acotada a esa vista, y junto a él un texto muestra **cuándo** fue la última sincronización ("Última actualización: hace X"). Esa capa de control y visibilidad tiene su propia feature: **[refrescar-y-estado-sincronizacion.md](refrescar-y-estado-sincronizacion.md)**. Hay además botones de semántica distinta: **"Sincronizar favoritos"** en la página de Favoritos, que reconcilia la columna de favoritos contra el proveedor (sección 6), y la re-sincronización de borradores desde su listado. En todos los casos el botón muestra un spinner mientras trabaja y se deshabilita para evitar dobles clics.

### 2.4 Lo que NO hay: sincronización automática en background

No existe un proceso programado (cron, push del proveedor, websockets) que sincronice solo mientras la app está cerrada o en otra pantalla. La sincronización ocurre **reactivamente**, atada a que el usuario abra un listado o conecte una cuenta. Si el usuario deja el inbox abierto y llega un correo nuevo al proveedor, no aparece hasta que vuelva a entrar al listado (o navegue y regrese). Es una decisión de alcance del MVP — ver [../limits/sincronizacion.md](../limits/sincronizacion.md).

---

## 3. Primera sincronización (bootstrap) vs. sincronizaciones siguientes (incremental)

Aquí está la parte más interesante, y donde Gmail y Outlook se comportan de forma distinta por debajo aunque el resultado para el usuario sea el mismo.

La app distingue dos modos:

- **Bootstrap (sincronización completa).** Es la primera vez que se sincroniza una cuenta: no hay punto de partida, así que la app baja una **tanda de los correos más recientes** de la cuenta (un tope por cuenta, ver [../limits/sincronizacion.md](../limits/sincronizacion.md)) abarcando todas las bandejas, y guarda un "marcador de posición" (un cursor) para la próxima vez.
- **Incremental.** A partir de la segunda sincronización, la app no rebaja todo: usa el cursor guardado para preguntarle al proveedor **solo qué ha cambiado desde la última vez** — correos nuevos, borrados y cambios de estado (leído/no leído, movido de bandeja). Es mucho más barato y casi instantáneo.

El usuario no elige el modo; la app decide sola según si tiene o no un cursor válido para esa cuenta.

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
- **Outlook** usa **delta queries** de Microsoft Graph, pero con una limitación importante: Graph **no** soporta delta a nivel de buzón completo, solo por carpeta. Así que la app sincroniza carpeta por carpeta (entrada, enviados, borradores, eliminados, correo no deseado, archivo) y guarda un cursor compuesto con un marcador por cada carpeta. El bootstrap de Outlook, además, baja los correos recientes de forma transversal (todas las carpetas ordenadas por fecha) y **por separado** inicializa los marcadores delta de cada carpeta para los futuros incrementales.

Esta diferencia tiene una consecuencia de robustez: en Outlook, si una carpeta concreta falla durante un incremental, la app **conserva el marcador anterior de esa carpeta** y sigue con las demás, en vez de abortar todo. Solo si fallan **todas** las carpetas se considera el incremental fracasado y se recurre al bootstrap. Nunca se acepta un "incremental con cero cambios" cuando en realidad todas las carpetas dieron error — eso enmascararía un fallo real.

### 3.3 El umbral de fallback: cuando rehacer todo sale más barato

Hay un caso límite en el incremental de Gmail que conviene conocer. Si entre dos sincronizaciones se han acumulado **demasiados** eventos (porque el usuario llevaba mucho tiempo sin abrir la app, o hubo un movimiento masivo de correos), procesar ese aluvión de eventos uno a uno puede salir **más caro** que simplemente rehacer un bootstrap completo. Cuando el número de eventos supera un umbral de seguridad (ver [../limits/sincronizacion.md](../limits/sincronizacion.md)), la app **descarta el incremental y cae a bootstrap**: rebaja la tanda de correos recientes y reinicia el cursor.

El mismo principio de "caer a bootstrap" se aplica cuando el cursor guardado ha **caducado** o es inválido: si el proveedor rechaza el cursor (por ejemplo, un `historyId` demasiado viejo que Gmail ya purgó, o un cursor de Outlook en formato antiguo), la app no falla — silenciosamente rehace un bootstrap. Para el usuario el resultado es transparente: ve su listado actualizado; solo que por dentro se rebajó la tanda completa en lugar de un puñado de cambios.

### 3.4 Reconciliación de "fantasmas" tras un bootstrap

Cuando una sincronización es un bootstrap completo, la app aprovecha para limpiar **fantasmas**: correos que siguen en la copia local pero que ya no aparecen en la tanda recién bajada del proveedor. Antes de borrarlos a ciegas, la app **verifica** contra el proveedor si esos correos sospechosos siguen existiendo; solo elimina de la copia local los que el proveedor confirma que ya no están. Es una limpieza best-effort: si la verificación falla por cualquier motivo, no borra nada (prefiere conservar de más a borrar de menos). Este paso solo ocurre en bootstrap, no en incremental.

---

## 4. El alcance "histórico": qué NO se recupera

Conviene ser explícito sobre una limitación que sorprende a quien espera un cliente de correo "completo": **MailManager no baja todo el histórico de la cuenta**. El bootstrap trae una tanda de los correos **más recientes** (el tope exacto está en [../limits/sincronizacion.md](../limits/sincronizacion.md)) y se detiene ahí. Los correos más antiguos que ese corte **no** entran en la copia local y, por tanto, **no aparecen** en los listados ni en la búsqueda.

No hay ningún mecanismo de "cargar correos más antiguos" ni scroll infinito hacia el pasado en el MVP. La app es una vista de la actividad reciente del buzón, no un archivo histórico exhaustivo. El "porqué" (coste de cuota, foco del MVP) está en [../limits/sincronizacion.md](../limits/sincronizacion.md).

> **Ejemplo.** Una cuenta con 50.000 correos conecta por primera vez. Tras el bootstrap, el usuario ve sus correos más recientes hasta el tope por cuenta, no los 50.000. Un correo de hace tres años existe en Gmail pero no en MailManager, y buscarlo en la lupa no lo encuentra — porque la lupa solo mira la copia local (ver [lupa.md](lupa.md)).

---

## 5. Sincronización de borradores: reemplazo, no fusión

Los borradores se sincronizan con una semántica distinta a la de los correos. Mientras que la metadata de correos es incremental (altas, bajas, cambios), los borradores se sincronizan por **reemplazo completo por cuenta**: la app pide al proveedor la lista actual de borradores de la cuenta (hasta un tope, ver [../limits/sincronizacion.md](../limits/sincronizacion.md)) y **sustituye** los borradores locales de esa cuenta por esa lista — añade los que falten, actualiza los existentes y **borra los locales que ya no estén** en el proveedor. Todo en una sola operación atómica por cuenta.

La razón de no usar incremental aquí es simple: los proveedores no exponen un "delta de borradores" fiable, y los borradores son pocos y volátiles. Rebajar la lista entera es barato y elimina cualquier deriva entre lo local y lo del proveedor.

### 5.1 La trampa silenciosa: preservar la metadata de respuesta

Hay un cuidado importante en este reemplazo. Cuando un borrador es una **respuesta** o un **reenvío**, MailManager guarda localmente datos de hilo (a qué mensaje responde, identificadores de threading) que el proveedor **no** devuelve al leer el borrador. Si el reemplazo sobrescribiera esos campos con los nulos que llegan del proveedor, cada sincronización **rompería el hilo** de los borradores de respuesta. Por eso el reemplazo **conserva** la metadata de respuesta local cuando lo que llega del proveedor viene vacío en esos campos. Esta sutileza es la que permite que un borrador de respuesta siga enganchado a su hilo aunque se sincronice mil veces. El detalle de esa metadata de respuesta y de cómo viaja al enviar está en [composicion-y-envio.md](composicion-y-envio.md).

### 5.2 Lo que el reemplazo NO trae

El reemplazo de borradores trae destinatarios, asunto, cuerpo y fechas, pero **no** rebaja los **adjuntos** de cada borrador en esta operación. Los adjuntos de un borrador se gestionan por su propia vía (ver [adjuntos.md](adjuntos.md)): para borradores creados o editados en la propia app viven en local hasta guardar/enviar, y para reenviados de Outlook se heredan en el lado del proveedor. Un borrador que aparece por sincronización muestra sus chips de adjunto leyéndolos de la copia local, no rebajando binarios.

---

## 6. Sincronización de favoritos: solo reconcilia, no importa

La sincronización de favoritos es un caso especial y acotado. Su trabajo es **reconciliar la columna de favoritos** de la copia local contra la verdad del proveedor: pregunta al proveedor qué correos están marcados como favoritos (estrella en Gmail, bandera en Outlook) y, en una sola operación por cuenta, marca como favorito todo lo que el proveedor diga y **desmarca todo lo demás** de esa cuenta.

La característica deliberada y que sorprende: esta sincronización **solo toca correos que ya existen en la copia local**. Un correo que está marcado como favorito en el proveedor pero que **no** está en la copia local de MailManager (porque cayó fuera del tope de bootstrap, por ejemplo) se **ignora silenciosamente** — la sincronización de favoritos **no importa** correos nuevos. Esa responsabilidad es exclusiva de la sincronización general de metadata. Mezclar ambas cosas duplicaría coste de cuota y recuento de filas.

El detalle completo de favoritos (la asimetría de conteos en la respuesta, por qué `total_synced` y `favorites_synced` no coinciden, la ortogonalidad del flag respecto a la bandeja) vive en su feature: **[favoritos.md](favoritos.md)** y **[../limits/favoritos.md](../limits/favoritos.md)**.

---

## 7. Multi-cuenta: una sincronización, varias cuentas, fallos aislados

Cuando se sincroniza la **vista unificada** de un mailbox con varias cuentas, la app sincroniza **todas** sus cuentas en la misma operación. Lo importante es cómo trata los fallos: el fracaso de una cuenta **no** tumba a las demás. Cada cuenta se sincroniza y reporta su propio resultado; los errores por cuenta se recogen y se evalúan al final, en vez de abortar el lote completo al primer tropiezo.

Esto significa que, si un usuario tiene una cuenta de Gmail sana y una de Outlook con el token caducado, sincronizar la vista unificada actualiza la de Gmail y reporta el problema de la de Outlook, sin dejar al usuario sin nada. La autenticación se refresca silenciosamente cuando es posible (tokens renovados se vuelven a guardar), y solo se escala como error lo que no se puede resolver solo.

> **Ejemplo.** Vista unificada con tres cuentas. Una sincroniza 12 correos nuevos, otra 0, y la tercera falla porque su autorización expiró. El listado se actualiza con los 12 nuevos de la primera; la app no muestra una pantalla de error global por culpa de la tercera, pero sí refleja que esa cuenta tuvo un problema.

---

## 8. Qué experimenta el usuario, de un vistazo

Para cerrar, el flujo completo tal como se vive delante de la pantalla:

1. El usuario abre un listado (o conecta una cuenta) → la app **dispara una sincronización** en segundo plano y, mientras, muestra la copia local previa.
2. La app decide sola **bootstrap** (primera vez / cursor inválido / demasiados eventos acumulados) o **incremental** (hay cursor válido y pocos cambios).
3. Baja **solo cabeceras** (nunca cuerpos ni adjuntos), actualiza la copia local (altas, bajas, cambios de estado) y, si fue bootstrap, limpia fantasmas.
4. El listado **se repinta** con las novedades. Si no hubo novedades, no cambia nada visible.
5. Los **borradores** se sincronizan por reemplazo completo por cuenta (conservando la metadata de respuesta local).
6. Los **favoritos** se reconcilian solo cuando el usuario pulsa su botón, marcando/desmarcando lo que el proveedor diga sobre los correos que ya existen en local.
7. Si una cuenta del lote falla, las demás siguen; el problema se reporta sin tumbar la sincronización entera.

Y lo que **no** pasa: no se baja el histórico completo, no se sincroniza en background con la app cerrada, no se descargan cuerpos ni adjuntos "por si acaso", y un correo recién llegado no aparece hasta la siguiente sincronización del listado donde vive.

---

## Resumen en una frase

> MailManager mantiene una copia local de la **cabecera** de los correos (nunca el cuerpo ni los adjuntos, que se bajan bajo demanda) y la actualiza sincronizando reactivamente al abrir cada listado o al conectar una cuenta: la primera vez hace un *bootstrap* de los correos más recientes de todas las bandejas y guarda un cursor; después hace *incrementales* baratos (altas, bajas y cambios de estado) vía History API en Gmail y delta queries por carpeta en Outlook, cayendo a bootstrap si el cursor caduca o se acumulan demasiados eventos; los borradores se sincronizan por reemplazo total por cuenta preservando su metadata de hilo, y los favoritos solo se reconcilian a demanda sobre correos que ya existen en local — sin recuperación histórica más allá del tope ni sincronización en segundo plano. Las cifras exactas están en [../limits/sincronizacion.md](../limits/sincronizacion.md).
