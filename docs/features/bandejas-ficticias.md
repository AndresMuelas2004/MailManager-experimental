# Bandejas ficticias (virtuales) — comportamiento (MVP)

Este documento describe **qué hace** una bandeja ficticia cuando un usuario la usa y **qué experimenta** delante de la app, sin entrar en cómo está cableado el código. Es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la pantalla.

Una bandeja ficticia (o "virtual") es una **vista filtrada y guardada** sobre correos que ya están sincronizados en la app. No es una carpeta nueva en Gmail/Outlook, no descarga nada del proveedor y no mueve ni copia ningún correo: es una "lente" que el usuario define una vez y reutiliza para ver, en una sola pantalla, un subconjunto de sus correos repartidos entre varias cuentas.

Los topes concretos (criterios disponibles, longitudes mínimas, límites de la consulta) y la lista exhaustiva de "qué NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md)**. Este fichero solo menciona los límites de pasada y enlaza a ese catálogo cuando hace falta.

---

## 1. Qué es y para qué sirve

El usuario tiene varias cuentas de correo conectadas (Gmail, Outlook), posiblemente repartidas entre distintas "bandejas reales". Una bandeja ficticia le permite responder a preguntas del tipo:

- "Enséñame **solo las facturas** que llegan a **cualquiera** de mis tres cuentas de trabajo."
- "Quiero una vista con **todo lo no leído** de mi cuenta personal y la del banco juntas."
- "Júntame los **favoritos** de estas dos cuentas en una sola pantalla."

Para conseguirlo, una bandeja ficticia guarda dos cosas:

1. **Una lista de cuentas** elegidas a mano (un correo de una cuenta que no esté en la lista nunca aparecerá).
2. **Un conjunto de filtros** opcionales (carpeta, remitente, asunto, leído/no leído, favorito) que se aplican sobre los correos de esas cuentas.

La pantalla resultante se ve y se comporta como un buzón normal: la misma tabla de correos, la misma lupa de búsqueda, las mismas acciones (abrir, marcar favorito, mover a papelera, responder, reenviar…). La diferencia es que el contenido sale de **varias cuentas a la vez** y ya viene recortado por los filtros guardados.

> Importante: la bandeja ficticia es una **definición, no una colección**. No "contiene" correos; los **calcula** cada vez que se abre, mirando lo que hay en ese momento en la base de datos local. Esto tiene consecuencias visibles que se explican a lo largo del documento (por ejemplo, qué pasa cuando se desconecta una cuenta).

---

## 2. Crear y editar una bandeja ficticia

### 2.1 El formulario

Desde la pantalla de bandejas ficticias, "Nueva bandeja ficticia" abre un formulario con tres bloques:

- **Nombre**: un texto libre obligatorio (ej. "Facturación", "Newsletters", "Notificaciones de banca"). Los espacios sobrantes al principio y al final se recortan; un nombre compuesto solo por espacios se rechaza con un mensaje claro, no se guarda en blanco.
- **Cuentas**: un acordeón que agrupa las cuentas del usuario **por bandeja real**, únicamente para ayudarle a localizarlas. Al marcar cuentas, estas se acumulan como **chips** arriba; cada chip muestra el email (o la etiqueta de la cuenta) y su proveedor, y se puede quitar con la "✕". Hay que seleccionar **al menos una** cuenta.
- **Filtros**: todos opcionales. Si no se rellena ninguno, la bandeja muestra **todos** los correos de las cuentas elegidas, excluyendo papelera y spam (ver § 4.1).

Editar una bandeja existente reutiliza el mismo formulario, precargado con sus valores. La edición es un **reemplazo completo**: lo que se envía sustituye por entero el nombre, la lista de cuentas y los filtros anteriores (no es un parcheo campo a campo). Si el usuario abre la edición y guarda sin tocar nada, la definición queda igual.

### 2.2 Qué se valida y por qué

La app rechaza ciertas entradas **antes** de guardarlas, para que un error del usuario no se convierta en una bandeja silenciosamente inútil:

- **Una cuenta que no es tuya**: si por cualquier vía llega a la petición un identificador de cuenta que el usuario no posee, la creación/edición falla sin guardar nada. Es una defensa de seguridad: sin ella alguien podría fabricar una bandeja que agregara cuentas ajenas y leer correo de otro. El error no distingue "esa cuenta no existe" de "esa cuenta no es tuya" — se devuelve el mismo "cuenta no encontrada" a propósito, para no filtrar qué cuentas existen probando identificadores al azar.
- **Un filtro inexistente o mal escrito**: el lenguaje de filtros es una lista cerrada. Si se envía un criterio que no está en esa lista (una errata, o un filtro antiguo ya retirado), la app responde con un error de validación en vez de tragárselo en silencio. El motivo es de diagnóstico: un filtro silenciosamente ignorado haría que la bandeja mostrara más correos de los que el usuario cree haber pedido, sin que nada se lo avise.
- **Un filtro de texto vacío**: rellenar "asunto contiene" o "remitente exacto" con una cadena vacía se rechaza. Pedir "asunto contiene «»" equivaldría a "que contenga cualquier cosa", es decir, a no filtrar — pero indistinguible de un fallo. La forma correcta de quitar un filtro es **dejarlo en blanco/sin marcar**, no escribir un espacio.

Estos detalles concretos (qué criterios existen, sus longitudes) están en [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md).

---

## 3. Cómo se eligen las cuentas (y por qué la lista es fija)

### 3.1 La lista es una "foto", no una regla que crece sola

Cuando el usuario crea la bandeja, **escoge a mano** las cuentas que agrega. Si más adelante conecta una cuenta nueva, esa cuenta **no** se une automáticamente a las bandejas ficticias que ya existían. Para incluirla, hay que **editar** la bandeja y añadirla.

Esto es una decisión deliberada. En una versión anterior existía un modo "todas mis cuentas" que se autoexpandía: cualquier cuenta nueva entraba sola en esas bandejas. Se retiró porque era peligroso de forma silenciosa — imagina una bandeja "Trabajo" que, al conectar una Gmail personal, empieza a mezclar correo privado sin que nadie lo haya pedido. Con la lista explícita, los chips del formulario **nombran exactamente** qué cuentas entran: la frontera es visible.

> El "porqué" técnico y la migración que congeló las bandejas antiguas a una lista fija se detallan en [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md).

### 3.2 Una cuenta que deja de ser tuya desaparece sola de la vista

La lista de cuentas guardada es una foto del momento de crearla; el mundo cambia después. Si el usuario **desconecta una cuenta** o **pierde acceso a una bandeja real** que contenía cuentas de esta bandeja ficticia, al volver a abrirla:

- La bandeja **sigue funcionando**: no da error 404 ni se rompe.
- Simplemente **muestra solo los correos de las cuentas que el usuario todavía posee**. Las cuentas perdidas se ignoran en silencio.

Es el comportamiento esperado de una "definición, no colección": en cada apertura se revalida qué cuentas siguen siendo del usuario y se trabaja con el subconjunto superviviente.

#### Ejemplo

> Una bandeja "Proyectos" agrega la cuenta Gmail de trabajo y una Outlook de un cliente. El cliente termina y el usuario desconecta esa Outlook. Al abrir "Proyectos", ya solo ve correos de la Gmail; ninguna fila del cliente aparece, y no hay ningún error. Si volviera a conectar esa Outlook, **no** reaparecería sola en "Proyectos" hasta que la añada de nuevo en la edición.

### 3.3 Borrar una bandeja ficticia

Borrar una bandeja ficticia elimina **solo la definición** (el nombre, la lista de cuentas y los filtros). **No toca ningún correo**: los mensajes siguen exactamente donde estaban, en sus cuentas y carpetas reales. Es una operación segura e instantánea. La app pide confirmación antes de borrar.

---

## 4. Los filtros: qué se puede recortar

Una bandeja ficticia puede combinar varios criterios. Todos son opcionales y, cuando se ponen varios, se aplican **a la vez** (un correo tiene que cumplirlos todos para aparecer). El catálogo completo de criterios disponibles está en [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md); aquí va el comportamiento.

### 4.1 Carpeta (box): exclusión de papelera y spam por defecto

El filtro de carpeta tiene un comportamiento con matiz importante:

- **Si no se elige carpeta** (lo más común): la bandeja muestra los correos de las cuentas seleccionadas **excluyendo papelera y spam**. Es el mismo criterio "flujo activo" que usa la vista de Favoritos — el usuario casi nunca quiere ver basura y spam mezclados con lo relevante.
- **Si se elige una carpeta concreta** (Enviados, Spam o Papelera): la bandeja muestra **solo** esa carpeta, ignorando la exclusión por defecto. Es decir, una bandeja "Papelera de estas cuentas" sí enseña la papelera.
- **Si se pide explícitamente "no excluir nada"**: existe una forma de ver papelera y spam **junto con** el resto. No es un botón evidente en el formulario actual; es una capacidad del filtro pensada para vistas que quieran abarcarlo todo.

Hay una regla de coherencia: **no se puede pedir a la vez "solo esta carpeta" y "todo excepto estas carpetas"**. Son criterios contradictorios; la app rechaza esa combinación con un error de validación en lugar de aplicar las dos cosas y devolver una bandeja perpetuamente vacía (que el usuario confundiría con "no hay correos").

#### Ejemplo

> - Bandeja sin carpeta → ve INBOX/ALL_MAIL y Enviados de las cuentas, pero **no** Papelera ni Spam.
> - Bandeja con carpeta = "Papelera" → ve **solo** la papelera de esas cuentas.
> - Bandeja con "excluir solo Spam" → ve todo (incluida la **papelera**) menos el spam.

### 4.2 Remitente exacto

El filtro "remitente exacto" casa cuando el email del remitente es **idéntico** al indicado (sin distinguir mayúsculas/minúsculas). Ojo: es coincidencia **exacta de la dirección completa**, no "contiene". Buscar `jack@devops.net` trae los correos de exactamente esa dirección, no los de `jack@otra.com` ni los que solo mencionen "devops" en el asunto.

Esto lo diferencia de la lupa de texto libre (que sí es "contiene"): el filtro de remitente está pensado para "todo lo que me manda **esta** persona/sistema", una intención de igualdad, no de subcadena.

### 4.3 Asunto contiene

El filtro "asunto contiene" casa cuando el asunto **incluye** el texto indicado en cualquier posición, ignorando mayúsculas/minúsculas **y tildes/acentos**. Escribir `factura` encuentra "Factura mensual", "REFACTURA" y "tu facturación"; escribir `accion` encuentra "Acción requerida". Es la misma normalización de acentos que usa la lupa, imprescindible para trabajar en español.

> Los caracteres especiales de patrón (`%`, `_`) se tratan como texto literal, no como comodines — el usuario no puede inyectar comodines ni romper la consulta.

### 4.4 Leído / no leído y Favoritos

Dos filtros de estado, cada uno con tres opciones (cualquiera / sí / no):

- **Leído**: "solo no leídos" o "solo leídos".
- **Favorito**: "solo favoritos" o "excluir favoritos".

Se combinan con el resto. Por ejemplo, "no leídos + asunto contiene «pedido»" trae solo los correos sin leer cuyo asunto mencione pedidos.

### 4.5 Cómo se combina todo

Todos los criterios rellenados se exigen **simultáneamente**, y además se cruzan con la lupa de búsqueda si el usuario escribe algo en ella (ver § 5). En una frase: **más filtros = menos resultados**, nunca más.

#### Ejemplo

> Bandeja "Facturas trabajo": cuentas = [Gmail-trabajo, Outlook-trabajo], filtros = {asunto contiene "factura", no leídos}. Resultado: los correos **no leídos** de **esas dos cuentas** cuyo **asunto** mencione "factura", excluyendo papelera y spam, ordenados por fecha. Un correo de factura ya leído no aparece; uno no leído de una tercera cuenta tampoco.

---

## 5. La lupa dentro de una bandeja ficticia

La pantalla de una bandeja ficticia incluye la **misma lupa de búsqueda** que el resto de buzones. Funciona igual que en [lupa.md](lupa.md): es una búsqueda local, literal, sobre asunto + email del remitente + nombre del remitente, que normaliza mayúsculas y tildes y exige que **todas** las palabras aparezcan en algún campo.

La diferencia clave es el **alcance**: aquí la lupa busca dentro del **resultado ya filtrado** de la bandeja ficticia, es decir, dentro de las cuentas seleccionadas y respetando los filtros guardados. La búsqueda **se suma** a los filtros, no los sustituye. El término de búsqueda vive en la URL, igual que en los buzones normales, así que recargar la página o compartir el enlace conserva la búsqueda.

> Esta lupa de texto libre y los filtros guardados de la bandeja ficticia son **mecanismos distintos** que conviven: los filtros definen la bandeja; la lupa es un recorte temporal sobre lo que la bandeja ya muestra.

#### Ejemplo

> En la bandeja "Facturas trabajo" del ejemplo anterior, el usuario escribe `marzo` en la lupa. Ahora ve solo los correos que (1) son de las cuentas de trabajo, (2) tienen asunto con "factura", (3) están sin leer **y** (4) contienen "marzo" en asunto, email o nombre del remitente.

---

## 6. La vista de correos: igual que un buzón, con un matiz multi-cuenta

La tabla de correos de una bandeja ficticia es la misma que la de cualquier buzón: filas con remitente, asunto, fecha, indicadores de no leído, favorito y adjunto, y casillas de selección para acciones en bloque.

### 6.1 Por qué la columna muestra la cuenta de cada correo

Como una bandeja ficticia mezcla correos de **varias cuentas**, la tabla se muestra en modo "unificado": cada fila deja claro a qué cuenta pertenece el correo. En un buzón de una sola cuenta esa columna sería redundante (todas las filas serían la misma cuenta); aquí es necesaria para distinguir.

### 6.2 Por qué cada acción "sabe" a qué cuenta ir

Aquí hay una asimetría sutil pero crítica. Cada correo de la lista **lleva consigo cuál es su bandeja real y su cuenta real**. Cuando el usuario abre un correo, lo marca como favorito, lo mueve a la papelera o descarga un adjunto, la acción se dirige a la **cuenta real de ese correo**, no a la bandeja desde la que se está mirando.

El motivo: una bandeja ficticia puede agregar cuentas que viven en **bandejas reales diferentes**. Si una acción usara "la bandeja actual" en lugar de la del correo concreto, fallaría con "cuenta no encontrada" para todos los correos cuya cuenta vive en otra bandeja. Esto aplica también a las **acciones en bloque**: seleccionar correos de varias cuentas y, por ejemplo, marcarlos como leídos, funciona porque la app agrupa la selección por su cuenta real y lanza la operación a cada una por separado.

### 6.3 Responder, reenviar y demás

Abrir un correo desde una bandeja ficticia permite las mismas acciones que desde un buzón normal: leer el contenido, responder, responder a todos, reenviar. Todo se comporta exactamente igual que en [adjuntos.md](adjuntos.md) y el resto de la app, porque por debajo el correo es un mensaje real de una cuenta real.

### 6.4 No hay botón de "sincronizar" aquí

La bandeja ficticia **no** ofrece un botón para sincronizar correo nuevo. La sincronización se hace desde las bandejas reales; la ficticia solo **muestra** lo que esas ya bajaron. Un "sincronizar" en este contexto tendría que abanicar peticiones a todas las cuentas implicadas, lo que es una decisión de producto aparte. Lo que sí hace la vista es **refrescarse de forma agresiva**: cada vez que el usuario vuelve a abrir la bandeja, vuelve a pedir la lista al instante, sin servir una versión cacheada antigua. La razón es que estas vistas son curadas y sensibles al tiempo ("lo de hoy", "lo no leído"): el usuario espera ver lo recién llegado en cuanto entra, no una foto de hace medio minuto.

---

## 7. Casos borde y comportamiento ante conflictos

### 7.1 El mismo correo en dos cuentas: se muestra una sola vez

Un mismo correo físico puede aparecer **dos veces** en los datos cuando el usuario ha conectado **la misma cuenta de proveedor bajo dos bandejas reales distintas** (dos "cuentas" en la app que apuntan al mismo buzón de Gmail/Outlook). Si una bandeja ficticia agrega ambas, ese correo saldría duplicado.

La app **colapsa esos duplicados** y muestra **una sola fila** por mensaje. Para decidir cuál de las dos copias gana, prefiere la que tenga datos más completos (una dirección de destinatario real por delante de una vacía; en empate, la más reciente). Es una decisión de presentación: el usuario ve un buzón limpio sin filas repetidas.

> El detalle de la preferencia de desempate está en [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md). Nótese que esta deduplicación es **exclusiva** de las bandejas ficticias: en un buzón normal, acotado a una sola cuenta, la duplicación no puede ocurrir.

### 7.2 Bandeja vacía vs. sin resultados de búsqueda

La pantalla distingue dos situaciones que parecen iguales pero no lo son:

- **"Ningún correo coincide con los filtros de esta bandeja ficticia"**: la definición no encuentra nada (filtros demasiado estrictos, o cuentas sin correos que casen).
- **"No se encontraron correos para tu búsqueda en esta bandeja ficticia"**: hay correos en la bandeja, pero la **lupa** no ha encontrado coincidencias para el texto escrito.

Diferenciarlos ayuda al usuario a saber si debe relajar los filtros guardados o simplemente cambiar lo que busca.

### 7.3 La bandeja ya no existe

Si el usuario abre el enlace de una bandeja ficticia que ha sido **borrada** (por él en otra pestaña, por ejemplo) o cuya URL es incorrecta, la app no muestra una pantalla rota ni un título obsoleto: enseña un estado dedicado "Esta bandeja ficticia ya no existe", con un botón para volver al listado. Lo mismo ocurre si se intenta editar o borrar una bandeja que ya desapareció — la operación responde "no encontrada" de forma limpia, nunca con un éxito silencioso ni un error genérico.

### 7.4 Acceso a bandejas de otros usuarios

Una bandeja ficticia pertenece a su creador. Intentar acceder a la de otro usuario (probando identificadores) responde uniformemente "no encontrada", igual que si no existiera — nunca un "prohibido" que confirmaría que esa bandeja existe.

---

## 8. Qué NO hace una bandeja ficticia

Para fijar expectativas (la lista completa con el porqué de cada límite está en [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md)):

- **No descarga correo nuevo** del proveedor ni importa mensajes que no estén ya sincronizados. Si una cuenta favorita en el proveedor todavía no se ha sincronizado en la app, no aparece.
- **No es una carpeta real**: no existe en Gmail/Outlook, no se puede mover un correo "a" una bandeja ficticia.
- **No autoexpande** la lista de cuentas al conectar cuentas nuevas (§ 3.1).
- **No ofrece acciones en bloque propias** distintas de las que ya da la tabla de correos: la bandeja es de **solo lectura** respecto a su definición; las acciones operan sobre los correos reales subyacentes.
- **No ordena por relevancia** ni soporta scroll infinito: hereda el comportamiento de la lista de correos (orden por fecha descendente, con un tope de resultados por carga).

---

## Resumen en una frase

> Una bandeja ficticia es una vista guardada, de solo lectura y calculada al vuelo, que junta en una sola pantalla los correos **ya sincronizados** de una lista fija de cuentas (elegidas a mano, sin autoexpansión) recortados por filtros opcionales que se aplican todos a la vez (carpeta —con papelera y spam excluidos por defecto—, remitente exacto, asunto contiene, leído y favorito), excluye `box` y `box_not_in` como mutuamente contradictorios, deduplica el mismo mensaje cuando llega por dos cuentas, dirige cada acción a la cuenta real de cada correo aunque viva en otra bandeja, revalida la propiedad de las cuentas en cada apertura quedándose con el subconjunto superviviente, y nunca toca el correo real al crearse, editarse o borrarse; las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md).
