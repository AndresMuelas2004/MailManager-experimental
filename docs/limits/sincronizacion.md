# Límites de la sincronización

Catálogo cuantitativo de **hasta dónde llega** la sincronización de correos, borradores y favoritos: topes con cifras exactas, paginación, concurrencia, reintentos y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, bootstrap vs. incremental, asimetrías Gmail/Outlook y el porqué de las decisiones) está en **[../features/sincronizacion.md](../features/sincronizacion.md)**. Aquí solo van los números y los límites.

La mayoría de estos valores están **hardcodeados** en el backend y aplican por igual a todos los usuarios; unos pocos son **configurables por variable de entorno** — el de concurrencia de lotes de Gmail y **todos los de la descarga masiva inicial** (§ 8). La sincronización de favoritos tiene además sus propios topes en **[favoritos.md](favoritos.md)**.

---

## 1. Topes de volumen

| Límite | Valor | Ámbito | Detalle |
|--------|-------|--------|---------|
| Correos por cuenta en la descarga masiva inicial (backfill) | **100.000** (por defecto) | Gmail y Outlook | La primera carga de una cuenta recién conectada baja como mucho sus 100.000 correos más recientes, abarcando todas las bandejas, en segundo plano (§ 8). Configurable con `BACKFILL_MAX_EMAILS_PER_ACCOUNT`; no se puede cambiar desde la UI. |
| Correos por cuenta en el bootstrap síncrono de reserva | **500** | Gmail y Outlook | La variante síncrona del bootstrap — usada solo con la descarga masiva desactivada, o al re-anclar un cursor caducado — baja como mucho los 500 correos más recientes. Valor fijo, distinto del tope de la descarga masiva. |
| Borradores por cuenta y sincronización | **500** | Gmail y Outlook | El reemplazo de borradores trae como mucho los 500 borradores más recientes de la cuenta (subido desde 100). Los más antiguos no se sincronizan. Tope compartido con la sincronización manual — ver [borradores.md](borradores.md). |
| Umbral de eventos para fallback a bootstrap | **100** | Solo Gmail (incremental) | Si un incremental de Gmail acumula más de 100 eventos desde el último cursor, se descarta y se rehace un bootstrap completo (sale más barato que procesarlos uno a uno). |

### Notas sobre los topes de volumen

- **Los topes son por cuenta, no por mailbox**: con la descarga masiva, una vista unificada con tres cuentas puede acabar con hasta 300.000 correos en la copia local (100.000 por cuenta), pero ninguna cuenta individual supera su tope.
- **El tope de 100.000 y el de 500 son excluyentes por cuenta**: una cuenta nueva usa la descarga masiva (100.000); la variante de 500 solo entra como reserva (descarga masiva desactivada o re-anclaje de un cursor caducado). Nunca se aplican los dos a la misma carga.
- **El umbral de 100 eventos es específico de Gmail**. Outlook no tiene un umbral equivalente: su fallback a bootstrap se dispara por cursor inválido/caducado o por fallo de **todas** las carpetas, no por volumen de eventos.
- **El reemplazo de borradores es total por cuenta**: no es incremental. Lo que no esté en esos 500 borradores del proveedor se borra de la copia local de esa cuenta.

---

## 2. Paginación y batching

| Aspecto | Valor | Ámbito | Detalle |
|---------|-------|--------|---------|
| Tamaño de lote (batch) de metadata | **100** correos por petición | Gmail | Los detalles de cada correo se piden en lotes de 100 vía BatchHttpRequest. |
| Página de listado de IDs | **500** por página (acotada al restante hasta 500) | Gmail | El listado de IDs de mensajes pagina de 500 en 500 hasta alcanzar el tope de 500 correos. |
| Página de eventos de historial | **500** por página | Gmail (incremental) | La History API se pagina de 500 en 500 eventos. |
| Página de delta / mensajes | **100** por página | Outlook | Las delta queries por carpeta piden 100 elementos por página. |
| Página de bootstrap (mensajes recientes) | **mín(500, 1000)** = **500** | Outlook | El bootstrap transversal pide `$top` acotado a 1000, pero limitado por el tope de 500 correos. |
| Página de borradores | **500** por página (`$top=500`) | Outlook | El listado paginado de borradores ordenado por última modificación descendente; una sola página cubre el tope de 500 (Graph acepta `$top`≤1000). |

### Carpetas que Outlook sincroniza por delta

Microsoft Graph no soporta delta a nivel de buzón, así que Outlook itera estas **5 carpetas** por separado, cada una con su propio cursor:

`inbox` (entrada), `sentitems` (enviados), `deleteditems` (eliminados), `junkemail` (correo no deseado), `archive` (archivo).

La carpeta **`drafts` (borradores) ya NO se recorre**: los borradores se sincronizan por su propia vía (§ 8.4) y no deben colarse en la copia local de correos. Cualquier mensaje marcado como borrador que aún aparezca se descarta, y un cursor de `drafts` heredado de una sincronización anterior se ignora.

Una carpeta que no exista o que falle al resolverse se omite (bootstrap) o conserva su cursor anterior (incremental).

---

## 3. Concurrencia

| Aspecto | Valor | Ámbito | Detalle |
|---------|-------|--------|---------|
| Workers paralelos de batch | **5** (por defecto) | Gmail | Los lotes de 100 correos se reparten entre hasta 5 hilos en paralelo. Configurable con la variable de entorno `GMAIL_BATCH_MAX_WORKERS`. |
| Concurrencia de Outlook | **secuencial por carpeta** | Outlook | Las carpetas delta se recorren en serie; no hay paralelismo de carpetas (Graph limita las peticiones concurrentes por buzón). |

### Notas sobre concurrencia

- **`GMAIL_BATCH_MAX_WORKERS`** se valida al arrancar: un valor no numérico o ausente cae a **5**, y un valor menor que 1 se eleva a 1. Con el tope de 500 correos y lotes de 100, hay como mucho 5 lotes, así que 5 workers basta para cubrirlos en una sola tanda.

---

## 4. Reintentos

| Operación | Intentos | Espera entre intentos | Ámbito |
|-----------|----------|-----------------------|--------|
| Lote de metadata (lectura) | **5** (1 + 4 reintentos) | **1 s** fija | Gmail |
| Página de listado de borradores | **5** (1 + 4 reintentos) | **1 s** fija | Outlook |
| Delta de carpeta (incremental / bootstrap) | **1** (sin reintento) | — | Outlook |

### Notas sobre reintentos

- **Los lotes de lectura de metadata de Gmail reintentan 5 veces en total** (un intento inicial + 4 reintentos), con 1 segundo de espera, sobre errores transitorios: 429, 5xx y **también 403 cuando su cuerpo trae una razón de límite de ritmo** (`rateLimitExceeded` / `userRateLimitExceeded`) — Gmail limita el ritmo bajo 403, no bajo 429, y no tratar bien ese caso hacía que se perdieran correos por saturación durante la carga inicial. Los errores permanentes (400, 404, 410, y el 403 `dailyLimitExceeded` de cuota diaria agotada) no se reintentan: esos correos se marcan como "no recuperables" en ese lote y se omiten. El listado de borradores de Gmail reutiliza este mismo lote (mismo 5 = 1+4), y la lista paginada de borradores de Outlook reintenta cada página 5 veces (1 + 4) con 1 s fija.
- **La delta de carpeta de Outlook NO reintenta**: cada petición de delta es un único intento. Si una carpeta falla, en un incremental conserva su cursor anterior (sin reintentarla) y la sincronización continúa con las demás; en el bootstrap se omite esa carpeta. El fallback a bootstrap solo se fuerza si **todas** las carpetas fallan en el incremental. No hay tolerancia de reintento por carpeta para la delta.
- Los reintentos de **envío** y de **descarga de adjuntos** pertenecen a otras features — ver [composicion-y-envio.md](../features/composicion-y-envio.md) y [../limits/adjuntos.md](adjuntos.md).

---

## 5. Qué se baja y qué NO en cada sincronización

| Dato | ¿Se sincroniza? | Cuándo se obtiene |
|------|-----------------|-------------------|
| Cabecera del correo (asunto, remitente, primer destinatario, fecha, leído, bandeja) | **Sí** | En cada sincronización (bootstrap e incremental). |
| Cuerpo del correo (HTML / texto) | **No** | Bajo demanda, al abrir el correo (ver [visualizacion-de-correos.md](../features/visualizacion-de-correos.md)). |
| Lista de adjuntos de un correo | **No** | Al abrir el correo, no al sincronizar. |
| Binario de un adjunto | **No** | Al clicar el adjunto (ver [adjuntos.md](../features/adjuntos.md)). |
| Flag "tiene adjuntos" (icono clip) | **No durante el sync** | Se calcula la primera vez que se abre el correo (estrategia "lazy"). |
| Borradores (destinatarios, asunto, cuerpo, fechas) | **Sí** (reemplazo total por cuenta) | En cada sincronización de borradores. |
| Adjuntos de borradores | **No durante el sync de borradores** | Vía propia de adjuntos (local hasta guardar/enviar, o heredados en Outlook). |
| Favoritos (estrella/bandera) | **Sí**, como una cabecera más | En cada sincronización (bootstrap e incremental); el proveedor es autoritativo. La reconciliación dedicada quedó vestigial y sin botón en la UI (ver [favoritos.md](../features/favoritos.md)). |

---

## 6. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Detalle | Por qué |
|------------|---------|---------|
| **Recuperación histórica más allá del tope** | La descarga masiva baja hasta 100.000 correos por cuenta; los anteriores a ese corte no entran en la copia local ni en la búsqueda | El tope es hoy muy alto (la mayoría de buzones caben enteros), pero bajar sin límite dispararía el coste de cuota del proveedor y queda fuera del foco del MVP. No hay "cargar más antiguos". |
| **Sincronización periódica en segundo plano** | No hay cron, push del proveedor ni websockets para el correo del día a día; la sincronización de novedades es reactiva (al abrir un listado). La única tarea de fondo es completar una descarga masiva inicial ya arrancada (§ 8) | El MVP sincroniza las novedades bajo demanda. Un correo recién llegado no aparece hasta la siguiente sincronización del listado donde vive. |
| **Control del usuario sobre la descarga masiva** | No se puede pausar, reanudar a mano ni elegir cuántos correos bajar desde la interfaz | MVP: el único "control" es reconectar la cuenta para reintentar una descarga fallida; el tope solo lo cambia un administrador por variable de entorno. |
| **Reintentos ilimitados / aviso de error de la descarga masiva** | Una descarga que falla **sí se reintenta sola** (hasta 5 intentos, § 8.2), pero al agotarlos queda fallida sin un aviso de error específico en la tarjeta: el contador simplemente desaparece | MVP: el auto-reintento cubre los tropiezos puntuales; un fallo persistente se recupera reconectando la cuenta, y la señalización visible del estado "fallido" se dejó para una fase futura. |
| **Delta de borradores** | Los borradores no se sincronizan por incremental, sino por reemplazo total por cuenta | Los proveedores no exponen un delta fiable de borradores y son pocos; rebajar la lista entera es más simple y evita deriva. |
| **Importar favoritos nuevos** | La sincronización de favoritos solo reconcilia correos que ya existen en local; ignora los que están marcados en el proveedor pero no sincronizados | Importar metadata nueva es trabajo de la sincronización general; mezclarlo duplicaría coste y recuento (ver [favoritos.md](../features/favoritos.md)). |
| **Descarga de cuerpos/adjuntos en el sync** | La sincronización nunca baja cuerpos ni binarios "por si acaso" | Mantiene el sync barato e instantáneo; el contenido se baja bajo demanda y se cachea. |
| **Umbral de eventos en Outlook** | Outlook no cae a bootstrap por volumen de eventos (solo Gmail lo hace) | El fallback de Outlook se basa en cursor inválido o fallo total de carpetas, no en un contador de eventos. |

---

## 7. Comportamiento ante cursor inválido o caducado

| Situación | Resultado |
|-----------|-----------|
| Cursor de Gmail (`historyId`) caducado o rechazado (404/410) | Fallback silencioso al **bootstrap síncrono de reserva** (rebaja los 500 recientes, reinicia cursor). |
| Más de 100 eventos acumulados en incremental de Gmail | Fallback al **bootstrap síncrono de reserva**. |
| Cursor de Outlook en formato antiguo / no versionado / corrupto | Decodifica a vacío → fallback al **bootstrap síncrono de reserva**. |
| Una carpeta de Outlook falla en incremental | Conserva su cursor anterior; las demás continúan. |
| **Todas** las carpetas de Outlook fallan en incremental | Se fuerza el fallback al **bootstrap síncrono de reserva** (nunca se acepta "incremental con cero cambios" cuando todo falló). |

Para el usuario, cualquiera de estos fallbacks es transparente: ve su listado actualizado; por dentro se rebajó la tanda de 500 en lugar de unos pocos cambios.

> **Cuidado con las cuentas gestionadas por la descarga masiva.** Su copia local puede tener decenas de miles de correos, pero el bootstrap de reserva solo rebaja 500. Para no borrar en masa el histórico, la **reconciliación de fantasmas se omite para cualquier cuenta con una descarga masiva asociada** — no solo las **completadas**, sino también las que **fallaron a mitad** (que ya pueden guardar decenas de miles de correos parciales) y las que siguen en curso: el fallback solo re-ancla el cursor, sin purgar. Además, una cuenta con una descarga aún activa o reintentable se **excluye por completo** de la sincronización normal para que no caiga al bootstrap de reserva. Ésta es la corrección del fallo por el que, tras una carga incompleta, una sincronización posterior podía borrar por error parte del histórico. El comportamiento está en [../features/sincronizacion.md](../features/sincronizacion.md) § 3.4.

---

## 8. La descarga masiva inicial (backfill)

La primera carga de una cuenta recién conectada la ejecuta un **trabajador en segundo plano** dentro del propio proceso del servidor (sin cola externa; MVP de un solo proceso). El comportamiento está en [../features/sincronizacion.md](../features/sincronizacion.md) § 3.5; aquí van las cifras.

### 8.1 Configuración y ritmo

| Aspecto | Valor (por defecto) | Variable de entorno | Detalle |
|---------|---------------------|---------------------|---------|
| Tope de correos por cuenta | **100.000** | `BACKFILL_MAX_EMAILS_PER_ACCOUNT` | Ver § 1. |
| Cuentas descargando en paralelo | **15** | `BACKFILL_MAX_CONCURRENT` | **Todas** las cuentas que un usuario puede conectar se descargan a la vez (alineado con el tope de cuentas por usuario, ver [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md)); ya no se serializan de dos en dos ni queda ninguna en cola. Los límites del proveedor son por cuenta, así que el paralelismo es seguro. |
| Escrituras de BD en paralelo (backfill) | **8** | `BACKFILL_DB_WRITE_CONCURRENCY` | Recurso compartido desacoplado de la descarga: aunque 15 cuentas bajen a la vez, como mucho 8 escriben en la BD simultáneamente, para no agotar el pool de conexiones que necesitan las peticiones del usuario. Debe mantenerse por debajo de `DB_POOL_MAX_CONN` (por defecto 25, subido de 10 por este cambio). |
| Reintentos automáticos de un trabajo fallido | **5** | `BACKFILL_MAX_ATTEMPTS` | Un trabajo que falla se **revive automáticamente** hasta 5 intentos en total antes de quedar fallido de forma permanente (el usuario reconecta). Compartido con la sincronización de borradores (§ 8.4). |
| Espera antes de revivir un trabajo fallido | **60** s | — | Enfriamiento antes de que el proceso de recuperación reintente un trabajo fallido (evita un bucle de reintento apretado). |
| Interruptor del trabajador | **encendido** | `BACKFILL_WORKER_ENABLED` | Apagarlo desactiva la descarga masiva **y** el encolado al conectar (en bloque): las cuentas nuevas caen entonces al bootstrap síncrono de 500 (§ 1). |
| Ritmo de Gmail | **300** peticiones `messages.get`/min | `BACKFILL_GMAIL_GETS_PER_MINUTE` | Gmail exige una petición por correo; este es el techo de ritmo que evita la saturación. |
| Retardo entre páginas de Outlook | **300** ms | `BACKFILL_OUTLOOK_PAGE_DELAY_MS` | Outlook trae la cabecera inline, así que las páginas son baratas; basta un retardo pequeño. |
| Sondeo del despachador | **5** s | `BACKFILL_POLL_INTERVAL_S` | Cada cuánto el trabajador busca trabajos nuevos que arrancar. |
| Tamaño de página (por oleada) | Gmail **500** / Outlook **1.000** | — | Máximos de cada proveedor (Gmail `messages.list`, Graph `$top`). |
| Reintento por oleada | **5** intentos (1 + 4), esperas **2 / 5 / 10 / 20 s** | — | Sobre fallo de proveedor, por **encima** de los reintentos internos del lote de metadata (§ 4). Honra el `Retry-After` del proveedor cuando lo indica. |
| Sondeo del contador (navegador) | **2,5** s | — | Cada cuánto se refresca el "Cargando… N correos" mientras haya una descarga activa; se detiene al terminar. |

### 8.2 Estados del trabajo y reanudación

- Cada cuenta tiene **un** trabajo, con estado **pendiente → en curso → completado / fallido**.
- **Reanudación tras un corte:** al arrancar el servidor, todo trabajo que quedó "en curso" por una caída se vuelve a poner "pendiente" y se retoma **desde su punto guardado** (cuántos correos llevaba y por qué página iba), sin duplicar ni reiniciar de cero.
- **Recuperación automática de un fallo:** un trabajo **fallido** se revive solo (vuelve a "pendiente", retomando desde su punto guardado) hasta **5 intentos** en total (`BACKFILL_MAX_ATTEMPTS`), tras una espera de **60 s** entre reintentos. Solo cuando agota esos intentos queda fallido de forma permanente, y entonces la única vía es reconectar la cuenta (que lo revive de nuevo). Esto es independiente del interruptor del trabajador: un trabajo que existe se atiende siempre.
- **Solo la primera conexión encola** una descarga. Una **reconexión** de una cuenta ya sincronizada no la relanza. Una cuenta **completada** nunca se vuelve a descargar.

### 8.3 Tiempo estimado para el tope de 100.000

| Proveedor | Orden de magnitud | Por qué |
|-----------|-------------------|---------|
| **Outlook** | **minutos** | Entrega la cabecera de hasta 1.000 correos por página; el ritmo lo marca solo el retardo entre páginas. |
| **Gmail** | **~2 a 6 horas** | Una petición por correo a 300/min ≈ 5,5 h para el tope completo; menos si el buzón es más pequeño o un administrador sube `BACKFILL_GMAIL_GETS_PER_MINUTE`. |

### 8.4 Sincronización de borradores dirigida por el servidor

El **mismo** trabajador procesa un segundo tipo de trabajo: la sincronización de borradores de una cuenta.

| Aspecto | Valor | Detalle |
|---------|-------|---------|
| Cuándo se encola | **En cada conexión** (primera **y** reconexión) | A diferencia de la descarga masiva (solo la primera vez), la sincronización de borradores se re-encola en cada conexión para que los borradores siempre queden al día. Gobernado por el mismo `BACKFILL_WORKER_ENABLED`: con el trabajador apagado no se encola y el fallback es la sincronización desde el navegador al abrir la sección de Borradores. |
| Forma del trabajo | **Una sola pasada** (sin paginación ni checkpoint) | Un `fetch` de los borradores del proveedor + un reemplazo atómico local. Se reclama **antes** que la descarga masiva en cada sondeo (es rápido, cuestión de segundos). |
| Borradores por cuenta | **500** | El mismo tope que la sincronización de borradores manual (ver [borradores.md](borradores.md)). |
| Reintentos automáticos | **5** (`BACKFILL_MAX_ATTEMPTS`), 60 s de espera | Comparte el proceso de recuperación con la descarga masiva (§ 8.2). |

---

> La sincronización llega hasta: **100.000 correos por cuenta en la descarga masiva inicial (por defecto, configurable; con un bootstrap síncrono de reserva de 500), hasta 15 cuentas descargando en paralelo (todas las que un usuario puede conectar) con ritmo controlado (Gmail ~300 get/min), reanudación tras un corte y auto-reintento de un trabajo fallido (5 intentos), 500 borradores por cuenta (reemplazo total, encolado por el servidor en cada conexión), umbral de 100 eventos antes de rehacer bootstrap en Gmail, lotes de 100 con 5 workers y 5 intentos (1+4) en Gmail, 5 carpetas delta secuenciales en Outlook (sin borradores)** — baja solo cabeceras (nunca cuerpos ni adjuntos), excluye los borradores de la copia local de correos, no recupera histórico más allá del tope y no sincroniza periódicamente en segundo plano el correo del día a día. El comportamiento completo está en [../features/sincronizacion.md](../features/sincronizacion.md).
