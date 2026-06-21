# Autenticación y gestión de cuentas — comportamiento (MVP)

Este documento describe **qué hace** MailManager cuando alguien inicia sesión y cuando vincula o desvincula cuentas de correo, y **qué experimenta** delante de la app. No entra en cómo está cableado el código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se comporta la funcionalidad sin tener que leer la implementación.

Hay dos conceptos que conviene no mezclar y que este documento trata en este orden:

1. **La sesión de la aplicación** — quién está usando MailManager. Se obtiene iniciando sesión con **Google o con Microsoft** y vive en una cookie. Es la identidad del *usuario de la app*.
2. **Las cuentas de correo conectadas** — los buzones de Gmail y Outlook cuyos correos la app va a leer y gestionar. Son recursos que el usuario vincula *después* de iniciar sesión.

Iniciar sesión (sea con Google o con Microsoft) **no** conecta ninguna cuenta de correo: solo identifica a la persona. Son autorizaciones distintas, con proveedores y permisos distintos, y ocurren en momentos distintos. En particular, **entrar con Microsoft no conecta ningún buzón de Outlook**: una persona puede entrar con su cuenta Microsoft y luego conectar (o no) buzones de Gmail y/o de Outlook, que es otra cosa (sección 2).

La frontera de este documento llega hasta el **ciclo de vida de la cuenta y de la sesión**. Cómo se agrupan las cuentas bajo un buzón y cómo se presenta la vista unificada se documenta en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md). Los topes, valores por defecto y la lista de "lo que NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)**.

---

## 1. Iniciar sesión (Google o Microsoft)

La pantalla de login ofrece **dos métodos** para entrar, uno al lado del otro: **"Continuar con Google"** y **"Continuar con Microsoft"**. Cualquiera de los dos identifica a la persona y abre **la misma sesión** (la cookie, su duración y todo el comportamiento posterior son idénticos, sección 4); lo único que cambia es por dónde se verifica la identidad. Microsoft admite tanto cuentas **personales** (Outlook.com, Hotmail, Live) como **de organización** (trabajo o estudio).

### 1.1 El flujo con Google

La pantalla muestra el botón oficial de **"Continuar con Google"** (el widget de Google Identity Services). Al pulsarlo, Google gestiona toda la interacción de consentimiento y devuelve a la app una credencial firmada que la identifica. La app la verifica contra el servidor, crea una sesión y, si todo va bien, redirige a la aplicación.

A nivel de experiencia:

- Mientras se verifica la credencial, el botón muestra un breve "Iniciando sesión…".
- Si la verificación falla, aparece un mensaje de error bajo el botón y el usuario se queda en el login.
- Si va bien, entra directamente en la app. La próxima vez que abra MailManager, **seguirá dentro** sin volver a pasar por el login mientras la sesión siga viva (sección 4).

> El login es contra **Google**, sea cual sea el correo del usuario. Es la identidad de la *app*, no el primer correo conectado. Alguien puede entrar con su Google personal y luego conectar cuentas de Outlook del trabajo; son cosas separadas.

### 1.2 El flujo con Microsoft

Al pulsar **"Continuar con Microsoft"**, la app abre una **ventana emergente** de Microsoft donde el usuario elige su cuenta y da su consentimiento. Tras consentir, la ventana se cierra, la app recibe la credencial de identidad, el servidor la verifica y, si todo va bien, el usuario entra directamente en la aplicación. De cara al usuario la experiencia es equivalente a la de Google: mientras se verifica, el botón muestra un breve "Iniciando sesión…"; si algo falla, aparece un mensaje de error bajo los botones y el usuario se queda en el login.

Una diferencia de mecánica que conviene conocer (aunque para el usuario el resultado sea el mismo): el consentimiento de Microsoft ocurre en una **ventana emergente** —igual que el popup de *conectar* una cuenta de Outlook (sección 2.2), pero aquí es para **entrar en la app**, no para vincular un buzón—. Los permisos que pide este popup son solo los de **identidad** (saber quién eres), no permisos de correo; la lista exacta está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

> Reintentar tras cancelar: por una limitación de la librería de Microsoft que usa el frontend, detectar que el usuario **cerró la ventana** sin terminar no es instantáneo. La app lo gestiona para que el cierre se interprete como una **cancelación suave** ("Inicio de sesión cancelado.") y el usuario pueda volver a pulsar el botón sin esperas largas. El detalle técnico vive en el documento de frontend.

### 1.3 Qué valida el servidor antes de dar por buena la sesión

No basta con que el proveedor diga "esta credencial es mía". En **ambos** proveedores el servidor exige que la credencial sea **criptográficamente válida** y esté **emitida para esta aplicación** concreta (se comprueba el destinatario del token): una credencial generada para otra app distinta se rechaza aunque sea legítima. Más allá de eso, cada proveedor tiene su política de identidad:

- **Google.** La credencial debe traer un **identificador estable de Google** y un **email**, y ese email debe estar **verificado** (la credencial trae una marca `email_verified`). Una cuenta de Google cuyo email primario no está verificado se rechaza: la identidad se ancla al identificador de Google, pero un email sin verificar no es fiable para mostrarlo ni para futuras notificaciones.
- **Microsoft.** La credencial debe traer un **identificador estable de Microsoft**. Microsoft **no** entrega una marca de "email verificado", así que el login con Microsoft **no la exige** (sección 6). El email es más laxo que en Google: si Microsoft no entrega un email "limpio", la app cae al **nombre de usuario principal** y no se bloquea (sección 6). Solo si faltaran a la vez el identificador o ambos campos de email se rechazaría el login.

Se tolera un pequeño desfase de reloj entre el cliente y el servidor para que un reloj ligeramente adelantado o atrasado no tumbe logins legítimos (los márgenes —que difieren entre Google y Microsoft— están en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md)).

La distinción de errores es deliberada y **la misma para los dos proveedores**: un **fallo de red** al contactar con el proveedor para verificar (el servicio de verificación está caído o inalcanzable) **no** es lo mismo que una credencial inválida. El primero se reporta como un error de servidor temporal —reintentar más tarde tiene sentido—, el segundo como "no autorizado". Confundirlos haría que un corte momentáneo del proveedor se mostrase como "tu sesión es inválida", empujando al usuario a acciones inútiles.

### 1.4 Usuario nuevo vs. usuario que vuelve

La app identifica a cada persona por el **identificador estable que entrega su proveedor** (el de Google o el de Microsoft), no por su email:

- **Usuario nuevo**: se crea su ficha con su email y su nombre (y, en Google, su foto). En Microsoft el email puede ser el nombre de usuario si la cuenta no expone un email "limpio" (sección 6).
- **Usuario que vuelve**: se reconoce por el mismo identificador del mismo proveedor y **conserva su misma identidad interna** y, con ella, todos sus buzones, cuentas y datos. En cada inicio de sesión se **refrescan** su nombre y su email (y la foto en Google) con los datos actuales del proveedor, por si los cambió.

Una consecuencia práctica de identificar por el identificador del proveedor y no por el email: si una persona cambia la dirección de email de su cuenta pero mantiene la misma cuenta del proveedor, **sigue siendo el mismo usuario** de MailManager y no pierde nada.

El identificador es **propio de cada proveedor**: el mismo usuario que entra unas veces con Google y otras con Microsoft se trata como **dos usuarios distintos** aunque comparta email. Esto es deliberado y se explica en la sección 5 ("no hay enlace de cuentas").

#### Ejemplo

> Una usuaria entra por primera vez con su cuenta de Google `ana@gmail.com`; se crea su perfil. Meses después cambia el nombre visible de su cuenta de Google. La próxima vez que entra en MailManager, su nombre aparece actualizado automáticamente, sin haber tocado nada en la app. Si esa misma persona entrara otro día con **Continuar con Microsoft**, MailManager crearía un **usuario aparte**, sin los buzones ni los datos del usuario de Google.

### 1.5 Sin enlace de cuentas: cada método identifica a su propio usuario

El login con Google y el login con Microsoft son **independientes**. Si una misma persona usa el **mismo email** en su cuenta de Google y en su cuenta de Microsoft y entra unas veces con una y otras con otra, MailManager la tratará como **dos usuarios distintos**, cada uno con sus propios buzones, cuentas y datos. **No se fusionan.**

Es una limitación consciente del MVP: vincular ("enlazar") la cuenta de Google y la de Microsoft de una misma persona bajo un único usuario queda fuera de alcance. La identidad interna se ancla al par *(proveedor, identificador del proveedor)*, no al email, de modo que el email coincidente no basta para unirlas. Quien quiera conservar un único conjunto de buzones y datos debe **entrar siempre con el mismo método**.

### 1.6 El email de Microsoft puede no venir (asimetría con Google)

Con Google la app exige siempre un email **verificado** (sección 1.3). **Microsoft funciona distinto** y el login lo asume de forma explícita:

- Microsoft **no** entrega la marca de "email verificado", así que el login con Microsoft **no la exige**.
- En algunas cuentas —sobre todo de organización— Microsoft puede **no entregar el email**. En ese caso la app usa el **nombre de usuario principal** que sí entrega Microsoft (que muy a menudo es el propio email) y **el login NO se bloquea**. Solo si Microsoft no entregara ni email ni nombre de usuario —caso muy raro— se rechazaría el login.

Para el usuario el efecto es: entrar con Microsoft funciona aunque su cuenta no exponga un email "limpio"; como mucho, el email mostrado en su perfil será su nombre de usuario de Microsoft. (Esto es distinto del email *de una cuenta de correo conectada*, que es best-effort por otra razón — sección 2.4.)

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

## 6. Protección frente a abuso (límite de frecuencia)

Para poder abrir la app a usuarios de prueba desconocidos sin que nadie pueda abusar de ella, MailManager aplica un **límite de frecuencia de peticiones por cliente** sobre las operaciones más sensibles. Es una medida de **endurecimiento** previa al despliegue y, para la inmensa mayoría de usuarios, es **invisible**.

### 6.1 Qué ve el usuario

- **En uso normal, nada.** Los topes están calibrados muy por encima de lo que hace una persona de verdad usando la app a mano; un usuario corriente no los toca nunca.
- **Si se superan**, la operación afectada se rechaza y el usuario ve un mensaje claro del tipo **"Demasiadas peticiones. Espera N segundos e inténtalo de nuevo."** (la app rellena los segundos concretos cuando el servidor los indica). Pasados unos segundos vuelve a funcionar con normalidad: **no se pierde nada ni se rompe la sesión**.
- **No cambia ningún flujo, pantalla ni paso** de la aplicación. No hay nada nuevo que el usuario tenga que hacer.

Además, la app **no reintenta automáticamente** una petición rechazada por este motivo: un reintento solo consumiría el límite más deprisa y alejaría el momento de recuperación.

### 6.2 Qué operaciones tienen tope (y cuáles no)

Tienen un tope pensado para frenar el abuso sin estorbar el uso legítimo:

- **Iniciar sesión** — el login con Google, el login con Microsoft y el dev login (sección 5) comparten el mismo tope.
- **Enviar correo** — el envío directo y el envío de un borrador.
- **Sincronizar con el proveedor** — sincronizar correos, borradores y favoritos.

Por encima de todo eso, **toda la aplicación** tiene un **tope global por cliente** muy holgado como red de seguridad, para que nadie pueda saturar el servidor (que en esta fase corre como un solo proceso). El resto de acciones cotidianas (abrir un correo, navegar por las bandejas, marcar como leído, mover a papelera, descargar adjuntos…) funcionan igual que siempre, solo bajo ese tope global. Quedan **fuera** de todo límite la sonda de salud y los retornos del consentimiento OAuth, para no romper ni la monitorización ni una conexión de cuenta legítima.

> Las cifras exactas de cada tope —cuántas peticiones por minuto/hora y a qué se aplican—, las rutas exentas y la semántica de la espera están en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md) § 5.

### 6.3 Cómo se identifica al "cliente"

El "cliente" al que se le cuenta el límite cambia según la operación, y la elección tiene su lógica:

- En el **login** se cuenta **por dirección de origen (IP)**: todavía no hay un usuario identificado, así que esto es lo que frena el martilleo desde un mismo origen.
- En las operaciones donde **ya hay sesión** (enviar, sincronizar) se cuenta **por usuario**, que es la unidad real de consumo de la cuota del proveedor.
- El **tope global** de seguridad se cuenta **por IP**.

### 6.4 Por qué se añade y por qué es opt-in

El motivo de fondo es doble: frenar la **fuerza bruta del login** y —lo más importante— evitar el **abuso de la cuota compartida de los proveedores**. La app usa credenciales de Gmail/Outlook **compartidas**; un usuario abusivo que dispare miles de envíos o sincronizaciones podría agotar esa cuota y dejar la app inservible para todos, o provocar que el proveedor suspenda las credenciales.

La protección se **activa en producción** y está **desactivada por defecto en desarrollo y en los tests**, para no estorbar el trabajo diario ni alterar las pruebas automáticas. Es una protección **básica y suficiente para el MVP** (un solo servidor): no pretende ser un sistema de cuotas sofisticado, sino la protección mínima sensata antes de exponer la app a usuarios desconocidos. Una limitación consciente de esta simplicidad —el conteo no se comparte si algún día hubiera varios procesos de servidor— está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md) § 7.

---

## 7. Resumen del modelo de errores (lo que el usuario percibe)

Sin entrar en códigos, conviene tener clara la intención detrás de los errores más habituales de esta área, porque están elegidos para que el usuario o el equipo entiendan qué hacer:

- **Credenciales de login inválidas** (Google o Microsoft) → "no autorizado": hay que volver a intentar el login.
- **Proveedor de login inalcanzable al verificar** (Google o Microsoft) → error temporal de servidor: reintentar más tarde, la credencial podría estar bien.
- **Login con Microsoft no configurado en este despliegue** → error de configuración del servidor: el botón existe pero el operador no terminó de configurar Microsoft; no es culpa del usuario.
- **Fallo de autorización al conectar una cuenta** → se trata como "credenciales incorrectas" (no como "vuelve a conectar"), para no meter al usuario en un bucle de reintentos sobre el mismo paso.
- **Acceder a un buzón que no es tuyo** → se distingue entre "no existe" y "no tienes acceso", de forma que la app nunca confirme la existencia de buzones ajenos a quien no debe.
- **Operar sobre un usuario o cuenta que ya no existe** → "no encontrado", nunca un éxito silencioso.
- **Superar el límite de frecuencia** (sección 6) → "demasiadas peticiones": esperar unos segundos y reintentar; la sesión y los datos quedan intactos.

La correspondencia exacta de cada situación con su código y estado HTTP está en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).

---

## 8. Resumen en una frase

> MailManager separa dos autorizaciones: el **login de la app** —con **Google o con Microsoft** (cuenta personal o de organización), que solo identifica al usuario, crea una sesión en una cookie `HttpOnly` de caducidad fija y es la única vía para dar de alta un usuario; cada método identifica a su **propio** usuario sin enlace de cuentas, y como Microsoft no garantiza un email verificado el login lo tolera (cae al nombre de usuario si falta el email) sin bloquearse— y la **conexión de cuentas de correo** Gmail u Outlook bajo un buzón, que registra la cuenta y lanza la autorización OAuth en una **ventana emergente** cuyo callback recibe el propio servidor; si la autorización no se completa, el registro **se deshace** y no queda ninguna tarjeta vacía, y si se completa la app guarda credenciales cifradas, descubre el email de la cuenta de forma best-effort (sin caerse si falla) y sincroniza los correos; cada proveedor pide permisos distintos (Gmail uno solo y amplio, Outlook varios separados, con el envío como permiso aparte), borrar una cuenta o el usuario entero limpia en cascada todo lo asociado sin tocar nada en el proveedor; como endurecimiento previo al despliegue, un **límite de frecuencia opt-in (apagado por defecto)** protege el login, el envío y las sincronizaciones —más una red global por IP— mostrando un aviso de "demasiadas peticiones" cuando alguien se pasa, invisible en uso normal; y los topes y todo lo que deliberadamente no soporta viven en [../limits/autenticacion-y-cuentas.md](../limits/autenticacion-y-cuentas.md).
