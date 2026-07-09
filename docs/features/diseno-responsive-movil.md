# Diseño responsive / uso en móvil — comportamiento

Este documento describe **qué cambia en la interfaz de MailManager cuando la ventana es estrecha** (móviles y tablets en vertical) y **qué experimenta el usuario** en ese modo: la barra superior con menú, el cajón de navegación, el botón flotante de redactar, los visores y el compositor a pantalla completa, el listado como tarjetas, el paginador compacto y los demás ajustes de aspecto. No entra en código: es una guía de comportamiento para el equipo y futuros mantenedores.

Es una capacidad **nueva y transversal de la capa de presentación**: ninguna funcionalidad cambia lo que hace, ningún flujo cambia sus pasos y **ningún contrato con el servidor cambia** (mismos endpoints, mismos DTOs, mismo tamaño de página, mismas llamadas a Gmail/Outlook). Lo único que cambia es **cómo se ve y se distribuye** la interfaz. Por eso este documento no describe nuevas capacidades de correo —esas viven en sus propios documentos—, sino cómo se **re-disponen** las que ya existen en una pantalla pequeña.

El único tope con cifra exacta (el punto de corte) y la lista de "lo que deliberadamente NO hace" viven en su gemelo: **[../limits/diseno-responsive-movil.md](../limits/diseno-responsive-movil.md)**. Aquí se menciona de pasada y se explica el *porqué*; allí están las cifras.

---

## 1. El corte: escritorio vs. móvil

Existe **un único punto de cambio de disposición**, en un ancho de ventana concreto (la cifra está en [../limits/diseno-responsive-movil.md](../limits/diseno-responsive-movil.md)):

- **En escritorio (por encima del corte):** todo se ve **exactamente igual que antes**. Barra lateral fija, listados en columnas, compositor acoplado en la esquina, visores como ventana modal centrada, Ajustes con su menú lateral. **Cero cambios visibles.**
- **En móvil y tablet en vertical (por debajo del corte):** se activa la disposición adaptada al dedo que se describe en este documento.

El cambio es **continuo y automático**: basta con estrechar o ensanchar la ventana, o rotar el dispositivo, para pasar de una disposición a otra. No hay un botón de "modo móvil" ni una preferencia que recordar — lo decide el ancho de la ventana en cada momento, vía CSS. No hay estado nuevo en el servidor ni en el navegador para esto.

> El corte es **único** a nivel de disposición global (la chrome de escritorio entera frente a la chrome móvil entera). Algunos contenedores internos reorganizan su contenido un poco antes (p. ej. las tarjetas de cuenta se apilan, ciertos formularios pasan sus campos de fila a columna); ese segundo umbral, más fino, está catalogado en el gemelo.

---

## 2. Navegación principal: barra superior + cajón

En escritorio la navegación es la **barra lateral** de siempre. En móvil esa barra **desaparece de la vista** y la navegación se reparte en dos piezas:

- Una **barra superior fija** con un **botón de menú (hamburguesa)** a la izquierda y el **nombre de la bandeja actual** al lado. Es lo único de navegación que queda siempre visible.
- Un **cajón lateral deslizante** que contiene lo mismo que la barra lateral de escritorio (logo, selector de bandeja, accesos a Recibidos, Enviados, Favoritos, Bandejas ficticias, Spam, Borradores, Papelera y Ajustes).

Al pulsar la hamburguesa, el cajón **entra deslizándose desde la izquierda** sobre un **fondo oscurecido** (el resto de la pantalla se atenúa). El cajón **se cierra solo** en tres situaciones:

1. al **elegir un destino** (cualquier enlace de navegación, cambiar de bandeja, crear una bandeja o pulsar redactar);
2. al **tocar fuera**, sobre el fondo oscuro;
3. con su propio **botón de cerrar** (la X de la cabecera del cajón).

En cuanto se cierra, el listado de correos vuelve a ocupar **toda la anchura** de la pantalla. Que el cajón se cierre al navegar es deliberado: en móvil no tiene sentido mantener tapado el contenido al que el usuario acaba de ir.

---

## 3. Redactar: el botón flotante

El botón redondo de **redactar** vive dentro de la barra lateral, que en móvil queda oculta en el cajón. Para no perder el acceso a "redactar correo nuevo", aparece un **botón flotante de acción** (un círculo azul con el icono de enviar) **abajo a la derecha**, **siempre visible** sobre el listado. Pulsarlo abre el compositor en modo "Nuevo mensaje" (ver [composicion-y-envio.md](composicion-y-envio.md)), igual que el botón de la barra lateral en escritorio.

---

## 4. Compositor a pantalla completa

En escritorio el **compositor** es la conocida **ventanita acoplada** en la esquina inferior derecha. En móvil pasa a **pantalla completa**: ocupa toda la ventana, con el cuerpo desplazable, de modo que todos los campos (Para / CC / CCO, asunto, cuerpo enriquecido, adjuntos y las acciones de enviar / guardar / descartar) queden cómodos para el dedo. No cambia **qué** hace el compositor ni cómo se envía o se guarda — solo su tamaño y disposición. Todo el comportamiento de composición, borradores y adjuntos sigue siendo el de [composicion-y-envio.md](composicion-y-envio.md), [borradores.md](borradores.md) y [adjuntos.md](adjuntos.md).

---

## 5. Lectura: visores a pantalla completa

El **visor de correo** y el **visor de conversación** (la cadena de mensajes de un hilo, ver [conversaciones.md](conversaciones.md)) son en escritorio una **ventana modal centrada**. En móvil pasan a **pantalla completa**, para leer cómodamente el cuerpo, ver los datos de remitente / destinatario y los adjuntos, y usar Responder / Responder a todos / Reenviar (ver [responder-y-reenviar.md](responder-y-reenviar.md)). El contenido y las acciones del visor son los mismos; solo cambia que pasa a ocupar toda la pantalla en lugar de flotar centrado.

> El **cuerpo del correo** se sigue mostrando dentro de su marco aislado (iframe) — ver [visualizacion-de-correos.md](visualizacion-de-correos.md). Ese cuerpo es HTML de terceros y **puede no estar pensado para móvil**: si la plantilla original no es responsive, puede requerir desplazamiento dentro del propio marco. Esto es una consecuencia del contenido ajeno, no del visor.

---

## 6. Listado de correos: de tabla a tarjetas

Aplica a todas las vistas de listado (Recibidos, Enviados, Spam, Papelera, Favoritos, Bandejas ficticias y Borradores).

- En **escritorio** se mantiene la **tabla por columnas** de siempre (Remitente, Para, De, Asunto, Fecha) con su fila de cabeceras.
- En **móvil** desaparece la fila de cabeceras y cada correo se muestra como una **tarjeta de dos líneas**:
  - **Línea 1:** remitente (proveedor y/o nombre) a la izquierda y **fecha** a la derecha.
  - **Línea 2:** el **asunto** con sus indicadores habituales (estrella de favorito/hilo, clip de adjunto, contador de mensajes del hilo).
  - A la izquierda se mantiene la **casilla de selección**, para poder seguir seleccionando correos.

Se conservan **todas las señales** de la tabla: los **no leídos** siguen resaltando en negrita y con fondo distinto, y **tocar la fila abre el correo** igual que el clic en escritorio. Las columnas **"Para" y "De" se ocultan** en móvil porque no caben; su información sigue disponible al **abrir** el correo. La lógica de cuándo se muestra "Para" o "De" (vista de una cuenta vs. unificada) no cambia — solo deja de pintarse esa columna en la rejilla angosta (ver [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md)).

---

## 7. Búsqueda

La **barra de búsqueda** (la lupa: texto libre + operadores, ver [lupa.md](lupa.md)) ocupa el ancho disponible en móvil. El botón de **ayuda de operadores** sigue accesible y su panel se ajusta para no salirse de la pantalla. La búsqueda en sí —qué se busca, los operadores, el debounce— es idéntica; solo cambia que el campo y su panel de ayuda se adaptan al ancho.

---

## 8. Selección múltiple y paginación que conviven

Dos piezas se compactan en móvil para que **quepan a la vez** sin romper el diseño:

- La **barra de acciones masivas** (marcar leído / no leído, mover a papelera, marcar spam, etc. — ver [acciones-sobre-correos.md](acciones-sobre-correos.md)) se muestra en móvil **solo con iconos**, sin las etiquetas de texto, y el contador de seleccionados se reduce a un número. Si aún así no caben todas las acciones, la barra se puede **desplazar horizontalmente**.
- El **paginador** se compacta a **flechas anterior / siguiente** (sin sus etiquetas de texto) con un indicador **"página / total"** en medio. En escritorio se mantiene la lista completa de números de página.

Esto tiene una consecuencia de comportamiento que merece destacarse: **gracias a que ambas piezas caben a la vez en móvil**, la **selección se conserva al cambiar de página** igual que en escritorio. Si la barra de acciones o el paginador no cupieran, el usuario tendría que elegir entre seleccionar o paginar; compactando ambas se evita ese compromiso. El tope de cuántos correos se pueden seleccionar no cambia (vive en [listado-de-correos.md](listado-de-correos.md) / [acciones-sobre-correos.md](acciones-sobre-correos.md)).

---

## 9. Ajustes: navegación que se vuelve horizontal

- El área de **Ajustes** (ver [ajustes.md](ajustes.md)) tiene en escritorio su propio **menú lateral** (Tu cuenta, Cuentas conectadas, Bandejas, Idioma, Datos, Acerca de). En móvil ese menú se convierte en una **barra de pestañas horizontal deslizable** (con scroll lateral) encima del contenido de cada sección. El contenido de cada sección no cambia; solo su navegación pasa de columna a fila.
- Dentro de **Cuentas conectadas**, la rejilla de **tarjetas de cuenta** se **apila en una sola columna** en móvil (en escritorio se mantiene en fila).

---

## 10. Inicio de sesión, crear bandeja, formularios y aspecto general

- Las pantallas de **login** y **crear bandeja** son en escritorio de **dos columnas** (panel de marca decorativo + formulario). En móvil se muestra **solo el formulario**, centrado y a pantalla completa; el **panel de marca se oculta**. No cambia nada del inicio de sesión ni del onboarding (ver [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md)) — solo se deja de pintar el panel decorativo cuando no hay sitio.
- Los **formularios y diálogos** (crear / editar bandeja ficticia, confirmar borrado de cuenta de usuario, confirmar cierre del compositor, error de envío de adjuntos, etc.) ajustan sus anchos, paddings y la disposición de sus botones para caber y ser cómodos en móvil. Los diálogos **pequeños de confirmación siguen centrados** (no van a pantalla completa); solo los **visores y el formulario largo** de bandeja ficticia ocupan toda la pantalla en móvil.
- Se reducen los **márgenes laterales** y el **tamaño de los títulos** en móvil para aprovechar el espacio, recuperando los valores de escritorio por encima del corte.

---

## 11. Qué NO cambia

- **Ninguna funcionalidad:** los mismos correos, cuentas, búsquedas, borradores, adjuntos, favoritos, bandejas ficticias y ajustes, con el mismo comportamiento.
- **Ningún contrato con el servidor:** no hay endpoints nuevos, ni cambios en peticiones / respuestas, ni nuevas llamadas a Gmail/Outlook. El backend **no se tocó**.
- **El aspecto de escritorio:** por encima del corte, permanece **idéntico píxel a píxel**.
- **Idioma, autenticación, sincronización y demás flujos:** intactos.

La lista exhaustiva de lo que esta entrega **deliberadamente deja fuera** (app nativa / PWA, modo sin conexión, gestos táctiles, notificaciones push, scroll horizontal de la tabla, etc.) está en [../limits/diseno-responsive-movil.md](../limits/diseno-responsive-movil.md).

---

## 12. Resumen en una frase

> Por debajo de un único punto de corte de ancho, toda la interfaz de MailManager adopta una disposición móvil táctil —barra superior con hamburguesa y un cajón de navegación deslizante que se cierra al navegar, botón flotante para redactar, compositor y visores de correo / conversación a pantalla completa, listados como tarjetas de dos líneas (ocultando "Para"/"De"), búsqueda y barra de acciones compactadas, paginador reducido a flechas con indicador "página/total" (de modo que selección y paginación siguen cabiendo a la vez), navegación de Ajustes y de cuenta como pestañas horizontales deslizables, tarjetas de cuenta apiladas y pantallas de login/crear-bandeja sin su panel de marca— mientras que por encima del corte el escritorio queda idéntico píxel a píxel y **nada del backend ni del contrato con el servidor cambia**; la cifra del corte y todo lo que no soporta viven en [../limits/diseno-responsive-movil.md](../limits/diseno-responsive-movil.md).
