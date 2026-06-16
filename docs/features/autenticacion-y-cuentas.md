# Autenticación y gestión de cuentas — comportamiento (MVP)

Este documento describe **qué hace** MailManager cuando alguien inicia sesión y cuando vincula o desvincula cuentas de correo, y **qué experimenta** delante de la app. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se comporta la funcionalidad sin tener que leer la implementación.

Hay dos conceptos que conviene no mezclar y que este documento trata en este orden:

1. **La sesión de la aplicación** — quién está usando MailManager. Se obtiene iniciando sesión con Google y vive en una cookie. Es la identidad del *usuario de la app*.
2. **Las cuentas de correo conectadas** — los buzones de Gmail y Outlook cuyos correos la app va a leer y gestionar. Son recursos que el usuario vincula *después* de iniciar sesión.

Iniciar sesión con Google **no** conecta ninguna cuenta de correo: solo identifica a la persona. Son dos autorizaciones OAuth distintas, con proveedores y permisos distintos, y ocurren en momentos distintos.

La frontera de este documento llega hasta el **ciclo de vida de la cuenta y de la sesión**. Cómo se agrupan las cuentas bajo un buzón y cómo se presenta la vista unificada se documenta en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md). Los topes, valores por defecto y la lista de "lo que NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)**.

---

## 1. Iniciar sesión con Google

### 1.1 El flujo que ve el usuario

La pantalla de login muestra el botón oficial de **"Continuar con Google"** (el widget de Google Identity Services). Al pulsarlo, Google gestiona toda la interacción de consentimiento y devuelve a la app una credencial firmada que la identifica. La app la verifica contra el servidor, crea una sesión y, si todo va bien, redirige a la aplicación.

A nivel de experiencia:

- Mientras se verifica la credencial, el botón muestra un breve "Iniciando sesión…".
- Si la verificación falla, aparece un mensaje de error bajo el botón y el usuario se queda en el login.
- Si va bien, entra directamente en la app. La próxima vez que abra MailManager, **seguirá dentro** sin volver a pasar por el login mientras la sesión siga viva (sección 4).

> El login es contra **Google**, sea cual sea el correo del usuario. Es la identidad de la *app*, no el primer correo conectado. Alguien puede entrar con su Google personal y luego conectar cuentas de Outlook del trabajo; son cosas separadas.

### 1.2 Qué valida el servidor antes de dar por buena la sesión

No basta con que Google diga "esta credencial es mía". El servidor exige que la credencial:

- Sea **criptográficamente válida** y esté **emitida para esta aplicación** concreta (se comprueba el destinatario del token). Una credencial generada para otra app distinta se rechaza aunque sea legítima.
- Traiga un **identificador estable de Google** y un **email**. Si falta cualquiera de los dos, el login se rechaza.
- Tenga el **email verificado** por Google (la credencial trae una marca `email_verified`). Una cuenta de Google cuyo email primario no está verificado se rechaza, aunque la credencial sea criptográficamente válida: la identidad se ancla al identificador de Google, pero un email sin verificar no es fiable para mostrarlo ni para futuras notificaciones.

Se tolera un pequeño desfase de reloj entre el cliente y el servidor para que un reloj ligeramente adelantado o atrasado no tumbe logins legítimos (el margen exacto está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)).

La distinción de errores es deliberada: un **fallo de red** al contactar con Google para verificar (el servicio de verificación está caído o inalcanzable) **no** es lo mismo que una credencial inválida. El primero se reporta como un error de servidor temporal —reintentar más tarde tiene sentido—, el segundo como "no autorizado". Confundirlos haría que un corte momentáneo de Google se mostrase como "tu sesión es inválida", empujando al usuario a acciones inútiles.

### 1.3 Usuario nuevo vs. usuario que vuelve

La app identifica a cada persona por su **identificador estable de Google**, no por su email:

- **Usuario nuevo**: se crea su ficha (email, nombre y foto de Google).
- **Usuario que vuelve**: se reconoce por el mismo identificador de Google y **conserva su misma identidad interna** y, con ella, todos sus buzones, cuentas y datos. En cada inicio de sesión se **refrescan** su nombre, su email y su foto con los datos actuales de Google, por si los cambió.

Una consecuencia práctica de identificar por el identificador de Google y no por el email: si una persona cambia la dirección de email de su cuenta de Google pero mantiene la misma cuenta, **sigue siendo el mismo usuario** de MailManager y no pierde nada.

#### Ejemplo

> Una usuaria entra por primera vez con `ana@gmail.com`; se crea su perfil. Meses después cambia el nombre visible de su cuenta de Google. La próxima vez que entra en MailManager, su nombre aparece actualizado automáticamente, sin haber tocado nada en la app.

---

## 2. Conectar cuentas de correo (Gmail y Outlook)

Una vez dentro, el usuario gestiona sus cuentas de correo desde la página **"Cuentas conectadas"** de un buzón. Cada cuenta es un Gmail o un Outlook concreto cuyos correos la app va a sincronizar y gestionar.

### 2.1 Modelo multi-cuenta

MailManager está pensado para agrupar **varias cuentas de correo** bajo un mismo buzón y ofrecer una vista unificada de todas ellas. La estructura es jerárquica:

- Un **usuario** (la identidad de Google) posee uno o más **buzones**.
- Cada **buzón** agrupa una o más **cuentas de correo** (cada una Gmail u Outlook).

La página de cuentas conectadas muestra una tarjeta por cada cuenta vinculada —con su color de proveedor, su etiqueta y un adelanto de sus correos más recientes— más una tarjeta especial **"Añadir cuenta"**.

Solo se admiten dos proveedores: **Gmail** y **Outlook**. No hay forma de elegir otro: el desplegable de proveedor ofrece exactamente esos dos, y el servidor además lo verifica (cualquier otro valor se rechaza). El detalle de cuántas cuentas caben y qué otros proveedores **no** se soportan está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

### 2.2 Añadir una cuenta: registro + autorización en una ventana emergente

Vincular una cuenta no es una sola acción, sino dos encadenadas que la app ejecuta seguidas cuando el usuario pulsa "Añadir cuenta":

1. **Registrar la cuenta**: se crea la ficha de la cuenta en la app (proveedor + etiqueta). En este punto la cuenta existe pero **todavía no está conectada**: no tiene credenciales y no puede leer correo.
2. **Conectar la cuenta**: la app abre una **ventana emergente** con la pantalla de consentimiento del proveedor (Google o Microsoft). El usuario elige su cuenta y consiente **en esa ventana**; el proveedor redirige de vuelta al servidor de la app, que guarda las credenciales, y la ventana avisa del resultado y se cierra sola si todo fue bien.

Mientras la ventana está abierta, la página de cuentas queda a la espera (el botón muestra "Conectando…"). Al completarse la autorización, aparece la tarjeta de la cuenta nueva y la app **sincroniza automáticamente** sus correos y borradores: la tarjeta pasa de "Sincronizando correos…" a mostrar sus mensajes recientes. Si la sincronización inicial no encuentra correos, la tarjeta queda en "Sin correos todavía".

**Si la conexión no se completa, la cuenta no se queda a medias.** Cuando el flujo falla antes de empezar (p. ej. el servidor no puede preparar la autorización), el usuario cancela el consentimiento, cierra la ventana emergente sin terminar, o se agota el tiempo de espera (la cifra exacta está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)), la app **deshace el registro del paso 1**: la cuenta se elimina y **no aparece ninguna tarjeta vacía**. Se muestra el motivo del error y el usuario puede volver a intentarlo desde el formulario. La razón de este rollback: una cuenta recién registrada que **nunca llegó a conectarse** no sirve para nada — dejarla viva solo acumularía tarjetas muertas. Distinto es el caso de una cuenta que **sí estuvo conectada** y cuyo token muere más tarde: esa **conserva** su tarjeta y se recupera con el botón **"Reconectar cuenta"** (ver § 2.7).

Dos detalles de la ventana emergente que conviene conocer:

- La ventana se abre **en el momento del clic** (en blanco) y navega al proveedor en cuanto el servidor devuelve la URL de autorización. Si el navegador bloquea las ventanas emergentes para el sitio, la app lo detecta, avisa pidiendo permitirlas, y **no llega a registrar nada**.
- Si la ventana se cierra sin haber comunicado el resultado, la app hace una comprobación final contra el servidor antes de dar el intento por fallido: si la conexión en realidad llegó a completarse (carrera entre el cierre y el aviso), la cuenta se da por conectada y no se deshace nada.

### 2.3 La etiqueta de la cuenta (display_label)

Cada cuenta lleva una **etiqueta visible** ("nombre personalizado"). En el formulario de alta es **opcional**: si el usuario lo deja en blanco, la app usa automáticamente el nombre del proveedor ("Gmail" u "Outlook") como etiqueta. Es decir, de cara al usuario el campo es opcional, pero **internamente toda cuenta acaba teniendo una etiqueta no vacía** — nunca se guarda una cuenta sin nombre.

La etiqueta tiene un tope de longitud (ver [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)) y se puede cambiar después.

#### Cómo se muestra el encabezado de la tarjeta

El encabezado de cada tarjeta de cuenta combina la etiqueta con el email de la cuenta de forma inteligente:

- Si el usuario puso una **etiqueta personalizada** y ya se conoce el email → muestra `etiqueta - email` (p. ej. `Trabajo - ana@empresa.com`).
- Si la etiqueta es **genérica** (la del proveedor, porque el usuario no personalizó nada) → muestra solo el **email**, que aporta más información que repetir "Gmail".
- Si todavía **no se conoce el email** (ver 2.4) → muestra la etiqueta.

Así, una cuenta sin personalizar pero ya conectada se identifica por su dirección real, que es lo que de verdad distingue una cuenta de otra.

### 2.4 El email de la cuenta es "best-effort"

Durante la conexión, la app intenta averiguar **cuál es la dirección de correo** de la cuenta recién conectada preguntándoselo al proveedor (a Google o a Microsoft). Si lo consigue, lo guarda y lo usa para etiquetar la cuenta; si la consulta falla por cualquier motivo, **la conexión NO se cae por ello**: la cuenta queda conectada y operativa, simplemente sin email conocido (aparecerá como `null`).

Esto es una decisión consciente: el email es un dato *cosmético* para ayudar a identificar la cuenta; no es imprescindible para leer correo. Bloquear la conexión entera porque una consulta secundaria falló sería desproporcionado.

Otro matiz: una vez guardado, el email **no se borra** si una reconexión posterior no logra recuperarlo. Si la primera conexión obtuvo `ana@empresa.com` y una reconexión más tarde no consigue leer el email, la app **conserva** el valor previo en lugar de machacarlo con un vacío. Nunca se pierde un dato bueno por un fallo transitorio.

### 2.5 Permisos que pide cada proveedor (asimetría Gmail vs Outlook)

Las dos autorizaciones piden conjuntos de permisos distintos porque cada API funciona distinto:

- **Gmail** pide un **único permiso amplio** de modificación de correo, que cubre de una vez leer, enviar, gestionar borradores y mover mensajes entre carpetas/etiquetas. Con ese solo permiso la app puede hacer todo lo que necesita.
- **Outlook** pide **varios permisos separados**: leer y escribir correo, **enviar** correo (es un permiso aparte del de lectura/escritura), leer el perfil básico del usuario (para descubrir el email de la cuenta) y un permiso de **acceso prolongado** que permite a la app refrescar la sesión sin volver a molestar al usuario.

La asimetría tiene una consecuencia operativa importante en Outlook: como **enviar** es un permiso distinto de **leer/escribir**, una cuenta de Outlook a la que se le haya recortado el permiso de envío podrá crear y guardar borradores pero fallará al enviarlos —y el fallo aparece en un momento distinto (al enviar, no al conectar)—. En Gmail esto no pasa porque el permiso es uno solo e indivisible. El listado exacto de permisos está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

### 2.6 Dónde quedan las credenciales

Las credenciales que devuelve el proveedor (los tokens que permiten a la app actuar en nombre del usuario) se guardan **cifradas en reposo** en la base de datos. No viajan al navegador ni se exponen en ninguna respuesta de la API: el frontend nunca ve un token de proveedor. La app las usa internamente y las **refresca de forma silenciosa** cuando caducan, sin que el usuario tenga que reconectar (salvo que el proveedor revoque el acceso). Las garantías de cifrado se detallan en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

### 2.7 Reconectar una cuenta cuyo acceso ha caducado o sido revocado

Una cuenta ya conectada puede dejar de funcionar sin que el usuario haga nada: si **revoca el acceso** desde el proveedor, o —caso típico durante las pruebas— el token de Gmail **caduca** en modo *Testing* (ver [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)). A partir de ahí la sincronización falla y la cuenta no trae correo nuevo.

Para recuperarla sin perder nada, el menú (⋮) de cada tarjeta ofrece **"Reconectar cuenta"**. Es el **mismo** consentimiento OAuth en ventana emergente que el alta (sección 2.2), pero sobre la cuenta que **ya existe**: al completarse, la app **sobrescribe** las credenciales y vuelve a sincronizar. La cuenta, su etiqueta y sus correos ya sincronizados **se conservan** — reconectar no es borrar y volver a crear.

Diferencias con el alta que conviene conocer:

- **No pide confirmación** (no es una acción destructiva) y está **siempre disponible** en el menú: reconectar una cuenta que en realidad seguía sana es inofensivo, simplemente renueva el acceso.
- **Si la reconexión no se completa, la cuenta NO se elimina** (al contrario que el rollback del alta inicial, sección 2.2): la tarjeta sigue ahí y el usuario puede reintentar.
- Funciona igual para **Gmail y Outlook** (es el mismo flujo para ambos proveedores).

> No hay reconexión *automática*: la app refresca el token de forma silenciosa mientras el proveedor lo permita (sección 2.6), pero un acceso revocado o caducado solo se recupera con esta acción **manual**.

---

## 3. Editar y desconectar cuentas

### 3.1 Cambiar la etiqueta

La etiqueta visible de una cuenta se puede modificar después de crearla. Es un cambio puramente local (no toca al proveedor ni a las credenciales) e instantáneo.

### 3.2 Desconectar (eliminar) una cuenta

Desde el menú de cada tarjeta, "Eliminar cuenta" pide confirmación —avisando de que **se eliminarán los correos y borradores asociados a esa cuenta**— y, al confirmar, desvincula la cuenta del buzón.

Eliminar una cuenta **arrastra consigo todo lo que dependía de ella**: sus credenciales, los correos sincronizados, los borradores y los adjuntos de esos borradores. Es una limpieza en cascada: no quedan restos huérfanos en la base de datos de la app. Lo que **no** hace es tocar nada en el proveedor: los correos siguen intactos en Gmail/Outlook; simplemente MailManager deja de gestionarlos.

> Importante: desconectar una cuenta de correo **no** cierra la sesión de la app ni borra al usuario. Son planos distintos. El usuario sigue dentro de MailManager con sus demás cuentas.

---

## 4. La sesión de la aplicación: cómo vive y cómo termina

### 4.1 La sesión vive en una cookie

Tras iniciar sesión, la identidad del usuario viaja en una **cookie de sesión** que el navegador envía automáticamente en cada petición. Características relevantes para el usuario y para el equipo:

- Es **inaccesible desde JavaScript** de la página (cookie `HttpOnly`), de modo que un fallo de tipo XSS no puede robar la sesión leyendo la cookie.
- El identificador de sesión es **opaco**: no contiene datos del usuario, solo una referencia que el servidor resuelve contra su almacén de sesiones.
- Tiene una **caducidad fija** desde el momento del login (el plazo exacto está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)).

La consecuencia visible: **recargar la página o cerrar y reabrir el navegador mantiene la sesión** mientras no haya caducado. La app, al arrancar, comprueba si hay una sesión válida y, si la hay, entra directamente sin pedir login.

### 4.2 La caducidad es fija, no se renueva con el uso

La sesión caduca a un plazo fijo **contado desde el login**, y ese plazo **no se prolonga** por seguir usando la app. Es decir, no hay "renovación por actividad": al cumplirse el plazo, la sesión deja de ser válida aunque el usuario haya estado activo hasta hace un segundo, y la próxima petición lo devolverá al login. Es una decisión de simplicidad del MVP; el valor del plazo está pensado para que en la práctica no resulte molesto.

Cuando una sesión caduca o deja de ser válida, las pantallas protegidas redirigen al login. El usuario vuelve a entrar con Google y sigue donde estaba (sus datos no se han tocado).

### 4.3 Cerrar sesión

"Cerrar sesión" elimina la sesión del servidor y borra la cookie. A partir de ahí, las pantallas protegidas redirigen al login. El cierre de sesión **funciona aunque la sesión ya estuviera caducada o ausente** —no falla por intentar cerrar algo que ya no existía—, lo cual evita que un usuario se quede "atrapado" sin poder pulsar logout.

### 4.4 Borrar la cuenta de usuario (DELETE /auth/me)

Distinta de cerrar sesión, existe la opción de **borrar la cuenta de usuario por completo**. Esto elimina la ficha del usuario y, en cascada, **absolutamente todo lo que le pertenece**: sus buzones, todas sus cuentas de correo conectadas, las credenciales de esas cuentas, sus correos sincronizados, borradores, adjuntos y todas sus sesiones. Tras el borrado, la cookie se limpia y el usuario queda fuera.

Es una operación **irreversible** y de gran alcance: no es "desconectar una cuenta" (sección 3.2), es eliminar al usuario entero de MailManager. Lo único que no toca son los correos en los proveedores: Gmail y Outlook conservan los mensajes; lo que desaparece es todo lo que MailManager tenía guardado de esa persona.

> Asimetría con el login: **no se puede crear un usuario sin pasar por el login interactivo de Google**. No hay un endpoint de "registro" separado: el primer login con Google es el que crea al usuario. Por eso borrar la cuenta y querer volver implica, necesariamente, un nuevo login con Google (que recreará un usuario nuevo, sin los datos anteriores).

---

## 5. Acceso de desarrollo (dev login)

Para poder trabajar en local sin pasar cada vez por el consentimiento de Google, existe un **atajo de desarrollo**: un botón "Dev login" que aparece **solo cuando la app corre en modo desarrollo**. En una build de producción ese botón no existe.

El atajo está fuertemente acotado por el lado del servidor con varias barreras encadenadas (debe estar explícitamente habilitado por configuración, la petición debe venir de la propia máquina local, y debe haber un email de desarrollo configurado). Si alguna barrera no se cumple, el atajo responde con un error distinto según el motivo, de forma que quede claro si es que "aquí no está habilitado" o que "la petición no viene de localhost". Los detalles exactos de cada barrera y sus respuestas están en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

Una característica clave: el dev login **solo puede iniciar sesión como un usuario que YA existe** (lo busca por su email). **No crea usuarios**. Sigue siendo cierto que la única vía para crear un usuario es el login interactivo de Google.

---

## 6. Resumen del modelo de errores (lo que el usuario percibe)

Sin entrar en códigos, conviene tener clara la intención detrás de los errores más habituales de esta área, porque están elegidos para que el usuario o el equipo entiendan qué hacer:

- **Credenciales de login inválidas** → "no autorizado": hay que volver a intentar el login.
- **Google inalcanzable al verificar** → error temporal de servidor: reintentar más tarde, la credencial podría estar bien.
- **Fallo de autorización al conectar una cuenta** → se trata como "credenciales incorrectas" (no como "vuelve a conectar"), para no meter al usuario en un bucle de reintentos sobre el mismo paso.
- **Acceder a un buzón que no es tuyo** → se distingue entre "no existe" y "no tienes acceso", de forma que la app nunca confirme la existencia de buzones ajenos a quien no debe.
- **Operar sobre un usuario o cuenta que ya no existe** → "no encontrado", nunca un éxito silencioso.

La correspondencia exacta de cada situación con su código y estado HTTP está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

---

## 7. Resumen en una frase

> MailManager separa dos autorizaciones: el **login con Google** —que solo identifica al usuario de la app, crea una sesión en una cookie `HttpOnly` de caducidad fija y es la única vía para dar de alta un usuario— y la **conexión de cuentas de correo** Gmail u Outlook bajo un buzón, que registra la cuenta y lanza la autorización OAuth en una **ventana emergente** cuyo callback recibe el propio servidor; si la autorización no se completa, el registro **se deshace** y no queda ninguna tarjeta vacía, y si se completa la app guarda credenciales cifradas, descubre el email de la cuenta de forma best-effort (sin caerse si falla) y sincroniza los correos; cada proveedor pide permisos distintos (Gmail uno solo y amplio, Outlook varios separados, con el envío como permiso aparte), borrar una cuenta o el usuario entero limpia en cascada todo lo asociado sin tocar nada en el proveedor, y los topes y todo lo que deliberadamente no soporta viven en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).
