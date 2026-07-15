# Buzones reales y vista unificada — comportamiento (MVP)

Este documento describe **qué es un buzón** dentro de MailManager, **cómo lo crea y lo vive el usuario**, y cómo la app decide mostrar los correos de **todas las cuentas a la vez** (vista unificada) o de **una sola cuenta** (vista de cuenta concreta). No entra en código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se comporta la app cuando se sienta delante de ella.

Frontera de este documento: aquí se explica el **modelo** de buzón y de vista, y la lógica de las columnas "Para" / "De". El **listado de correos en sí** —qué bandejas existen (recibidos, enviados, spam, papelera), en qué orden salen los correos, cómo se pagina— vive en su propio documento. La **lupa de búsqueda** se documenta en [lupa.md](lupa.md) y las **bandejas ficticias** (vistas guardadas por criterios) en [bandejas-ficticias.md](bandejas-ficticias.md): ambas son funcionalidades distintas que se montan *sobre* el modelo de buzón descrito aquí.

Los pocos topes numéricos que tiene esta funcionalidad y la lista de "lo que deliberadamente NO hace" viven en un documento aparte: **[../limits/buzones-y-vista-unificada.md](../limits/buzones-y-vista-unificada.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están las cifras exactas.

---

## 1. Qué es un buzón

Un **buzón** (en la interfaz, "bandeja") es un **contenedor que agrupa varias cuentas de correo** bajo un mismo nombre. No es una cuenta de Gmail ni de Outlook: es una carpeta lógica que el usuario crea en MailManager y a la que después **conecta** una o varias cuentas reales (de Gmail, de Outlook, o una mezcla de ambas).

La idea de producto es directa: el usuario quiere ver "Trabajo" en un sitio y "Personal" en otro, sin importar que "Trabajo" combine una cuenta de Gmail corporativa y una de Outlook. El buzón es esa agrupación.

Propiedades de un buzón:

- **Tiene un nombre visible** que el usuario elige al crearlo (p. ej. "Trabajo", "Personal", "Universidad"). Ese nombre es lo único que el usuario define; todo lo demás (identificador interno, fecha de creación, propietario) lo gestiona la app.
- **Pertenece a un único usuario.** Cada buzón está ligado a la cuenta con la que el usuario inició sesión en MailManager. Un usuario no ve los buzones de otro, y un buzón no se comparte entre usuarios.
- **Agrupa cuentas conectadas.** Las cuentas reales (los correos de Gmail/Outlook que el usuario autoriza) "cuelgan" del buzón. Conectar y desconectar cuentas es una funcionalidad propia (la pantalla "Cuentas conectadas"), pero el contenedor donde viven es el buzón.
- **Es la raíz de la navegación.** Una vez dentro de un buzón, toda la barra lateral (bandeja unificada, enviados, favoritos, spam, papelera, borradores, bandejas ficticias) opera **dentro de ese buzón**. La dirección de la página siempre lleva el buzón activo, así que recargar o compartir un enlace conserva el contexto.

### 1.1 Relación buzón ↔ cuentas ↔ correos

La jerarquía es estricta y conviene tenerla clara porque explica casi todo el comportamiento posterior:

```
Usuario
  └── Buzón ("Trabajo")
        ├── Cuenta A (Gmail)  ──► sus correos
        └── Cuenta B (Outlook) ──► sus correos
```

- Un **correo pertenece a exactamente una cuenta**, y esa cuenta pertenece a exactamente un buzón. No hay correos "sueltos" ni correos compartidos entre cuentas.
- Cuando se **borra un buzón**, se llevan por delante en cascada todas sus cuentas, sus credenciales y todo lo asociado. No queda nada huérfano. Lo mismo ocurre si se elimina el usuario entero: desaparecen sus buzones y, con ellos, todo lo de dentro. Es una limpieza automática a nivel de base de datos, no algo que la app tenga que recorrer manualmente.
- Cada correo "sabe" a qué buzón real pertenece. Esto es invisible en el uso normal, pero es **decisivo** para las bandejas ficticias, que pueden mezclar cuentas de buzones distintos (ver sección 5.3).

---

## 2. Crear un buzón

### 2.1 El primer buzón: onboarding

La primera vez que un usuario entra en MailManager **sin ningún buzón**, la app lo lleva automáticamente a una pantalla de bienvenida ("Crea tu primera bandeja"). Es un paso obligado: sin buzón no hay dónde colgar cuentas ni dónde ver correos.

Esa pantalla pide **una sola cosa**: el nombre del buzón. Hay un campo de texto con sugerencias de ejemplo ("Ej: Trabajo, Personal, Universidad…") y un botón "Crear bandeja". El botón permanece deshabilitado mientras el campo esté vacío (o solo tenga espacios); en cuanto hay texto real, se habilita.

Al crear el buzón, la app navega directamente a la pantalla de **cuentas conectadas** del buzón recién creado, que es el siguiente paso natural: ahora que existe el contenedor, toca conectar la primera cuenta de Gmail o de Outlook.

#### Ejemplo

> Un usuario nuevo inicia sesión. No tiene buzones, así que aterriza en "Crea tu primera bandeja". Escribe `Trabajo`, pulsa "Crear bandeja" y la app lo deja en la pantalla de cuentas conectadas de "Trabajo", listo para conectar su Gmail.

### 2.2 Buzones adicionales

Una vez dentro de la app, el usuario puede crear **más buzones** en cualquier momento, sin límite práctico (ver [../limits/buzones-y-vista-unificada.md](../limits/buzones-y-vista-unificada.md)). El selector de buzón vive en la cabecera de la barra lateral: muestra el buzón activo y, al desplegarlo, lista todos los buzones del usuario con una marca en el actual, más una opción "Crear nueva bandeja".

"Crear nueva bandeja" abre un pequeño campo en línea dentro del propio desplegable: el usuario teclea el nombre, confirma, y la app crea el buzón y **navega a su pantalla de cuentas conectadas**, igual que en el onboarding. El nuevo buzón aparece al instante en el selector porque la app lo añade a su lista local sin esperar a recargar nada.

### 2.3 Cambiar de buzón

Desde ese mismo selector, elegir otro buzón **cambia todo el contexto**: la barra lateral y el contenido pasan a operar sobre el buzón elegido. La app lleva al usuario a la pantalla de cuentas conectadas del buzón seleccionado.

Un detalle de continuidad: al cambiar de bandeja (recibidos, enviados, etc.) **dentro** de un mismo buzón, la app conserva los parámetros de la dirección actual —en particular, el término de búsqueda activo de la lupa— para no perder el filtro al saltar entre secciones. El cambio de **buzón** sí reinicia el contexto.

### 2.4 Renombrar y eliminar un buzón

Un buzón ya creado se puede **renombrar** y **eliminar** desde la interfaz. Hay dos puntos de entrada para ambas acciones: la sección **"Bandejas"** del área de Ajustes (que las ofrece para **todas** las bandejas a la vez) y el desplegable del selector de la cabecera (para la bandeja activa). El flujo de cada acción —edición en línea del nombre al renombrar, confirmación que enumera lo que se borra al eliminar, y a dónde va el usuario tras borrar la bandeja que estaba viendo— se documenta en [ajustes.md](ajustes.md) § 4.

El **borrado es en cascada**: se lleva por delante las cuentas de esa bandeja y todos sus correos sincronizados (ver § 1.1). La confirmación lo avisa antes de ejecutar. Borrar la última bandeja está permitido: el usuario vuelve a "crear bandeja".

Lo que el modelo de buzón sigue **sin** ofrecer es **compartir** un buzón con otro usuario y **mover** una cuenta de un buzón a otro. El detalle de estas ausencias está en [../limits/buzones-y-vista-unificada.md](../limits/buzones-y-vista-unificada.md). La promesa que la interfaz hace al crear el buzón ("Podrás cambiarlo más tarde") ya se cumple para el nombre.

---

## 3. Las dos formas de ver los correos de un buzón

Aquí está el corazón de la funcionalidad. Un buzón puede contener varias cuentas, así que la app ofrece **dos formas de mirar sus correos**, y el usuario alterna entre ellas con un clic:

> Tanto la vista unificada como la de cuenta concreta presentan hoy sus filas **agrupadas por conversación** (una fila por hilo — ver [conversaciones.md](conversaciones.md)). Esa agrupación **no** altera la lógica de columnas "Para"/"De" de la sección 4 ni los modos de tabla: la fila-conversación toma como cara visible su mensaje más reciente y resuelve "Para"/"De" con el mismo criterio. La fila **conserva la casilla de selección** y su barra de acciones masivas (que actúan sobre ese mensaje más reciente); lo único que pierde respecto a la fila clásica es la **estrella clicable** (el favorito se alterna por mensaje en el visor del hilo).

### 3.1 Vista unificada (todas las cuentas del buzón)

Es la vista por defecto y la razón de ser de la app. Combina en **una sola lista** los correos de **todas las cuentas conectadas al buzón**, ordenados juntos. El usuario ve su Gmail de trabajo y su Outlook de trabajo entremezclados como si fueran un único buzón.

Cada sección de la barra lateral (Bandeja unificada, Enviados, Spam, Papelera) es una vista unificada de la bandeja correspondiente: "Enviados" unificado muestra lo enviado desde **cualquiera** de las cuentas del buzón, "Spam" unificado el spam de todas, etc.

### 3.2 Vista de una cuenta concreta

En la barra lateral, un **selector desplegable de cuentas** deja elegir el ámbito: su botón muestra el ámbito activo ("Todas las cuentas" para la vista unificada, o el correo de la cuenta en la que estés) y, al abrirlo, despliega la lista con "Todas las cuentas" más cada cuenta conectada al buzón (con el color de su proveedor); las cuentas permanecen ocultas mientras no se abre. Al elegir una cuenta, todas las bandejas (recibidos, enviados, spam, papelera, borradores) se filtran **solo a esa cuenta**: es el equivalente a "entrar dentro de" una de las cuentas del buzón. Las carpetas se recorren desde la **misma barra lateral** —no hay una fila de pestañas aparte encima del listado—, de modo que toda la navegación (elegir cuenta + elegir carpeta) vive en un único sitio a la izquierda.

La vista de cuenta concreta se distingue de la unificada por un **título con la identidad de la cuenta**: si la cuenta tiene una etiqueta personalizada, se muestra como "Etiqueta - correo@ejemplo.com"; si no, simplemente la dirección de correo. Además, la entrada de la bandeja de entrada en la barra lateral pasa de leerse "Bandeja unificada" a "Bandeja de entrada" (dentro de una cuenta no hay nada que unificar).

### 3.3 Cómo se distingue una vista de la otra

La diferencia la marca la **dirección de la página**: la vista unificada no nombra ninguna cuenta, mientras que la vista de cuenta concreta lleva el identificador de la cuenta en la ruta. El usuario no gestiona esto manualmente; elige el ámbito (Todas o una cuenta) en el selector de la barra lateral y las carpetas de esa misma barra lo llevan a la bandeja correspondiente **dentro** del ámbito elegido, y la app sabe en qué modo está.

### 3.4 Acceso rápido a "Añadir cuenta"

Conectar una cuenta nueva vive en Ajustes → Cuentas conectadas, pero la app ofrece **dos accesos directos permanentes** para que un usuario recién llegado (o que quiere una cuenta más) encuentre el camino sin buscar:

- **En el selector de cuentas de la barra lateral**: el desplegable termina siempre con una entrada "Añadir cuenta", separada de la lista, visible tanto en el ámbito unificado como dentro de una cuenta concreta. Elegirla navega a la pantalla de cuentas conectadas del buzón activo.
- **En la cabecera de la Bandeja unificada**: junto al botón "Refrescar" hay un botón "Añadir cuenta" con el mismo destino. Aparece **solo en la bandeja de entrada unificada** (la vista donde aterriza el usuario), siempre — con o sin cuentas conectadas; las demás bandejas unificadas (Enviados, Archivados, Spam, Papelera) y las vistas de cuenta concreta no lo muestran.

Ninguno de los dos accesos inicia la conexión por sí mismo: ambos llevan a la pantalla de cuentas conectadas, que es donde vive el flujo real de conexión.

---

## 4. La lógica de columnas "Para" / "De"

Este es el comportamiento menos obvio y el que más fácilmente se rompe en una regresión, así que merece su propia sección. La tabla de correos siempre muestra una columna fija de **Remitente** (un nombre corto identificando el origen) y, según el contexto, una columna **"Para"**, una columna **"De"**, o **ambas**.

La decisión de qué columnas mostrar **no es arbitraria**: responde a "qué información le falta al usuario para entender de un vistazo cada fila". Mostrar una columna cuyo valor es idéntico en todas las filas es ruido inútil; ocultarla cuando varía deja al usuario sin contexto.

### 4.1 La regla, en lenguaje natural

Hay tres modos de tabla, y cada uno resuelve las columnas de forma distinta:

**Modo "cuenta concreta".** El usuario ya sabe de qué cuenta son los correos (está dentro de ella), así que la propia dirección de la cuenta es constante en todas las filas y no aporta nada mostrarla. Por eso:

- En una bandeja de **recibidos** de una cuenta, solo importa **de quién** viene cada correo → se muestra solo **"De"** (el remitente real del mensaje).
- En una bandeja de **enviados** de una cuenta, todos los correos los envió la propia cuenta, así que lo que varía y aporta es **a quién** se mandó → se muestra solo **"Para"** (el destinatario).

**Modo "unificado".** Aquí conviven correos de varias cuentas del buzón, y el usuario necesita desambiguar **cuál de sus cuentas** está implicada en cada fila. Por eso se muestran **ambas columnas, "Para" y "De"**, siempre. El sentido de cada celda lo fija si la bandeja es de enviados o de recibidos:

- En recibidos unificados: "De" es el remitente real, y "Para" es **la cuenta del propio usuario** en la que aterrizó ese correo (sirve para saber a cuál de sus cuentas llegó).
- En enviados unificados: "De" es la cuenta del propio usuario que lo envió, y "Para" es el destinatario real.

**Modo "mixto".** Es un modo especial para listados que **mezclan correos recibidos y enviados en la misma tabla**. Hoy lo usa únicamente la pantalla de **Favoritos** (ver [favoritos.md](favoritos.md)), donde un favorito puede ser tanto algo que recibiste como algo que enviaste. En este modo se muestran **ambas columnas**, pero —y esto es lo único que lo diferencia del unificado— el sentido de cada celda **se decide fila a fila** según si ese correo concreto es enviado o recibido, en lugar de aplicar un criterio único a toda la tabla.

**Destinatario ausente en enviados.** En cualquier vista de enviados, si un correo se mandó **sin un destinatario en el campo `To`** (por ejemplo, enviado solo con CC/BCC, o un envío de prueba o antiguo que no guardó destinatario), la columna "Para" muestra un texto tenue **"Sin destinatario"** en vez de quedar en blanco. El destinatario se captura al sincronizar desde la cabecera `To` del mensaje; cuando esa cabecera no traía ninguna dirección, el marcador deja claro que no había nada que mostrar (no es un fallo de la vista).

### 4.2 Por qué el modo "mixto" existe

Sin el modo mixto, una lista de favoritos que mezcla recibidos y enviados tendría que elegir un único criterio para toda la tabla. Si eligiera "tratar todo como recibido", los favoritos que en realidad **enviaste** mostrarían en "De" tu propia cuenta —el mismo valor en todas esas filas— y ocultarían el destinatario real, que es justo el dato útil. El modo mixto resuelve cada fila por separado: un favorito enviado enseña su destinatario real bajo "Para", y un favorito recibido mantiene la semántica de entrada. Es el único sitio de la app donde la tabla consulta la bandeja de cada correo individualmente para decidir el sentido de sus celdas.

### 4.3 Ejemplos concretos

> **Vista de cuenta `ana@gmail.com`, bandeja de recibidos.** La tabla muestra Remitente + **De**. No hay columna "Para": sería siempre `ana@gmail.com` y no aporta nada.

> **Vista de cuenta `ana@gmail.com`, bandeja de enviados.** La tabla muestra Remitente + **Para**. No hay columna "De": todo lo envió Ana.

> **Vista unificada del buzón "Trabajo" (Gmail + Outlook), recibidos.** La tabla muestra Remitente + **Para** + **De**. La columna "Para" deja claro a cuál de las dos cuentas del usuario llegó cada correo; "De" muestra el remitente externo.

> **Pantalla de Favoritos con un correo recibido de `jefe@empresa.com` y un correo que el usuario envió a `cliente@otra.com`.** La tabla está en modo mixto: la fila del recibido muestra "De: jefe@empresa.com", y la fila del enviado muestra "Para: cliente@otra.com" —cada una resuelta según su propia naturaleza, en la misma tabla.

### 4.4 La trampa silenciosa que hay que evitar

Toda página que monte la tabla de correos debe **decidir explícitamente** su modo y si es una bandeja de enviados. Una página nueva que olvide fijar bien estos dos parámetros reintroduce en silencio el problema que toda esta lógica resuelve: o duplica una columna constante e inútil, o —peor— monta un listado mixto como si fuera unificado de recibidos y entonces **todas las filas enviadas colapsan** mostrando la cuenta del propio usuario en ambas columnas, perdiendo el destinatario real. No hay error visible; simplemente la información se degrada. Por eso la regla es que ambos parámetros son obligatorios incluso en el modo mixto (donde el de "enviados" se ignora): mantener el contrato uniforme evita olvidos.

> Las bandejas ficticias son el único caso que **no** fija "enviados" con un valor fijo, sino que lo deduce del filtro guardado de la vista (una bandeja ficticia puede ser una vista guardada de "enviados"). El detalle está en [bandejas-ficticias.md](bandejas-ficticias.md).

---

## 5. Multi-cuenta y multi-buzón: las asimetrías que importan

### 5.1 La vista unificada es "todas las cuentas de este buzón"

Conviene no confundir la **vista unificada** con una "súper-bandeja de todo". La unificada agrupa las cuentas de **un** buzón, no las de todos los buzones del usuario. Para ver "Trabajo" y "Personal" juntos no sirve la vista unificada; eso es justo lo que resuelven las **bandejas ficticias** (ver [bandejas-ficticias.md](bandejas-ficticias.md)), que sí pueden seleccionar cuentas a través de buzones distintos.

### 5.2 La lupa hereda el modo de la vista

La lupa de búsqueda respeta el modo en el que se está mirando la bandeja: en vista unificada busca en **todas** las cuentas del buzón a la vez; en vista de cuenta concreta busca **solo** en esa cuenta. El usuario no configura nada: la búsqueda hereda el alcance del listado. El detalle completo está en [lupa.md](lupa.md).

### 5.3 Acciones por correo: cada correo va a su propia cuenta

Esta es una asimetría sutil pero crítica. Dentro de una vista unificada —y, sobre todo, dentro de una bandeja ficticia que mezcla cuentas de buzones distintos—, cada fila puede pertenecer a una cuenta (y a un buzón real) **diferente** del contexto que el usuario está mirando. Por eso, **cada acción sobre un correo concreto** (abrirlo, marcarlo leído, marcar favorito, mover a papelera, marcar spam, descargar adjunto) se dirige al buzón y la cuenta **reales de ese correo**, no a los de la pantalla en la que está.

El motivo: el backend valida en cada operación que la cuenta pertenezca al buzón indicado. Si una acción usara el buzón de la pantalla en lugar del buzón real del correo, fallaría en cuanto el correo viviera en otro buzón —exactamente el caso de una bandeja ficticia multi-buzón—. Las acciones en bloque resuelven lo mismo agrupando los correos seleccionados por su buzón real y disparando una operación por grupo. Una regresión que volviera a usar "el buzón de la URL" para todo rompería en silencio cualquier selección que cruce buzones.

#### Ejemplo

> El usuario tiene una bandeja ficticia "Importante" que agrupa una cuenta del buzón "Trabajo" y una del buzón "Personal". Selecciona tres correos —dos de Trabajo, uno de Personal— y pulsa "Mover a papelera". La app no hace una sola llamada: agrupa por buzón real y envía la orden de Trabajo a "Trabajo" y la de Personal a "Personal". Los tres acaban en su papelera correspondiente.

---

## 6. Qué experimenta el usuario, de principio a fin

Para entender el flujo completo de un vistazo:

1. Usuario inicia sesión → si no tiene buzones, aterriza en "Crea tu primera bandeja"; si tiene, entra directo al primero.
2. Crea un buzón con un nombre → la app lo lleva a "Cuentas conectadas" de ese buzón.
3. Conecta una o varias cuentas (Gmail/Outlook) → ahora el buzón tiene contenido.
4. Navega por la barra lateral → todo lo que ve (unificada, enviados, favoritos, spam, papelera, borradores, ficticias) está **dentro de ese buzón**.
5. En cualquier bandeja, ve los correos de **todas** las cuentas mezclados (vista unificada), con columnas "Para"/"De" que le dicen qué cuenta suya está implicada.
6. Si quiere centrarse en una sola cuenta, la elige en el selector de cuentas de la barra lateral → mismas bandejas (recorridas desde esa misma barra), pero filtradas a esa cuenta, con su título e identidad propios.
7. Cambia de buzón desde el selector de la cabecera → todo el contexto cambia al nuevo buzón.

---

## 7. Resumen en una frase

> Un buzón es un contenedor por usuario que agrupa varias cuentas reales de Gmail y Outlook bajo un nombre; el usuario crea su primero en el onboarding y los siguientes desde el selector de la cabecera, lo **renombra** o lo **elimina** (con cascada) desde Ajustes o el propio selector, y dentro de cada buzón puede ver los correos de **todas** sus cuentas mezclados (vista unificada, con columnas "Para"/"De" que desambiguan cuál de sus cuentas está implicada) o entrar en **una** cuenta concreta (que oculta la columna redundante y añade identidad y pestañas propias), mientras un modo "mixto" especial resuelve fila a fila los listados que mezclan recibidos y enviados —como Favoritos— y cada acción individual sobre un correo viaja siempre a su buzón y cuenta reales aunque la vista mezcle cuentas de buzones distintos; lo que el modelo deliberadamente sigue sin hacer (compartir, mover cuentas) y sus topes viven en [../limits/buzones-y-vista-unificada.md](../limits/buzones-y-vista-unificada.md), y el flujo de renombrar/eliminar está en [ajustes.md](ajustes.md).
