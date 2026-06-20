# Panel de Ajustes — comportamiento (MVP)

Este documento describe **qué es el área de Ajustes** de MailManager y **qué experimenta el usuario** dentro de ella: la identidad de su cuenta, la gestión de cuentas conectadas, el renombrado y borrado de bandejas, el selector de idioma de la interfaz, el "sincronizar todo" y el "acerca de". No entra en código: es una guía de comportamiento para el equipo y futuros mantenedores.

Antes esta funcionalidad era un **menú flotante** del engranaje de la barra lateral con solo dos acciones (cerrar sesión y eliminar cuenta de usuario). Ahora ese engranaje lleva a un **área dedicada con su propia ruta** y seis secciones. Esta página documenta la zona de Ajustes como tal; las funcionalidades que **se integran** dentro de ella (conectar/editar/desconectar cuentas, crear/cambiar de bandeja) tienen sus propios documentos y aquí solo se mencionan en su papel dentro del panel — el modelo de cuentas y buzones vive en [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) y [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md).

Los pocos topes numéricos (longitud del nombre de bandeja, versión de la app, clave de persistencia del idioma) y la lista de "lo que deliberadamente NO hace" viven en su gemelo: **[../limits/ajustes.md](../limits/ajustes.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están las cifras exactas.

---

## 1. Cómo se llega a Ajustes y cómo está organizado

El icono de **engranaje** al pie de la barra lateral ya no abre un menú flotante: es un enlace que navega al área de Ajustes del buzón activo (la dirección lleva siempre el buzón actual, igual que el resto de la app). Una vez dentro, Ajustes es un **área con sub-navegación**: un menú lateral propio lista las seis secciones y cada una es una sub-pantalla dentro del panel.

Las seis secciones, en orden:

1. **Tu cuenta** — identidad del usuario, cerrar sesión, eliminar cuenta.
2. **Cuentas conectadas** — gestión de las cuentas de correo (Gmail / Outlook).
3. **Bandejas** — renombrar y eliminar bandejas.
4. **Idioma** — selector de idioma de la interfaz (Español / English).
5. **Datos** — "Sincronizar todo ahora".
6. **Acerca de** — versión de la app.

Por qué un área propia y no un menú: el menú flotante solo cabían dos acciones. El panel reúne en un mismo sitio todo lo que es "configuración y gestión de la cuenta", incluida la pantalla de cuentas conectadas, que **antes colgaba suelta** de la barra lateral y ahora vive **integrada** dentro de Ajustes (es la misma pantalla de siempre, montada en una sub-ruta del panel). El *porqué* de reutilizar esa pantalla sin romper la frontera entre partes de la app está en el documento de frontend; a nivel de comportamiento, lo único que cambia para el usuario es **dónde** entra a gestionar sus cuentas.

---

## 2. Tu cuenta

### 2.1 Identidad visible

La sección abre con una cabecera de identidad del usuario: **avatar + nombre + email** de la cuenta con la que inició sesión en MailManager. Conviene no confundirla con las cuentas de correo conectadas: esta es la cuenta de **autenticación** (la de Google con la que se entra a la app), distinta de los buzones de Gmail/Outlook que el usuario conecta para leer correo.

- El **avatar** usa la **foto de perfil de Google** si está disponible. Si no la hay —o si la URL no es de confianza (solo se aceptan `http`/`https`; cualquier otro esquema se descarta por seguridad, ver [../limits/ajustes.md](../limits/ajustes.md))—, se muestran las **iniciales** sobre un círculo de color.
- Las **iniciales** se derivan del nombre (las primeras letras de hasta dos palabras); si no hay nombre, de la primera letra del email. Siempre en mayúsculas y nunca vacías mientras haya email.
- Si faltara el **nombre**, se muestra el **email** en su lugar. El email siempre está presente.

Esta identidad **no es una integración nueva**: se captura al iniciar sesión con Google y aquí simplemente se muestra. Ajustes no llama a ninguna API externa para pintarla.

### 2.2 Cerrar sesión

Cierra la sesión y lleva siempre a la pantalla de **inicio de sesión** (`/login`), nunca a "crear bandeja". Este destino ya era el correcto en el código; la pieza importante al transformar el menú en panel fue **no regresarlo**: ninguna ruta intermedia debe dejar al usuario en "crear bandeja" tras cerrar sesión.

### 2.3 Eliminar cuenta (borrado total e irreversible)

Borra **todo** lo del usuario. Por su gravedad, el flujo es deliberadamente fricciónado:

1. Se abre un **diálogo centrado y bloqueante** (con el fondo oscurecido), no un popover de esquina.
2. **Enumera qué se borra**: todas las bandejas, todas las cuentas de correo conectadas y todos los correos sincronizados.
3. El botón rojo de borrado permanece **deshabilitado** hasta que el usuario **teclea su propio email exactamente** (coincidencia carácter a carácter). Un solo clic nunca basta.
4. Tras borrar, se cierra la sesión y se vuelve a la pantalla de inicio de sesión.

> El borrado en cascada de los datos (bandejas → cuentas → correos) lo hace la base de datos automáticamente; eliminar el usuario se lleva por delante todo lo que cuelga de él. El detalle de esa cascada está en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md).

---

## 3. Cuentas conectadas

Esta sección **reúne en el panel** la gestión que antes colgaba suelta de la barra lateral. No cambia su comportamiento: añadir una cuenta (elegir proveedor Gmail/Outlook + etiqueta opcional), reconectar una cuenta cuyo token caducó, y eliminar una cuenta de correo concreta funcionan como siempre. El flujo completo (popup de OAuth, rollback si no se confirma, reconexión) está en [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md).

**Novedad — Editar la etiqueta de una cuenta.** Hasta ahora la etiqueta (el nombre personalizado de una cuenta, p. ej. "Trabajo") solo se podía fijar **al conectar** la cuenta. Se añade una acción **"Editar etiqueta"** en el menú de la tarjeta de cuenta: abre una edición **en línea** sobre la propia tarjeta, el usuario teclea el nuevo nombre, confirma, y la tarjeta se actualiza al instante con la nueva etiqueta. Un nombre vacío (o solo espacios) no se guarda. El backend ya sabía actualizar la etiqueta de una cuenta; lo nuevo es la puerta de entrada en la interfaz.

---

## 4. Bandejas

Esta sección estrena dos capacidades que el modelo de buzón del MVP **no exponía** en la interfaz (ver el detalle del cambio en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) § 2.4):

- **Renombrar** una bandeja existente. Cada bandeja se lista con una acción de lápiz que abre una edición **en línea** del nombre; al confirmar, el nuevo nombre aparece en la propia lista y en el selector de la cabecera. Un nombre vacío o solo-espacios no se acepta. La longitud máxima del nombre es la misma que al crearlo (la cifra está en [../limits/ajustes.md](../limits/ajustes.md)).
- **Eliminar** una bandeja existente. La acción de papelera abre una **confirmación** que avisa de que se eliminarán las cuentas de correo que contiene la bandeja **y todos sus correos sincronizados** (es el mismo borrado en cascada que ya existía en el backend; lo nuevo es que ahora hay un control visible que lo dispara).

Crear y cambiar de bandeja siguen funcionando como hoy, desde el selector de la cabecera.

**Qué pasa al borrar la bandeja que estás viendo.** Si el usuario elimina la bandeja activa, la app lo lleva a **otra bandeja suya** (la primera que sobreviva). Si no le queda **ninguna**, lo lleva al inicio, que a su vez lo deposita en "crear bandeja" —el mismo onboarding del primer arranque—. Borrar la última bandeja está permitido a propósito.

> Renombrar y eliminar una bandeja también está disponible, para la bandeja activa, desde el desplegable del selector de la cabecera; la sección "Bandejas" de Ajustes es la vista que las ofrece para **todas** las bandejas a la vez.

---

## 5. Idioma

La app deja de ser "español-fijo": **toda la interfaz** puede mostrarse en **Español o English**. La sección "Idioma" ofrece un selector con las dos opciones y una marca en la activa; al elegir una, **todos los textos de la interfaz cambian al instante**, sin recargar ni volver a entrar.

Dos límites de alcance, deliberados:

- **Idioma = solo interfaz.** No se traduce el **contenido de los correos** (siguen en su idioma original) ni **lo que el usuario escribe** al redactar. En particular, la **cabecera de cita** que se inserta al Responder/Reenviar («El día … escribió:») es **contenido del correo** y **permanece en español**: no la afecta este selector (ver [responder-y-reenviar.md](responder-y-reenviar.md) y su gemelo R-05). Los **operadores** de la lupa (`from:`, `to:`, …) también siguen siendo **solo en inglés** por decisión propia, igual que en Gmail (ver [lupa.md](lupa.md)).
- **La elección se recuerda en el navegador**, no en el servidor. La primera vez se **detecta el idioma del navegador** (English solo si el idioma del navegador empieza por `en`; cualquier otro caso, incluido el desconocido, cae en **Español**); a partir de ahí se respeta lo que el usuario haya elegido. Como la preferencia vive en el navegador, **no viaja entre dispositivos**: el mismo usuario en otro equipo vuelve a la detección inicial. Es una limitación aceptada del MVP; el idioma **no añade nada al backend** (ni columna ni endpoint).

---

## 6. Datos — Sincronizar todo

Un botón **"Sincronizar todo ahora"** fuerza la sincronización del correo de **todas** las cuentas conectadas del usuario, recorriendo **todas sus bandejas**. Reutiliza la sincronización que ya existe (ver [sincronizacion.md](sincronizacion.md)): dispara una sincronización por bandeja, sin acotar a ninguna cuenta concreta, de modo que se sincronizan todas las cuentas de cada bandeja.

Mientras corre, el botón se deshabilita y muestra un **indicador de progreso** ("N de M" bandejas) que avanza según cada bandeja termina. El proceso es **tolerante a fallos parciales**: si una bandeja falla, las demás siguen y el contador llega igualmente al total. Al acabar:

- Si **todas** las bandejas se sincronizaron bien, se muestra un aviso de "sincronización completa".
- Si **alguna** falló (pero el proceso terminó), se muestra un aviso de error **parcial**.
- Si la operación ni siquiera pudo arrancar (no se pudo obtener la lista de bandejas), se muestra el error correspondiente y no se sincroniza nada.

En cualquiera de los casos en que sí hubo sincronización, los **listados de correo se refrescan** al terminar para que el correo recién traído aparezca.

---

## 7. Acerca de

Muestra el nombre y el **logo** de la aplicación y su **versión**, una constante fija del frontend (la cifra exacta está en [../limits/ajustes.md](../limits/ajustes.md)). Los enlaces a **ayuda / privacidad / términos** quedan **deliberadamente ocultos** hasta que existan páginas reales a las que apuntar; se añadirán cuando esas páginas existan y sus URLs se puedan verificar.

---

## 8. Qué experimenta el usuario, de principio a fin

1. Pulsa el engranaje de la barra lateral → entra al área de Ajustes del buzón activo, en la sección "Tu cuenta".
2. Ve su identidad (avatar/nombre/email de Google) y, debajo, los botones de cerrar sesión y eliminar cuenta.
3. Navega por el menú lateral a "Cuentas conectadas" para añadir, reconectar, eliminar o **editar la etiqueta** de una cuenta de correo.
4. En "Bandejas", **renombra** o **elimina** cualquiera de sus bandejas (con confirmación al borrar).
5. En "Idioma", cambia la interfaz a Español o English al instante; la elección se recuerda en ese navegador.
6. En "Datos", pulsa "Sincronizar todo ahora" y ve el progreso hasta que todas sus bandejas terminan.
7. En "Acerca de", consulta la versión de la app.
8. Al cerrar sesión o eliminar la cuenta, acaba en la pantalla de inicio de sesión.

---

## 9. Resumen en una frase

> El antiguo menú del engranaje (cerrar sesión + eliminar cuenta) se convierte en un **área de Ajustes** con ruta propia y seis secciones —Tu cuenta (identidad de Google, cerrar sesión que termina siempre en login, y eliminar cuenta con un diálogo bloqueante que exige teclear el propio email), Cuentas conectadas (la gestión de siempre, ahora integrada, más la nueva acción de **editar la etiqueta** de una cuenta), Bandejas (estrenando **renombrar** y **eliminar** con su cascada y confirmación), Idioma (toda la interfaz en Español/English al instante, recordado en el navegador y solo de la interfaz —el contenido de los correos y la cabecera de cita siguen en su idioma—), Datos ("Sincronizar todo" tolerante a fallos parciales con progreso) y Acerca de (versión fija, sin enlaces aún)—; las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/ajustes.md](../limits/ajustes.md).
