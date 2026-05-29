# Límites de la sincronización

Catálogo cuantitativo de **hasta dónde llega** la sincronización de correos, borradores y favoritos: topes con cifras exactas, paginación, concurrencia, reintentos y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, bootstrap vs. incremental, asimetrías Gmail/Outlook y el porqué de las decisiones) está en **[../features/sincronizacion.md](../features/sincronizacion.md)**. Aquí solo van los números y los límites.

Todos estos valores están **hardcodeados** en el backend (salvo el de concurrencia de Gmail, configurable por variable de entorno) y aplican por igual a todos los usuarios. La sincronización de favoritos tiene además sus propios topes en **[favoritos.md](favoritos.md)**.

---

## 1. Topes de volumen

| Límite | Valor | Ámbito | Detalle |
|--------|-------|--------|---------|
| Correos por cuenta en bootstrap | **500** | Gmail y Outlook | La primera sincronización (o cualquier fallback a bootstrap) baja como mucho los 500 correos más recientes de la cuenta, abarcando todas las bandejas. No hay forma de subirlo desde la UI. |
| Borradores por cuenta y sincronización | **100** | Gmail y Outlook | El reemplazo de borradores trae como mucho los 100 borradores más recientes de la cuenta. Los más antiguos no se sincronizan. |
| Umbral de eventos para fallback a bootstrap | **100** | Solo Gmail (incremental) | Si un incremental de Gmail acumula más de 100 eventos desde el último cursor, se descarta y se rehace un bootstrap completo (sale más barato que procesarlos uno a uno). |

### Notas sobre los topes de volumen

- **El tope de 500 es por cuenta, no por mailbox**: una vista unificada con tres cuentas puede acabar con hasta 1.500 correos en la copia local (500 por cuenta), pero ninguna cuenta individual supera los 500.
- **El umbral de 100 eventos es específico de Gmail**. Outlook no tiene un umbral equivalente: su fallback a bootstrap se dispara por cursor inválido/caducado o por fallo de **todas** las carpetas, no por volumen de eventos.
- **El reemplazo de borradores es total por cuenta**: no es incremental. Lo que no esté en esos 100 borradores del proveedor se borra de la copia local de esa cuenta.

---

## 2. Paginación y batching

| Aspecto | Valor | Ámbito | Detalle |
|---------|-------|--------|---------|
| Tamaño de lote (batch) de metadata | **100** correos por petición | Gmail | Los detalles de cada correo se piden en lotes de 100 vía BatchHttpRequest. |
| Página de listado de IDs | **500** por página (acotada al restante hasta 500) | Gmail | El listado de IDs de mensajes pagina de 500 en 500 hasta alcanzar el tope de 500 correos. |
| Página de eventos de historial | **500** por página | Gmail (incremental) | La History API se pagina de 500 en 500 eventos. |
| Página de delta / mensajes | **100** por página | Outlook | Las delta queries por carpeta y el listado de borradores piden 100 elementos por página. |
| Página de bootstrap (mensajes recientes) | **mín(500, 1000)** = **500** | Outlook | El bootstrap transversal pide `$top` acotado a 1000, pero limitado por el tope de 500 correos. |
| Página de borradores | **100** por página | Outlook | El listado paginado de borradores ordenado por última modificación descendente. |

### Carpetas que Outlook sincroniza por delta

Microsoft Graph no soporta delta a nivel de buzón, así que Outlook itera estas **6 carpetas** por separado, cada una con su propio cursor:

`inbox` (entrada), `sentitems` (enviados), `drafts` (borradores), `deleteditems` (eliminados), `junkemail` (correo no deseado), `archive` (archivo).

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

- **Los lotes de lectura de metadata de Gmail reintentan 5 veces en total** (un intento inicial + 4 reintentos), con 1 segundo de espera, solo sobre errores transitorios (429 y 5xx). Los errores permanentes (400, 403, 404, 410) no se reintentan: esos correos se marcan como "no recuperables" en ese lote y se omiten. El listado de borradores de Gmail reutiliza este mismo lote (mismo 5 = 1+4), y la lista paginada de borradores de Outlook reintenta cada página 5 veces (1 + 4) con 1 s fija.
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
| Favoritos (estrella/bandera) | **Sí**, pero solo a demanda y solo reconciliando | Botón "Sincronizar favoritos" (ver [favoritos.md](../features/favoritos.md)). |

---

## 6. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Detalle | Por qué |
|------------|---------|---------|
| **Recuperación histórica más allá del tope** | Solo se bajan los 500 correos más recientes por cuenta; los anteriores no entran en la copia local ni en la búsqueda | Bajar el histórico completo dispararía el coste de cuota del proveedor y queda fuera del foco del MVP. No hay "cargar más antiguos". |
| **Sincronización en segundo plano** | No hay cron, push del proveedor ni websockets; la sincronización es reactiva (al abrir un listado o conectar una cuenta) | El MVP sincroniza bajo demanda. Un correo recién llegado no aparece hasta la siguiente sincronización del listado donde vive. |
| **Delta de borradores** | Los borradores no se sincronizan por incremental, sino por reemplazo total por cuenta | Los proveedores no exponen un delta fiable de borradores y son pocos; rebajar la lista entera es más simple y evita deriva. |
| **Importar favoritos nuevos** | La sincronización de favoritos solo reconcilia correos que ya existen en local; ignora los que están marcados en el proveedor pero no sincronizados | Importar metadata nueva es trabajo de la sincronización general; mezclarlo duplicaría coste y recuento (ver [favoritos.md](../features/favoritos.md)). |
| **Descarga de cuerpos/adjuntos en el sync** | La sincronización nunca baja cuerpos ni binarios "por si acaso" | Mantiene el sync barato e instantáneo; el contenido se baja bajo demanda y se cachea. |
| **Umbral de eventos en Outlook** | Outlook no cae a bootstrap por volumen de eventos (solo Gmail lo hace) | El fallback de Outlook se basa en cursor inválido o fallo total de carpetas, no en un contador de eventos. |

---

## 7. Comportamiento ante cursor inválido o caducado

| Situación | Resultado |
|-----------|-----------|
| Cursor de Gmail (`historyId`) caducado o rechazado (404/410) | Fallback silencioso a **bootstrap** (rebaja los 500 recientes, reinicia cursor). |
| Más de 100 eventos acumulados en incremental de Gmail | Fallback a **bootstrap**. |
| Cursor de Outlook en formato antiguo / no versionado / corrupto | Decodifica a vacío → fallback a **bootstrap**. |
| Una carpeta de Outlook falla en incremental | Conserva su cursor anterior; las demás continúan. |
| **Todas** las carpetas de Outlook fallan en incremental | Se fuerza el fallback a **bootstrap** (nunca se acepta "incremental con cero cambios" cuando todo falló). |

Para el usuario, cualquiera de estos fallbacks es transparente: ve su listado actualizado; por dentro se rebajó la tanda completa en lugar de unos pocos cambios.

---

> La sincronización llega hasta: **500 correos por cuenta en bootstrap, 100 borradores por cuenta (reemplazo total), umbral de 100 eventos antes de rehacer bootstrap en Gmail, lotes de 100 con 5 workers y 5 intentos (1+4) en Gmail, 6 carpetas delta secuenciales en Outlook** — baja solo cabeceras (nunca cuerpos ni adjuntos), no recupera histórico más allá del tope y no sincroniza en segundo plano. El comportamiento completo está en [../features/sincronizacion.md](../features/sincronizacion.md).
