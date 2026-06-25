# Ordenar y filtrar rápido el listado — comportamiento

Este documento describe **qué cambia para el usuario** cuando ordena o filtra el listado de correos: las opciones de orden, los chips de filtro rápido, cómo se combinan entre sí y con la búsqueda, y los casos borde. Es una **extensión** de la feature [listado-de-correos.md](listado-de-correos.md): hasta ahora el listado salía siempre ordenado por fecha descendente y sin posibilidad de reordenar ni de filtrar de un clic; esta funcionalidad añade ambas capacidades **sin tocar** el resto del comportamiento del listado.

Las cifras exactas (qué valores de orden y de dirección existen, qué chips, cómo se serializan en la URL) y la lista de "qué NO soporta" viven en el gemelo: **[../limits/ordenar-y-filtrar-listado.md](../limits/ordenar-y-filtrar-listado.md)**. Aquí solo se mencionan de pasada y se enlaza allí.

Fronteras con otras features:

- El **esqueleto del listado** (de dónde salen los correos, las cuatro bandejas, la paginación numerada, el enrutado de cada acción al buzón real) se documenta en [listado-de-correos.md](listado-de-correos.md).
- La **búsqueda de texto libre y los operadores** (la lupa) se documentan en [lupa.md](lupa.md). Los chips de filtro de esta feature son atajos visuales para tres de esos operadores, pero **separados** de la caja de búsqueda (sección 3).
- La **agrupación por conversación** (cómo se presentan las filas en las bandejas reales) se documenta en [conversaciones.md](conversaciones.md); su interacción con el orden y los filtros se trata en la sección 5.

---

## 1. Qué problema resuelve

Antes de esta funcionalidad, el listado tenía dos rigideces:

1. **Salía siempre por fecha, de lo más reciente a lo más antiguo, y no se podía cambiar.** Ni siquiera existía el "más antiguos primero".
2. **No había forma rápida** de quedarse con "solo los no leídos", "solo los que traen adjunto" o "solo los destacados" sin teclear una búsqueda con operadores en la lupa.

Esta funcionalidad añade dos capacidades al listado de las bandejas reales:

1. **Ordenar** por **Fecha**, **Remitente** o **Asunto**, en sentido **ascendente o descendente**.
2. **Filtrar rápido** con tres chips de un clic: **No leídos**, **Con adjuntos** y **Destacados**, aplicables a la bandeja que se está mirando.

Es la diferencia entre "todo en orden cronológico fijo" y "enséñame solo lo que me interesa ahora, en el orden que quiero", sin tener que escribir nada.

---

## 2. Dónde aparece (alcance)

La funcionalidad se añade **solo a las bandejas reales**, en sus cuatro bandejas (principal, enviados, spam y papelera):

- **Vista unificada** del mailbox (todas las cuentas mezcladas).
- **Vista de una cuenta concreta**.

**No se toca** (queda exactamente igual que antes):

- La pestaña de **Favoritos** (unificada y por cuenta). Ya filtra por "destacado" por definición, y su petición al backend sigue siendo idéntica a la de antes de esta feature.
- Las **bandejas ficticias**. Tienen su propio sistema de filtros guardados ([bandejas-ficticias.md](bandejas-ficticias.md)).

> Esta decisión es deliberada: la funcionalidad ordena y filtra "la bandeja que estás mirando", que son justamente las bandejas reales. Extenderla a Favoritos y a las bandejas ficticias se deja fuera de este alcance — el porqué está en [../limits/ordenar-y-filtrar-listado.md](../limits/ordenar-y-filtrar-listado.md).

---

## 3. Cómo se ve y se usa

Encima de la tabla de correos, **junto al buscador (la lupa)**, aparece una **fila de controles** con:

- Un desplegable **"Ordenar por"** con tres opciones: **Fecha** (por defecto), **Remitente**, **Asunto**.
- Un **botón de dirección** que alterna entre **descendente** (↓, el de siempre: más reciente / Z→A) y **ascendente** (↑, más antiguo / A→Z).
- Tres **chips de filtro** que se encienden/apagan al pulsarlos: **No leídos**, **Con adjuntos**, **Destacados**.

Toda la interacción vive en esa barra y **funciona igual en móvil y en escritorio**: no depende de las cabeceras de la tabla (que en móvil están ocultas — ver [diseno-responsive-movil.md](diseno-responsive-movil.md)), sino de controles propios que se reorganizan en varias líneas cuando no caben en una.

### Ejemplo

> En la bandeja principal unificada, el usuario pulsa el chip **No leídos** y, en "Ordenar por", elige **Remitente** ascendente. La lista pasa a mostrar únicamente los correos no leídos, ordenados alfabéticamente por quién los envía (de la A a la Z). Pulsa otra vez el chip **No leídos** y vuelve a ver todo, manteniendo el orden por remitente.

---

## 4. Reglas de comportamiento

### 4.1 Los filtros se combinan (Y lógico)

Se pueden activar **varios chips a la vez**. El resultado es la **intersección**: con **No leídos** + **Con adjuntos** activos, se ven solo los correos que son no leídos **y** además traen adjunto. Volver a pulsar un chip lo desactiva.

### 4.2 Los filtros son independientes de la búsqueda

Los chips conviven con la lupa: se puede tener una búsqueda escrita **y** chips activos a la vez, y se aplican juntos (búsqueda **Y** filtros **Y** orden). Los chips **no** escriben nada en la caja de búsqueda ni dependen de ella.

> Bajo el capó, cada chip se traduce exactamente al mismo filtro que el operador equivalente de la lupa: **No leídos** ≡ `is:unread`, **Con adjuntos** ≡ `has:attachment`, **Destacados** ≡ `is:favorite`. Por eso un chip y su operador escrito son intercambiables en resultado. Y por eso, si alguien combina el chip "No leídos" con un `is:read` escrito en la lupa, el resultado será vacío de forma natural (una condición contradice a la otra), igual que ya ocurre al escribir operadores contradictorios entre sí ([lupa.md](lupa.md)).

> Matiz importante: el chip **Destacados** **no** es lo mismo que la pestaña de **Favoritos**. El chip es un filtro normal sobre la bandeja que estás mirando (p. ej. "los destacados de Enviados"); la pestaña de Favoritos es una vista dedicada con sus propias reglas (excluye papelera y spam, no agrupa por conversación) — ver [favoritos.md](favoritos.md).

### 4.3 El orden por defecto no cambia

Sin tocar nada, el listado sigue saliendo **por fecha, de más reciente a más antiguo**, exactamente como antes. La ordenación alternativa es algo que el usuario elige explícitamente; el comportamiento de arranque es idéntico al histórico, y la propia petición al backend es la misma que antes mientras no se cambie ningún control.

### 4.4 Qué significa "ordenar por remitente"

Ordena alfabéticamente por **quién envía** el correo: por su **nombre visible** (p. ej. "Banco Santander") y, **cuando no hay nombre**, por su **dirección de correo**. La comparación **ignora mayúsculas/minúsculas y tildes**, para que el orden sea el natural que espera una persona (es el mismo criterio de normalización de texto que usa la búsqueda de la lupa).

"Ordenar por asunto" funciona igual: alfabético sobre el asunto, insensible a mayúsculas y tildes.

### 4.5 El desempate y el orden secundario son invisibles pero importantes

Como ya ocurría con el orden por fecha ([listado-de-correos.md](listado-de-correos.md) § 5.1), el orden es **totalmente determinista**: ante valores empatados, la app desempata de forma fija para que la lista no "baile" entre cargas ni se dupliquen/salten filas al paginar. Con orden por remitente o por asunto, además, los correos del **mismo** remitente (o mismo asunto) se agrupan **del más reciente al más antiguo** dentro de ese bloque, independientemente de la dirección elegida. El usuario no ve este detalle; solo percibe un orden estable y sensato. El porqué técnico está en [../limits/ordenar-y-filtrar-listado.md](../limits/ordenar-y-filtrar-listado.md).

### 4.6 Cambiar de orden o de filtro vuelve a la página 1

El listado está paginado. Cambiar el criterio de orden, la dirección o cualquier chip **reinicia la navegación a la página 1**, igual que ya ocurre al cambiar la búsqueda: el conjunto mostrado cambió, así que empezar por el principio es lo predecible.

### 4.7 El estado vive en la dirección (URL) y se reinicia al cambiar de contexto

El orden y los filtros activos se guardan en la dirección de la página (como ya ocurre con la búsqueda y la página actual): recargar o compartir el enlace conserva esa vista. Al **cambiar de bandeja o de cuenta**, los controles vuelven a su valor por defecto (orden por fecha descendente, sin filtros) — son ajustes "de la bandeja que estás mirando", no una preferencia global que persista entre vistas. Los nombres exactos de los parámetros de URL están en [../limits/ordenar-y-filtrar-listado.md](../limits/ordenar-y-filtrar-listado.md).

### 4.8 El total y la paginación reflejan lo filtrado, no el orden

El indicador "X–Y de Z" y el número de páginas se recalculan sobre el conjunto **ya filtrado**. Si los filtros dejan 12 correos, el total es 12 y habrá una sola página. **Reordenar no cambia el total**: el orden solo afecta a qué fila va antes que otra, no a cuántas hay. (Internamente, el conteo total no recibe el criterio de orden precisamente porque ordenar no altera el tamaño del conjunto.)

---

## 5. Interacción con la vista de conversaciones

En las bandejas reales, las filas se presentan **agrupadas por conversación** (cada fila es un hilo — ver [conversaciones.md](conversaciones.md)). Los filtros se aplican **mensaje a mensaje** y luego se agrupa, exactamente igual que ya hace la búsqueda de la lupa. En la práctica:

- Una **conversación aparece** si **al menos uno** de sus mensajes cumple el filtro (p. ej. tiene un mensaje no leído).
- La fila representa el **mensaje más reciente** del hilo, y el contador de mensajes del hilo refleja los que están presentes en esa bandeja.

Esto es coherente con cómo ya se comporta la búsqueda (un hilo aflora si cualquiera de sus mensajes casa).

El **orden** elegido se aplica sobre las filas-conversación ya formadas: los hilos se ordenan por la fecha / remitente / asunto de su **mensaje representativo** (el más reciente del hilo).

---

## 6. El chip "Con adjuntos" y su límite conocido

El chip **Con adjuntos** se apoya en el mismo indicador que pinta el clip en la lista. Ese indicador tiene una particularidad ya existente en la app: **arranca apagado y solo se enciende la primera vez que alguien abre el correo** y la app descubre que traía partes descargables (estrategia "perezosa"; el porqué completo está en [adjuntos.md](adjuntos.md)).

Consecuencia para el usuario: el chip **Con adjuntos** puede **dejar fuera** correos que sí tienen adjunto pero que **nadie ha abierto todavía** desde la última sincronización. La cobertura mejora sola a medida que se van abriendo correos.

Es exactamente el mismo comportamiento (y el mismo límite) que ya tiene el operador `has:attachment` de la lupa — lógico, porque el chip **es** ese operador por debajo. Se asume de forma consciente: ofrecer el chip aporta más de lo que resta, y arreglar el fondo exigiría cambiar cómo se detectan los adjuntos en la sincronización, que queda fuera de este alcance. Queda recogido en [../limits/ordenar-y-filtrar-listado.md](../limits/ordenar-y-filtrar-listado.md).

---

## 7. Estados visibles

- **Filtro o búsqueda sin resultados:** si hay chips activos (o una búsqueda escrita) y nada coincide, se muestra un mensaje de "no hay correos que coincidan con los filtros" (distinto del de bandeja vacía), y **sin barra de paginación**. Este mensaje unifica el caso "búsqueda sin resultados" y el caso "filtros sin resultados" en las bandejas reales.
- **Mientras carga otra página / cambia el orden:** la lista previa se mantiene en pantalla hasta que llega la nueva (sin parpadeo), como ya ocurre al paginar ([listado-de-correos.md](listado-de-correos.md) § 7.3).
- El resto de estados (cargando por primera vez, error, bandeja vacía) no cambian respecto al listado base.

---

## Resumen en una frase

> En las bandejas reales (unificada y por cuenta) el listado deja de estar atado al orden por fecha descendente: desde una barra de controles junto a la lupa —que funciona en móvil y escritorio— el usuario puede **ordenar por fecha, remitente o asunto** en ambos sentidos y **filtrar de un clic** por **no leídos / con adjuntos / destacados**, chips combinables entre sí, compatibles con la búsqueda y equivalentes a los operadores `is:unread` / `has:attachment` / `is:favorite` de la lupa; el orden por fecha descendente sigue siendo el valor por defecto, cualquier cambio reinicia a la página 1 y se refleja en la URL (y se reinicia al cambiar de bandeja o cuenta), el total se recalcula sobre lo filtrado pero no cambia al reordenar, y el chip "Con adjuntos" hereda el límite "perezoso" del indicador de adjuntos; Favoritos y las bandejas ficticias quedan fuera de este alcance. Las cifras exactas y lo que no soporta están en [../limits/ordenar-y-filtrar-listado.md](../limits/ordenar-y-filtrar-listado.md).
