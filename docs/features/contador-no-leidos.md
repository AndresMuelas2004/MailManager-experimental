# Contador de correos no leídos (badge) — comportamiento

Este documento describe **qué hace** el contador de no leídos y **qué experimenta** el usuario: dónde aparece el badge numérico, qué cuenta exactamente, cuándo se actualiza solo y por qué unas entradas del menú lo llevan y otras no. No entra en cómo está cableado el código: es una guía de comportamiento para el equipo.

Las cifras concretas (el tope "99+", las bandejas con badge, el formato del título) y la lista de "qué NO soporta" viven en su gemelo para no repetir números aquí: **[../limits/contador-no-leidos.md](../limits/contador-no-leidos.md)**. Este fichero solo los menciona de pasada y enlaza a ese catálogo.

Fronteras con otras features (no se cubren aquí, tienen su propio documento):

- **De dónde sale el correo y qué es "lo sincronizado"** se documenta en [listado-de-correos.md](listado-de-correos.md) (el contador lee de la misma copia local) y en [sincronizacion.md](sincronizacion.md).
- **La agrupación por conversación** del listado se documenta en [conversaciones.md](conversaciones.md). Es la clave para entender por qué el badge puede ser mayor que el número de filas (sección 3).
- **Las acciones que cambian "leído / no leído"** (abrir, marcar leído/no leído, abrir conversación, mover a papelera, spam) se documentan en [acciones-sobre-correos.md](acciones-sobre-correos.md). El contador solo **refleja** el efecto de esas acciones; no las añade ni las modifica.
- **El concepto de buzón (mailbox) y la vista de una cuenta vs. la unificada**, y el **selector de cuentas** de la barra lateral que fija el ámbito del que cuelga el badge, se documentan en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md).

---

## 1. Qué es

Un **badge numérico de correos sin leer** que aparece junto a las entradas de navegación del correo. De un vistazo, el usuario sabe **dónde hay correo nuevo por leer** sin entrar bandeja por bandeja ni cuenta por cuenta. Es la misma señal que Gmail y Outlook muestran junto a cada carpeta y en el icono de la app.

El badge es **informativo, no es un botón**. Para ir a una bandeja se sigue usando la propia entrada del menú lateral; el badge solo cuelga de ella como indicador.

## 2. Dónde aparece

El contador se muestra en el **menú lateral** —que refleja el ámbito activo del selector de cuentas— más el **título de la pestaña del navegador**:

1. **Menú lateral en la vista unificada — «Bandeja unificada» y «Spam».** Totales de sin leer de la **bandeja de entrada** y de la **carpeta de spam**, sumando **todas las cuentas** del buzón actual.
2. **Menú lateral dentro de una cuenta concreta — «Bandeja de entrada» y «Spam».** Cuando el selector de cuentas está en una cuenta, esas dos mismas entradas muestran los sin leer **de esa cuenta** en cada carpeta. Es el mismo menú: solo cambia el ámbito del que cuelga el número.
3. **Título de la pestaña del navegador.** El título refleja el total de sin leer de la **bandeja de entrada del buzón actual** —siempre a nivel de buzón, no del ámbito de cuenta que esté activo—, con el formato **«(N) MailManager»**. Cuando no hay sin leer, vuelve a ser **«MailManager»**. El número exacto que muestra el título (incluido su tope) está en el gemelo de límites.

> **Por qué solo Bandeja de entrada y Spam llevan contador.** Las entradas **Enviados, Favoritos, Bandejas ficticias, Borradores y Papelera no tienen badge**: no tienen una noción natural de «no leído». Los enviados y los borradores son del propio usuario (no se "leen"); Favoritos, Papelera y las bandejas ficticias son vistas **transversales** que cruzan bandejas, donde "cuántos sin leer" no es una pregunta con una respuesta única y útil. Es una decisión de producto, no una limitación técnica.

## 3. Qué cuenta exactamente

- **Cuenta mensajes individuales sin leer, no conversaciones.** Si una conversación tiene tres mensajes sin leer, el contador suma **tres**.
- **«Bandeja unificada» / «Bandeja»** cuenta los sin leer de la **bandeja de entrada**: correo recibido que **no** está en spam ni en papelera (ni eliminado definitivamente). Los enviados tampoco cuentan.
- **«Spam»** cuenta los sin leer de la **carpeta de spam**.
- **Refleja solo el correo ya sincronizado** en la copia local de la app —lo mismo que muestran los listados (ver [listado-de-correos.md](listado-de-correos.md))—: **no** es el contador en vivo del proveedor, sino el de lo que la app ya ha traído. Si Gmail tiene correo nuevo sin leer que aún no se ha sincronizado, todavía no cuenta.

### 3.1 Por qué el badge puede ser mayor que el número de filas

Hay una asimetría deliberada que conviene entender: el **listado de la bandeja de entrada agrupa los correos por conversación** (cada fila es un hilo — ver [conversaciones.md](conversaciones.md)), pero el **contador suma mensajes individuales**. Por eso el número del badge puede ser **mayor** que el número de filas (conversaciones) visibles en la lista.

Es intencionado: el badge responde a «cuántos correos tengo sin leer», no a «cuántas conversaciones». Coincide con la intuición de Gmail, donde el contador de la carpeta también cuenta mensajes, no hilos.

#### Ejemplo

> El usuario tiene en su bandeja de entrada una sola conversación, pero con **tres** mensajes nuevos sin leer. La lista muestra **una** fila (el hilo colapsado), mientras que el badge de «Bandeja unificada» muestra **3**. Al abrir la conversación, sus tres mensajes quedan leídos y el badge baja a **0** (desaparece).

## 4. Cuándo cambia (se mantiene al día solo)

El contador se mantiene **siempre al día** con las acciones del usuario: sube o baja **automáticamente, sin recargar la página**, en cuanto cambia el estado de lectura del correo. Se actualiza cuando:

- Se **abre un correo** sin leer (queda marcado como leído) → baja.
- Se **marca como leído / no leído** uno o varios correos, individualmente o **en bloque** → baja o sube.
- Se **abre una conversación** completa (sus mensajes sin leer quedan leídos) → baja.
- Se **mueve un correo a la papelera** o se **marca como spam**: deja de contar en la bandeja de origen; si se marca como spam y seguía sin leer, **pasa a contar en «Spam»**.
- Se **sincroniza** el buzón y llegan correos nuevos sin leer → sube.

El **título de la pestaña del navegador** se actualiza con los mismos cambios para la bandeja de entrada, de modo que el usuario sabe si tiene correo nuevo **aunque esté en otra pestaña del navegador**.

> Todas estas acciones ya existían antes de esta funcionalidad (se documentan en [acciones-sobre-correos.md](acciones-sobre-correos.md), [conversaciones.md](conversaciones.md) y [sincronizacion.md](sincronizacion.md)). El contador no añade ninguna acción nueva: solo **observa** el estado de lectura y se recalcula tras cada una.

## 5. Detalles de presentación

- **Cero sin leer: no se muestra badge.** Nunca aparece un «0»; la entrada se ve limpia. El título de la pestaña vuelve a «MailManager».
- **Números altos: tope visual.** A partir de cierto número el badge muestra una forma abreviada («99+») para no romper el diseño del menú. El umbral exacto está en [../limits/contador-no-leidos.md](../limits/contador-no-leidos.md). El título de la pestaña puede mostrar el mismo formato.
- **Mientras carga.** La primera vez que se entra a un buzón, el badge puede tardar un instante en aparecer mientras se calcula el conteo. **No bloquea** la navegación ni el listado: la lista ya es visible y usable, y el badge aparece en cuanto el dato está disponible.
- **Si el conteo falla.** Un fallo al calcular el conteo **no rompe la pantalla**: simplemente no se muestra el badge (se comporta como un 0). La página tiene sus propias consultas y su propio manejo de errores; el badge es un extra que nunca debe estropear la vista.

## 6. Qué NO hace esta funcionalidad

Resumen de las fronteras (el catálogo completo, con el porqué de cada una, está en [../limits/contador-no-leidos.md](../limits/contador-no-leidos.md)):

- **No marca correos como leídos por sí solo.** Solo refleja el estado real; quien cambia «leído/no leído» son las acciones que ya existían.
- **No añade contadores** a Enviados, Favoritos, Archivados, Bandejas ficticias, Borradores ni Papelera (sección 2).
- **No añade sonidos, notificaciones del sistema ni notificaciones push.** Eso pertenece a la funcionalidad de **Notificaciones** (una feature aparte), con la que el título de la pestaña del navegador se coordina.
- **No cambia el orden ni el contenido** de los listados de correo.
- **No persiste ninguna preferencia nueva** del usuario ni añade ajustes configurables.

---

## 7. Resumen en una frase

> El contador de no leídos es un badge numérico —en «Bandeja unificada» y «Spam» del menú lateral en la vista unificada, en «Bandeja de entrada» y «Spam» del mismo menú al entrar en una cuenta, y en el título de la pestaña del navegador como «(N) MailManager»— que suma los **mensajes individuales sin leer** (no conversaciones, por eso puede superar al número de filas del listado) de la **copia ya sincronizada** de la bandeja de entrada o de spam, se actualiza solo y sin recargar tras abrir, marcar leído/no leído (también en bloque), mover a papelera o spam y sincronizar, oculta el «0» y abrevia los números altos, y deliberadamente no aparece en Enviados, Favoritos, Borradores, Papelera ni bandejas ficticias; las cifras exactas y todo lo que no soporta viven en [../limits/contador-no-leidos.md](../limits/contador-no-leidos.md).
