# Características de adjuntos — comportamiento (MVP)

Este documento describe **qué hace** la app respecto a los archivos adjuntos: cómo se reciben, cómo se ven, cómo se añaden a un borrador, cómo se envían y qué pasa cuando algo falla. No entra en código: es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la app.

Recoge los comportamientos derivados de las decisiones D-01 a D-30 del documento `externalAPIinformation/decisiones-adjuntos-app.md`.

---

## 1. Qué soporta MailManager respecto a adjuntos

La app trata dos tipos de "archivo dentro de un correo":

- **Imágenes embebidas en el cuerpo HTML** (referenciadas con `cid:` desde un `<img>`). Ya estaban soportadas antes de esta feature: el correo se ve correctamente con su logo, su firma con foto, etc. La lógica de imágenes embebidas **no cambia**.
- **Archivos adjuntos descargables** (PDFs, documentos de Word/Excel, imágenes sueltas, ZIPs, etc.). Esto es la novedad.

La app cubre tres flujos completos:

1. **Recibir** correos con adjuntos y poder verlos / descargarlos.
2. **Componer** correos nuevos con adjuntos.
3. **Editar borradores** añadiendo, quitando y conservando adjuntos entre sesiones.

Funciona igual para cuentas Gmail y para cuentas Outlook, salvo en los puntos donde un proveedor impone un comportamiento distinto (los puntos exactos están marcados a lo largo del documento).

---

## 2. Límites de tamaño y cantidad

La app aplica **tres límites** a la hora de enviar (no a la hora de recibir):

### 2.1 Tamaño máximo por archivo individual: **25 MB**

El usuario no puede adjuntar un único archivo de más de 25 MB. Si lo intenta, la app rechaza el archivo en cuanto el usuario lo selecciona o lo arrastra al composer, sin esperar a llegar al servidor del proveedor.

Es el límite de Gmail estándar y suficientemente cómodo para casi cualquier escenario realista (PDFs, presentaciones, fotos, documentos ofimáticos, vídeos cortos).

### 2.2 Tamaño máximo total del mensaje: **25 MB**

El cuerpo del correo + todos los adjuntos juntos no pueden superar **25 MB**, sin distinción entre Gmail y Outlook. Es un límite uniforme.

El composer muestra siempre "X / 25 MB usados", y el contador no cambia al cambiar la cuenta de origen seleccionada.

**Por qué uniforme y no asimétrico**: Outlook permite por defecto hasta 35 MB y configurable hasta 150 MB. Mantener un techo distinto por proveedor introducía complejidad real (contador que cambia con la cuenta, casos del cambio de cuenta con exceso de adjuntos cargados, dos números en pantalla) a cambio de 10 MB extra que solo aprovechan correos cercanos al techo. Para casos por encima de 25 MB la respuesta correcta sigue siendo Drive/OneDrive — capacidad que la app tampoco implementa con 35 MB.

**Caveat aceptado**: si el tenant Outlook está configurado con un límite **inferior a 25 MB** (raro: el default histórico es 35 MB), la app no lo detecta y los correos pueden rebotar con NDR. Si está configurado por encima (50 MB, 150 MB), la app limita falsamente — pero esos clientes tienen Drive/OneDrive disponible para los casos extremos.

### 2.3 Número máximo de adjuntos por correo: **25**

Aunque la suma de tamaños permita más, **un correo no puede llevar más de 25 archivos adjuntos**. Es un límite duro y la app rechaza el archivo número 26 con un error explícito en el composer.

Es un techo cómodo para casos realistas y evita sorpresas en la UI cuando alguien arrastra una carpeta entera de 200 ficheros.

---

## 3. Qué archivos se pueden adjuntar al enviar

La app aplica una **blocklist** (lista de bloqueo) que **combina** las dos listas oficiales — la de Gmail y la de Microsoft Exchange Online. El mismo archivo se rechaza independientemente de la cuenta de origen.

### Por qué la lista es combinada y no una por proveedor

Las dos listas oficiales **no coinciden**. Outlook bloquea una superlista que incluye casi todo lo de Gmail más extensiones extra (`.py`, `.pyc`, `.mht`, `.mhtml`, `.mdb`, `.mde`, `.csh`, `.ksh`, `.url`, `.theme`, `.scf`, etc.). Si la app respetase la lista de cada proveedor por separado, el mismo `.py` se enviaría desde una cuenta Gmail y se rechazaría desde una Outlook — UX inconsistente y confusa para el usuario.

Aplicando la **unión** se garantiza que cualquier archivo que pase la validación del cliente llegará al destinatario sin que ningún proveedor lo bloquee. La regla es la más estricta de las dos, lo cual es la postura segura.

La lista combinada incluye, entre otras: ejecutables y scripts (`.exe`, `.bat`, `.cmd`, `.com`, `.scr`, `.vbs`, `.vbe`, `.js`, `.jse`, `.wsf`, `.wsh`, `.msi`, `.dll`, `.pif`, `.jar`, `.lnk`, `.reg`), bibliotecas (`.appx`, `.appxbundle`, `.cab`, `.dmg`), shells y scripts de PowerShell/Python/Perl (`.ps1`, `.ps1xml`, `.py`, `.pyc`, `.pl`, `.csh`, `.ksh`), archivos de Office Access (`.mdb`, `.mde`, `.mdt`, `.mdw`), formatos de Outlook (`.pst`, `.mht`, `.mhtml`), instaladores e imágenes de disco (`.msi`, `.msu`, `.iso`, `.img`, `.vhd`, `.vhdx`), atajos del sistema y configuraciones (`.url`, `.lnk`, `.scf`, `.settingcontent-ms`, `.theme`), y un largo etcétera (la lista exacta y autoritativa vive en una constante centralizada del código).

### Cuándo se valida

La validación ocurre **en el cliente, antes de subir el archivo** al backend. El usuario que arrastra `virus.exe` recibe un mensaje inmediato ("Este tipo de archivo no se puede enviar por correo") sin que el archivo viaje por la red. El backend vuelve a validar como segunda red de seguridad.

### Por qué la validación es en cliente y no solo en servidor

Hay una razón concreta para los dos proveedores:

- **Gmail rechaza síncronamente**: si la app sube el `.exe` igualmente, Gmail responde con `400 badRequest "The attachment is invalid"`.
- **Outlook NO rechaza síncronamente**: el endpoint `sendMail` devuelve `202 Accepted` aunque el adjunto sea de un tipo bloqueado. El usuario cree que el correo se envió, pero minutos más tarde recibe un NDR (mensaje de error diferido) en su bandeja de entrada.

La única forma de dar al usuario un error inmediato y consistente entre Gmail y Outlook es validar en el cliente. La validación del backend existe igualmente como segunda red de seguridad.

### Lo que NO hace la app por ahora

- No analiza los archivos buscando virus.
- No verifica que el contenido del fichero corresponde a su extensión (un `.pdf` que en realidad sea un ejecutable renombrado pasaría el filtro). La protección frente a malware queda fuera del MVP.

---

## 4. Qué archivos se pueden descargar al recibir

**Todos los adjuntos que lleguen** se muestran en la app y se pueden descargar, sin filtrado adicional. La app no bloquea ni esconde tipos de archivo "peligrosos" en correos recibidos.

Es la postura que adopta Gmail web: si el correo trajo un `.exe` adjunto, el usuario lo ve y decide qué hacer con él. Filtrar la recepción introduciría falsos negativos (esconder cosas legítimas que el usuario necesita) y falsos positivos (mostrar cosas que el usuario debería ignorar) sin valor real.

La protección frente a contenido malicioso queda **delegada al sistema operativo y al navegador** del usuario en el momento en que abre el archivo descargado.

---

## 5. Cómo se reciben y se ven los adjuntos

El proceso ocurre en **tres momentos** distintos, y solo el último gasta cuota del proveedor.

### Momento 1: el usuario sincroniza la bandeja (`sync_emails`)

La app baja únicamente la **metadata de los correos** (asunto, remitente, fecha, y el flag `tiene adjuntos`). **No descarga ningún adjunto** ni siquiera la lista de adjuntos de cada correo. Es lo que ya hacía antes de esta feature.

A nivel visual, la lista del buzón muestra el icono de **clip 📎** al lado del asunto cuando el correo tiene al menos un adjunto descargable. Si el correo solo tiene imágenes inline (logos del newsletter, firmas con foto), NO se muestra clip — coincide con cómo lo hacen Gmail y Outlook web. Esa distinción es importante: si todos los newsletters mostrasen clip, el icono perdería valor.

### Momento 2: el usuario abre un correo concreto

Cuando el usuario hace clic en un correo, la app descarga el contenido HTML + texto **junto con la lista detallada de adjuntos** (nombre, tipo, tamaño, si es inline o no). Esa lista se persiste localmente para que abrir el mismo correo una segunda vez sea instantáneo.

Lo que **no** se descarga en este momento son **los binarios** de los adjuntos. Solo el "menú": "factura.pdf, 1,2 MB, PDF". El PDF en sí se queda en el proveedor.

Resultado: abrir un correo con 5 adjuntos pesados es igual de rápido que abrir uno sin adjuntos.

#### Cómo se ve la lista de adjuntos en el visor

Debajo del cuerpo del correo, el visor pinta una **lista de tarjetas** — una por adjunto descargable. Cada tarjeta muestra:

- Icono según el tipo (PDF rojo, imagen, Word, Excel, ZIP, genérico).
- Nombre del archivo (truncado si es largo, con el nombre completo en tooltip).
- Tamaño formateado ("1,2 MB", "523 KB").
- Botón de descarga visible (icono flecha hacia abajo).

Click en cualquier sitio de la tarjeta dispara la descarga del adjunto.

### Momento 3: el usuario clica un adjunto concreto

Solo cuando el usuario clica un adjunto específico, la app:

1. Pide ese binario al proveedor (Gmail o Outlook).
2. Lo persiste localmente.
3. Se lo entrega al navegador como descarga al disco.

Mientras llega el binario, la tarjeta del adjunto muestra un spinner pequeño en su botón. Si la primera descarga tarda 1-2 segundos (porque hay que ir al proveedor), el usuario ve esa espera; las siguientes veces es instantáneo.

Si el usuario clica varios adjuntos seguidos, la app procesa **hasta 2 descargas en paralelo** y deja el resto en cola. Las tarjetas en cola muestran "En cola" sin spinner hasta que les llega el turno. Esta limitación evita acercarse a los topes de concurrencia que impone Microsoft Graph (4 peticiones simultáneas por buzón) y mantiene la respuesta del proveedor estable.

A partir de ese momento, ese adjunto está cacheado: si el mismo usuario vuelve a clicar el mismo adjunto otra vez (mañana, la semana que viene), la app lo sirve directamente desde su almacenamiento local **sin volver a llamar al proveedor**.

### Imágenes embebidas en el cuerpo: sin cambios

Las imágenes embebidas que aparecen dentro del HTML del correo (logos, firmas con foto, banners de newsletters, etc.) se siguen resolviendo en el momento 2, exactamente como antes. El usuario no nota ninguna diferencia: el correo se renderiza con sus imágenes, y la lista de "adjuntos descargables" del momento 2 NO incluye las imágenes que ya están embebidas en el HTML.

### Cómo distingue la app entre "imagen inline" y "adjunto descargable"

La regla es estricta: una parte del correo se considera **inline** solo si cumple las **dos** condiciones a la vez:

1. Está marcada como inline por el remitente (cabecera `Content-Disposition: inline` o el equivalente en Outlook).
2. Su identificador (`Content-ID`) aparece referenciado dentro del HTML del cuerpo (vía `cid:`).

Si solo cumple una de las dos, la app la trata como **adjunto descargable**. Esto evita dos problemas habituales con correos mal construidos: que una imagen marcada como inline pero no referenciada en el HTML "se pierda" sin que el usuario la vea, o que un archivo destinado a ser descargable acabe oculto en el cuerpo por un `Content-ID` despistado.

---

## 6. Cómo se gestionan los adjuntos al componer y editar borradores

El comportamiento es distinto del de los recibidos porque aquí el usuario es quien aporta el archivo.

### 6.1 Cómo se ve la zona de adjuntos en el composer

El composer tiene **dos formas de añadir** adjuntos + un **listado visual** + un **contador de capacidad**, y todas están disponibles en los tres flujos en los que se abre el composer: "Nuevo mensaje", "Nuevo borrador" y "Editar borrador". La zona de adjuntos no espera a que el usuario haya guardado nada — está activa desde que el composer aparece con una cuenta de origen ya cargada.

**Punto de entrada**:
- **Botón "Adjuntar"** (icono clip 📎) en la barra inferior del composer, al lado del botón "Enviar". Click abre el selector de archivos del sistema operativo.
- **Drag and drop** directamente sobre el composer. Mientras el usuario arrastra, el composer muestra un overlay con texto "Suelta el archivo para adjuntarlo".

**Listado**: cada adjunto aparece como un **chip compacto** entre el campo "Asunto" y el cuerpo. El chip lleva icono según el tipo, nombre del archivo (truncado a ~20 caracteres), tamaño en pequeño y un botón "X" para quitarlo.

**Contador de capacidad**: en la barra inferior, un contador discreto del tipo "12,4 / 25 MB". El número total es uniforme (D-02) — no cambia al cambiar la cuenta de origen seleccionada. Se pinta en rojo si el siguiente archivo excedería el límite.

### 6.2 Añadir un adjunto

Cuando el usuario arrastra un archivo al composer (o lo selecciona desde el botón), pasan estas cosas en orden:

1. **Validación cliente-side instantánea**: extensión, tamaño individual, tamaño total acumulado, número de adjuntos. Si algo falla, mensaje en rojo con la razón concreta y el archivo NO viaja al backend.
2. **Bootstrap silencioso del borrador (solo la primera vez en "Nuevo mensaje" o "Nuevo borrador")**: si el composer todavía no tiene un borrador asociado en el proveedor, la app crea uno con cuerpo, destinatarios y asunto vacíos para tener un identificador real al que asociar el adjunto. Es invisible para el usuario en la app (no aparece como entrada nueva en su lista local hasta que pulse "Guardar borrador"), pero sí queda creado en Gmail/Outlook web durante la composición. Si la cuenta de origen aún no se ha cargado en ese momento, los archivos arrastrados se encolan unos milisegundos y se procesan en cuanto la cuenta está lista. A partir de este bootstrap, el selector de cuenta de origen queda bloqueado: cambiarlo implicaría mover los adjuntos a otra cuenta y la app prefiere forzar al usuario a descartar y empezar de cero.
3. **Si pasa la validación**: el archivo viaja al backend de MailManager y se guarda **en local** (en la base de datos de la app). El composer lo muestra inmediatamente como un chip; mientras la subida está en curso, el chip lleva un spinner pequeño y, si el archivo es grande (>5 MB), una barra de progreso.
4. **Cuando la subida termina**: el chip se asienta sin spinner. Ya está persistido.

Después del bootstrap, el proveedor (Gmail o Outlook) ya tiene un borrador "vacío" asociado, pero **el adjunto en sí no viaja** al proveedor todavía. El cuerpo, los destinatarios, el asunto y los archivos siguen viviendo solo en la app hasta que el usuario pulse "Guardar borrador" o "Enviar". Esto preserva el lazy push de la sección 6.9.

### 6.3 Quitar un adjunto

Click en la "X" del chip lo elimina inmediatamente del composer y del backend. No se pide confirmación (es una operación trivial). El proveedor sigue sin saber nada hasta el siguiente "Guardar" / "Enviar".

### 6.4 Cerrar y volver al borrador

Si el usuario cierra el composer (o cierra el navegador) sin enviar, **los adjuntos se mantienen** asociados al borrador en la app. Cuando el usuario vuelve a abrir el borrador, ve los mismos adjuntos que dejó. Esto también aplica a los composers que se abrieron como "Nuevo mensaje" en cuanto se haya producido el bootstrap silencioso de 6.2: a partir de ese punto hay un borrador real en la app y el cierre con X dispara el diálogo de la sección 6.5 igual que con cualquier otro borrador.

### 6.5 Cerrar el composer con cambios pendientes

Si el usuario intenta cerrar el composer (X de la ventana, Esc, o navegando fuera) y hay cambios no guardados —incluyendo adjuntos no sincronizados con el proveedor— la app muestra un **diálogo de confirmación** con tres opciones:

- **"Guardar y cerrar"** — la app guarda el borrador (lo que internamente sube los adjuntos al proveedor) y cierra. Si el guardado falla, el diálogo se queda abierto con el error.
- **"Descartar"** — la app borra el borrador entero, adjuntos incluidos. Si el cierre se produce tras un bootstrap silencioso desde "Nuevo mensaje", "Descartar" también borra ese borrador del proveedor para no dejar restos en Gmail/Outlook web.
- **"Cancelar"** — vuelve al composer sin cerrar.

Este diálogo aplica igual a los tres flujos del composer: editar un borrador con cambios, redactar un nuevo borrador con contenido, y redactar un "Nuevo mensaje" en el que ya se hayan adjuntado archivos (porque el bootstrap silencioso de 6.2 ya creó un borrador real con trabajo asociado).

Si NO hay cambios respecto a la última versión guardada, el cierre es directo, sin preguntar.

### 6.6 Guardar el borrador o enviarlo

Cuando el usuario pulsa **"Guardar borrador"** o **"Enviar"**, la app:

1. Toma todos los adjuntos almacenados localmente para ese borrador.
2. Construye el mensaje completo (cuerpo + adjuntos).
3. Lo sube al proveedor con la estrategia adecuada por proveedor (ver sección 10):
   - **En cuentas Gmail**, la app usa una sola llamada atómica que combina la actualización del borrador con el envío. Es una operación única; si falla, el borrador del proveedor no queda en estado intermedio.
   - **En cuentas Outlook**, la app sube primero los adjuntos al borrador del proveedor (uno por uno) y después emite la orden de envío. Si la subida de un adjunto falla, los que ya estaban subidos quedan asociados al borrador en el proveedor — al reintentar el envío la app los detecta y NO los re-sube. La operación es reanudable.
4. Si todo va bien, guarda el cambio localmente. Si no, deja el borrador como estaba y muestra un error.

A partir de ese momento, el borrador del proveedor sí tiene los adjuntos y se puede ver desde Gmail web o Outlook web con normalidad.

**Nota técnica**: las operaciones locales (añadir o quitar un adjunto, editar texto/recipients/asunto del borrador) **no tocan al proveedor** mientras el usuario está componiendo, salvo el bootstrap silencioso de 6.2 (creación de un borrador vacío para tener un identificador al que asociar los adjuntos). El push real del cuerpo, los destinatarios, el asunto y los binarios al proveedor solo ocurre al "Guardar y cerrar" o al "Enviar".

**Nota sobre "Nuevo mensaje" con adjuntos**: cuando el usuario pulsa "Enviar" desde un "Nuevo mensaje" que ya tiene adjuntos —y por tanto un borrador silencioso creado—, internamente la app reutiliza la operación de envío de borrador en lugar del envío directo de correo. Es transparente para el usuario y para el destinatario, y garantiza que los adjuntos lazy-pushed viajan en el mismo envío atómico (Gmail) o reanudable (Outlook) que el resto del mensaje.

### 6.7 Cuando el envío falla porque algún adjunto no llega al proveedor

Si al pulsar "Enviar" alguno de los adjuntos pendientes falla al subir al proveedor (después de los reintentos automáticos), el envío entero **se aborta**. El correo NO se manda. El destinatario NO recibe nada parcial.

La app muestra al usuario un mensaje claro: "El correo no se pudo enviar porque uno o más adjuntos fallaron al subir", listando los archivos concretos que fallaron, con dos opciones:

- **"Reintentar enviar"** — vuelve a intentar el envío completo.
- **"Quitar adjuntos fallidos y enviar"** — elimina del borrador los archivos que fallaron y reintenta el envío sin ellos.

El borrador queda intacto durante todo este proceso (los adjuntos siguen ahí en local hasta que el usuario decida).

**Por qué se aborta el envío entero en lugar de mandar a medias**: enviar un correo sin uno de los adjuntos prometidos es peor que no enviarlo. El destinatario no sabe que faltó algo; el sender cree que llegó completo. La app fuerza la decisión explícita.

### 6.8 Edge case aceptado: edición simultánea desde dos sitios

Si el usuario está editando un borrador en MailManager y al mismo tiempo abre Gmail web (o Outlook web) y mira el mismo borrador, **no verá los adjuntos** que ha añadido en MailManager hasta que pulse "Guardar borrador" o "Enviar" desde MailManager. Los dos lados se sincronizan en cuanto eso ocurre.

Esta inconsistencia temporal es **intencional** y el precio que se paga por una experiencia rápida en el composer (cada arrastre no tiene que esperar al proveedor) y por evitar gastar cuota innecesaria del proveedor durante la composición.

**Variante a tener en cuenta tras el bootstrap silencioso (6.2)**: el borrador en sí (vacío de contenido) **sí es visible** en Gmail/Outlook web durante la composición de un "Nuevo mensaje" o un "Nuevo borrador" en el que ya se hayan adjuntado archivos, porque la creación se hace en el momento de aceptar el primer adjunto. Sus adjuntos siguen siendo invisibles desde el lado web hasta el "Guardar borrador" o "Enviar". Si el usuario cierra el composer eligiendo "Descartar" en el diálogo de 6.5, ese borrador silencioso se elimina por completo del proveedor para no dejar restos.

### 6.9 Por qué este flujo (lazy push) en lugar de subir cada adjunto al proveedor inmediatamente

Hay una razón concreta para Gmail. Su API obliga a que cada actualización de un borrador **reconstruya el mensaje completo**: añadir el quinto adjunto a un borrador que ya tiene cuatro implica volver a subir los cinco. Si la app subiese cada adjunto inmediatamente al proveedor, redactar un correo con 5 adjuntos costaría 5 + 4 + 3 + 2 + 1 = 15 subidas en lugar de una. Es un gasto enorme de tiempo y cuota sin ningún beneficio para el usuario.

En Outlook el problema es distinto pero también real: hay un techo de 4 peticiones concurrentes por par `(app, buzón)`, y los reintentos por throttling (`429`) se acumulan rápido cuando el usuario adjunta varios archivos seguidos.

El flujo "guardar todo en local y empujar de golpe al proveedor" elimina ambos problemas y se comporta igual de bien con los dos proveedores.

---

## 7. Robustez ante errores

### 7.1 Reintentos al descargar adjuntos del proveedor

Cuando el usuario clica un adjunto que aún no está cacheado, la app llama al proveedor para bajarlo. Si la llamada falla por una causa transitoria (caída momentánea del proveedor, throttling, error de red), la app reintenta automáticamente:

- **3 intentos** en total.
- **Espera creciente**: 1 segundo, 2 segundos, 4 segundos.
- Si el proveedor responde con un `Retry-After` específico (típico en throttling de Microsoft Graph), la app lo respeta en lugar de la espera por defecto.

Solo se reintenta sobre errores transitorios (`429`, `500`, `502`, `503`, `504`, errores de red). Errores permanentes (`400`, `401`, `403`, `404`, `410`) NO se reintentan — la app reporta el problema directamente.

### 7.2 Comportamiento ante errores irrecuperables

Si tras los reintentos el adjunto sigue sin poder descargarse, la app distingue tres casos:

- **El adjunto ya no existe en el proveedor (`404` / `410`)**: por ejemplo, el remitente borró el correo enviado de su buzón, o el admin del tenant purgó el contenido. La app marca ese adjunto como "no disponible" en su base de datos y, la próxima vez que el usuario lo clica, le muestra: **"Este adjunto ya no está disponible en el servidor"**. La fila no se borra (la metadata sigue ahí), pero ese adjunto concreto queda inutilizable.
- **Sin permisos al adjunto (`403`)**: típicamente un problema de tokens caducados o de permisos del tenant. La app muestra: **"No se pudo acceder al adjunto"** y deja registro detallado del error para investigación.
- **El proveedor está caído (`5xx` persistente)**: la app muestra: **"Inténtalo de nuevo en unos minutos"** y deja un botón para reintentar. NO marca el adjunto como inutilizable: probablemente vuelve a funcionar al rato.

### 7.3 TTL del cache de adjuntos descargados

Los binarios cacheados localmente tienen una vida útil de **30 días desde el último acceso**. Si un adjunto no se abre durante 30 días, su binario se purga del almacenamiento (la metadata se queda). Si el usuario lo abre después, la app lo vuelve a descargar del proveedor y lo cachea de nuevo — es transparente.

Esta política de expiración mantiene la base de datos manejable sin penalizar al usuario que vuelve a pedir un adjunto antiguo.

La purga por TTL es **manual en el MVP**: existe un endpoint admin (`POST /admin/attachments/purge`) protegido por un token configurado en variable de entorno (`ATTACHMENTS_PURGE_TOKEN`). Si la variable no está seteada en el servidor, el endpoint responde como deshabilitado. Las foreign keys de la base de datos sí limpian automáticamente cuando se desconecta una cuenta o se borra un correo, sin necesidad de intervención. Cuando llegue el momento de automatizar la purga por TTL, se hará con un cron sustituyendo el endpoint manual.

**Sobre el almacenamiento técnico**: los binarios viven hoy en una columna `bytea` de PostgreSQL. Al servirlos al cliente, la app responde con un `StreamingResponse` que envía bytes por chunks al navegador, pero el `bytea` se carga completo en RAM del proceso al hacer el SELECT (no es streaming desde la base de datos). El tope de 25 MB por adjunto (D-01) acota el coste de RAM por descarga. Si en el futuro el cache crece más allá de unos ~50 GB, las tablas están preparadas para migrar los binarios a sistema de ficheros local o S3 sin romper la API.

---

## 8. Seguridad

### 8.1 Saneamiento del nombre del archivo

El nombre del archivo que llega al sistema (sea desde un correo recibido o desde el composer) puede contener cualquier cosa: caracteres de control, secuencias de path traversal (`..`), nombres exóticos. La app aplica un **saneamiento mínimo** preservando lo que importa al usuario:

- **Sustituye caracteres peligrosos** (`/`, `\`, caracteres de control, `..`) por `-`.
- **Preserva acentos y caracteres UTF-8 legítimos** (no es un slugify agresivo).
- **Preserva la extensión** (la última parte después del último punto).
- **Resuelve duplicados dentro del mismo correo o draft**: si un correo lleva dos adjuntos con el mismo nombre saneado, el segundo se renombra automáticamente añadiendo " (1)" antes de la extensión, igual que hace el explorador de Windows.

El nombre saneado es el que se persiste y el que se devuelve al cliente al descargar.

### 8.2 Cómo se sirve el binario al navegador

Cuando el frontend pide descargar un adjunto, el backend responde con cabeceras estrictas de seguridad:

- **`Content-Disposition: attachment`** — fuerza al navegador a descargar el archivo, NUNCA a renderizarlo inline. Esto es importante: si la app sirviese un HTML como inline, el navegador lo ejecutaría — un agujero XSS de manual.
- **`X-Content-Type-Options: nosniff`** — impide que el navegador "adivine" el tipo del fichero y lo trate como otra cosa.
- **`Cache-Control: private, no-cache`** — ningún proxy intermedio guarda el contenido.

### 8.3 Autenticación del endpoint de descarga

La descarga usa la **cookie de sesión** existente de la app. NO hay URLs firmadas con tokens en query string ni descargas anónimas.

Antes de servir el binario, el backend valida que:

1. Hay sesión activa (la cookie es válida).
2. El `mailbox_id` y `account_id` de la URL pertenecen al usuario logueado.
3. El adjunto pertenece a un correo de ese `account_id`.

Si cualquiera falla, la respuesta es `404 attachment_not_found` (no `403`) — para no filtrar la existencia de adjuntos ajenos.

---

## 9. Cómo se almacenan los adjuntos por dentro (visión técnica resumida)

Aunque el usuario no se entera, conviene que el equipo lo tenga claro:

- **Los adjuntos recibidos** se cachean en la base de datos de MailManager (PostgreSQL) en una **tabla dedicada al binario** (`email_attachment_blobs`), separada de la tabla con los metadatos (`email_attachments`). Eso permite listar adjuntos sin cargar accidentalmente megabytes en memoria.
- **El identificador del cache depende del proveedor**, porque los IDs no son igual de estables en uno y otro:
  - **En Gmail**, la documentación oficial NO declara que `attachmentId` sea estable, y hay reportes de que cambia entre llamadas. La app usa `(account_id, provider_message_id, partId)` como clave (el `partId` sí es inmutable según la doc) y se vuelve a descubrir el `attachmentId` cuando hace falta descargar.
  - **En Outlook**, los IDs cambian si el usuario mueve el mensaje entre carpetas. Para estabilizarlos, **todas** las llamadas a attachments envían el header `Prefer: IdType="ImmutableId"` (igual que ya se hace para mensajes y borradores). Con eso, `attachment.id` es estable mientras el mensaje permanezca en el mismo buzón.
  - Como red de seguridad, cada fila guarda también la tripleta `(content_id, filename, size)` que permite re-emparejar local↔remoto si los IDs cambian inesperadamente.
- **La columna `email_metadata.has_attachments`** (boolean denormalizado) sirve para que la lista del inbox pinte el icono de clip sin tener que hacer `JOIN` con la tabla de adjuntos en cada listado.
- **El orden de los adjuntos** se preserva con una columna `position INT` en cada fila: respeta el orden en que el proveedor los devolvió (recibidos) o el orden en que el usuario los añadió (drafts).
- **Los adjuntos de borradores** viven en una **tabla aparte** (`draft_attachments`), también en PostgreSQL, también con el binario en `bytea`. Están vinculados al borrador que los contiene: si el borrador se borra o se envía, los adjuntos del borrador se van con él (cascade).
- **No se deduplica nada**. Si el mismo PDF llega a tres cuentas distintas conectadas a la misma instalación de MailManager, se guardan tres copias. Es una decisión consciente para mantener la implementación simple en el MVP. Se reevaluará si el cache crece más allá de unos 50 GB.
- **Las tablas están preparadas para migrar**: tienen columnas (`blob_storage_kind`, `blob_ref`) que permiten en el futuro mover los binarios fuera de PostgreSQL (a sistema de ficheros local o a almacenamiento externo tipo S3) sin romper la API ni la lógica de la app. Hoy no se usan: todos los binarios viven en `bytea`.

---

## 10. Qué pasa "por debajo" cuando llega un correo con adjuntos

Para entender el flujo completo de un vistazo:

1. Sincronización de la bandeja → la app baja la metadata de los correos, incluyendo el flag `has_attachments`. Ningún binario.
2. El usuario clica un correo concreto → la app pide al proveedor el contenido HTML/texto + la lista de adjuntos de ese correo. Persiste todo en local. Renderiza el correo con su lista de adjuntos.
3. El usuario clica un adjunto → la app pide ese binario al proveedor (con reintentos automáticos si falla por causa transitoria; cuando el proveedor devuelve `Retry-After`, la app lo respeta literalmente en lugar de aplicar su backoff por defecto), lo persiste, y se lo entrega al navegador con cabeceras de descarga forzada.
4. El usuario clica el mismo adjunto otra vez (mañana) → la app lo sirve directo desde su cache local. El proveedor no se entera.
5. El usuario clica un adjunto **distinto** del mismo correo → vuelve al paso 3 con ese binario nuevo.
6. Pasados 30 días sin acceso → un administrador puede invocar la limpieza manual y los binarios sin uso reciente se purgan. La metadata permanece. La próxima vez que el usuario clique uno purgado, se redescarga desde el proveedor.

---

## 11. Qué pasa "por debajo" cuando se envía o se guarda un correo con adjuntos

1. El usuario abre el composer y arrastra un PDF → la app valida cliente-side (extensión, tamaño individual, tamaño total, conteo) → si OK, sube el binario al backend → se guarda en local, asociado al borrador. UI lo refleja al instante con un chip.
2. El usuario sigue añadiendo o quitando adjuntos → todo ocurre en local. El proveedor sigue sin saber nada.
3. El usuario pulsa "Guardar borrador" → la app construye el mensaje completo con cuerpo + todos los adjuntos y lo sube al proveedor en una sola operación. Si va bien, sincroniza el estado local. Si falla, deja todo como estaba.
4. El usuario pulsa "Enviar" → mismo proceso que "Guardar borrador" pero, si el proveedor confirma envío, se eliminan los adjuntos locales del borrador (ya viven en el correo enviado del proveedor) y la entrada de borrador correspondiente se elimina.

Si el archivo es grande (>5 MB en Gmail, >3 MB en Outlook), la subida al proveedor usa internamente el mecanismo "resumable" / "uploadSession" del proveedor, troceando el archivo. El usuario no percibe diferencia más allá de la barra de progreso del chip.

Si **un solo adjunto** falla al subir al proveedor durante el envío (después de reintentos), todo el envío se aborta: el correo NO se manda y el usuario decide explícitamente entre reintentar o quitar los archivos fallidos.

---

## 12. Lo que NO hace la app por ahora (limitaciones aceptadas para el MVP)

Estas decisiones se documentan a propósito como aceptadas para el MVP:

- **No hay deduplicación**: dos copias del mismo binario ocupan dos veces el espacio.
- **No hay protección antivirus** ni verificación de "magic bytes" para detectar archivos cuyo tipo real no coincide con la extensión declarada.
- **No hay limpieza automática del cache**: la purga por TTL (30 días) se ejecuta manualmente vía endpoint admin protegido por token (`POST /admin/attachments/purge` con header `X-Admin-Token`). El token vive en una variable de entorno del servidor (`ATTACHMENTS_PURGE_TOKEN`); si no está seteada, el endpoint responde como deshabilitado. Cuando llegue el momento, este endpoint se sustituirá por un cron.
- **No hay descarga masiva**: no existe "descargar todos los adjuntos de este correo" ni "descargar como ZIP". Hay que clicar uno por uno.
- **No hay vista previa inline para tipos no-imagen**: los PDFs y similares se descargan, no se renderizan dentro de la app.
- **Reply / Forward NO pre-rellenan adjuntos** (**diferido a v1.1**, no es limitación permanente): el composer no incluye los adjuntos del correo original al responder o reenviar. Si el usuario quiere reenviar un PDF que recibió, tiene que descargarlo y volver a adjuntarlo manualmente. Cuando se aborde la v1.1, los adjuntos del original aparecerán como chips ya cargados, opcionalmente descartables.
- **No se soporta el caso de archivos enormes vía referencia a Drive / OneDrive**: la app no genera enlaces a Drive cuando el usuario adjunta algo >25 MB; simplemente rechaza el archivo.
- **No hay métricas custom (Prometheus / OpenTelemetry)**: solo logs estructurados en operaciones críticas (descarga del proveedor, errores).
- **El cuerpo del composer es texto plano**: el composer es un `<textarea>` plano, no un editor rich-text. El correo se envía siempre como `text/plain` a ambos proveedores. Pegar imágenes desde el portapapeles al cuerpo no funciona — la feature "pegar imagen → inline" requiere el composer rich, que está fuera de scope MVP.
- **No se valida `Origin` ni `Referer` en el endpoint de descarga** (mejora futura): el endpoint usa la cookie de sesión y verifica pertenencia, pero no añade defensas extra contra exfiltración cross-site del binario por una pestaña pirata que ya tenga sesión válida.

Si más adelante los usuarios reportan que necesitan algo de lo anterior, hay un plan de fases futuras para añadirlo.

---

## 13. Resumen en una frase

> La app acepta hasta 25 archivos por correo, 25 MB por archivo, con un techo total de 25 MB por mensaje sin distinción entre proveedores; bloquea ejecutables al enviar pero acepta cualquier cosa al recibir; descarga los binarios del proveedor solo cuando el usuario los clica (con un máximo de 2 descargas concurrentes en cola) y los cachea localmente con un TTL de 30 días sin acceso; mantiene los adjuntos de borradores en su propia base de datos hasta que el usuario decide guardarlos o enviarlos, momento en el que se suben todos juntos al proveedor (atómicamente en Gmail, reanudable adjunto a adjunto en Outlook); aborta cualquier envío en el que falle algún adjunto, dejando el borrador intacto para reintentar; y delega la protección frente a contenido malicioso al sistema operativo del usuario, igual que hace Gmail web.

Eso es todo lo que necesita saber un programador (o cualquier persona del equipo) para entender cómo se va a comportar la gestión de adjuntos en el MVP.
