# Diseño responsive / uso en móvil — límites y alcance

Este documento cataloga **hasta dónde llega** la adaptación responsive de MailManager: el punto de corte exacto y los pocos tamaños fijos de la chrome móvil, más la lista de "lo que deliberadamente NO hace" con un porqué breve de cada limitación.

El **comportamiento** (la barra superior y el cajón, el botón flotante, los visores y el compositor a pantalla completa, las tarjetas del listado, el paginador compacto, etc.) se describe en **[../features/diseno-responsive-movil.md](../features/diseno-responsive-movil.md)**. Aquí solo están las cifras y los límites.

> Nota: casi todos los topes que el usuario *experimenta* en móvil **no son propios** de esta funcionalidad, sino de la funcionalidad que se está re-disponiendo. El tamaño de página y el tope de selección viven en [listado-de-correos.md](listado-de-correos.md) y [acciones-sobre-correos.md](acciones-sobre-correos.md); los tamaños de adjuntos en [adjuntos.md](adjuntos.md); las reglas del saneamiento del cuerpo HTML en [visualizacion-de-correos.md](visualizacion-de-correos.md). Esta entrega **no cambia ninguno** de esos números. Aquí solo se recogen las cifras **propias** del layout responsive.

---

## 1. El punto de corte

| Límite | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Punto de corte de disposición (escritorio ↔ móvil) | **1024 px** de ancho de ventana | Toda la interfaz (prefijo `lg:` de Tailwind) | **Por debajo de 1024 px** se activa la chrome móvil; **a 1024 px y por encima**, el escritorio idéntico de siempre. Es un único umbral para el cambio de disposición global. |
| Umbral secundario de reflujo de contenedores | **640 px** de ancho de ventana | Solo unos pocos contenedores internos (prefijo `sm:`) | No es un segundo "modo": es el ancho al que **se apilan las tarjetas de cuenta** en Cuentas conectadas, y al que ciertos formularios pasan campos de fila a columna (el formulario de bandeja ficticia, la cabecera de la página de Bandejas ficticias). Por debajo de 640 px se apilan; entre 640 y 1024 px ya están en fila pero el resto de la chrome sigue en modo móvil. |

> Las tablets en vertical (típicamente < 1024 px) reciben la disposición **móvil**, por decisión de diseño a favor de la simplicidad y el uso táctil. No hay una disposición intermedia "tablet".

---

## 2. Tamaños fijos de la chrome móvil

Valores fijos que solo existen en el modo móvil (por debajo de 1024 px). Por encima del corte rige la geometría de escritorio (entre paréntesis, para contraste).

| Elemento | Valor en móvil | En escritorio (≥ 1024 px) |
|---|---|---|
| Cajón de navegación (ancho) | **280 px**, limitado a un máximo del **85 % del ancho de la ventana** (`max-w-[85vw]`) | Columna lateral fija de **260 px** |
| Compositor | **Pantalla completa** (alto `100dvh`, ancho completo) | Ventana acoplada de **400 px** de ancho en la esquina inferior derecha |
| Visor de correo y visor de conversación | **Pantalla completa** (alto completo, esquinas rectas, sin padding del fondo) | Ventana modal centrada (máx. **90 vh** de alto, esquinas redondeadas) |
| Barra superior móvil (hamburguesa + nombre de bandeja) | Alto **56 px**; no existe en escritorio | — (la navegación es la barra lateral) |
| Botón flotante de redactar (FAB) | Círculo de **56 px**, anclado a **24 px** del borde inferior y derecho; no existe en escritorio | — (el botón de redactar vive en la barra lateral) |

---

## 3. Qué se oculta o se transforma por debajo del corte

Catálogo de los elementos que cambian de forma o desaparecen en móvil (todos vuelven a su forma de escritorio a partir de 1024 px):

| Elemento | Comportamiento en móvil (< 1024 px) |
|---|---|
| Barra lateral de navegación | Se oculta y pasa a ser un **cajón deslizante** sobre fondo oscurecido; se abre con la hamburguesa de la barra superior. |
| Cabeceras de columna del listado (Remitente/Para/De/Asunto/Fecha) | **Ocultas.** Cada fila se convierte en una **tarjeta de dos líneas** (remitente + fecha arriba; asunto con indicadores abajo). |
| Columnas "Para" y "De" del listado | **Ocultas** (no caben). Su dato sigue disponible al abrir el correo. |
| Números de página y elipsis del paginador | **Ocultos.** En su lugar, flechas anterior/siguiente (sin etiqueta de texto) + indicador **"página/total"**. |
| Etiquetas de texto de la barra de acciones masivas | **Ocultas** (solo iconos). El contador de seleccionados se reduce a un número. La barra puede **desplazarse horizontalmente** si no caben todas las acciones. |
| Panel de marca de Login y de Crear bandeja | **Oculto.** Se muestra solo el formulario, centrado y a pantalla completa. |
| Menú lateral de Ajustes | Se transforma en una **barra de pestañas horizontal deslizable** (con el título "Ajustes" oculto en móvil). |
| Pestañas de una cuenta (Recibidos/Enviados/…) | **Fila deslizable horizontalmente** (scroll lateral). |
| Rejilla de tarjetas de cuenta | Se **apila en una sola columna** (por debajo de 640 px; ver § 1). |
| Títulos de página | Se reducen de tamaño (el tamaño grande se recupera a partir de 1024 px). |
| Márgenes laterales de las páginas | Se reducen (los márgenes de escritorio se recuperan a partir de 1024 px). |

> Cuáles diálogos van a pantalla completa y cuáles no: **solo** los dos visores (correo y conversación) y el **formulario largo de bandeja ficticia** ocupan toda la pantalla en móvil. Los **diálogos pequeños de confirmación** (cerrar sesión, eliminar cuenta de usuario, confirmar cierre del compositor, error de envío de adjuntos, etc.) **siguen centrados** y solo ajustan anchos y paddings.

---

## 4. Cadenas de interfaz nuevas

| Clave | Español | English | Uso |
|---|---|---|---|
| `nav.openMenu` | "Abrir menú" | "Open menu" | Etiqueta accesible del botón hamburguesa de la barra superior móvil. |

Es la **única** cadena de interfaz que añade esta entrega. El resto de etiquetas (cerrar, redactar, paginación, acciones masivas, etc.) ya existían y se reutilizan; en móvil simplemente se ocultan visualmente cuando el espacio aprieta, sin perder su versión accesible.

---

## 5. Lo que NO soporta (y por qué)

| No soporta | Porqué breve |
|---|---|
| **App nativa (iOS/Android)** | Esta entrega es **adaptación responsive de la web**, no una aplicación nativa. Fuera de alcance. |
| **PWA instalable** | No hay manifiesto de instalación ni *service worker*: no se puede "instalar" en la pantalla de inicio como app. Fuera del MVP. |
| **Modo sin conexión** | Sin *service worker* ni almacenamiento offline; la app requiere red, igual que en escritorio. |
| **Gestos táctiles avanzados** | No hay deslizar-para-archivar / deslizar-para-borrar ni gestos similares. La interacción es por toque y desplazamiento estándar. |
| **Notificaciones push** | No hay notificaciones del sistema de correo nuevo. Fuera de alcance. |
| **Scroll horizontal de la tabla de escritorio como alternativa en móvil** | En móvil el listado **siempre** son tarjetas apiladas; no se ofrece la tabla de columnas con desplazamiento lateral como opción. Decisión a favor de la legibilidad táctil. |
| **Optimización del cuerpo HTML del correo para móvil** | El cuerpo se muestra en un iframe aislado (ver [visualizacion-de-correos.md](visualizacion-de-correos.md)); su HTML viene de terceros y, si la plantilla no es responsive, puede requerir desplazamiento dentro del propio marco. MailManager no reescribe ese contenido. |
| **Una disposición intermedia "tablet"** | El corte de disposición es único (1024 px): las tablets en vertical reciben la disposición móvil. No hay un tercer layout. |
| **Recordar / forzar un "modo móvil"** | La disposición la decide el ancho de la ventana vía CSS en cada momento; no hay preferencia que el usuario pueda fijar ni que se persista (ni en el navegador ni en el servidor). |

---

## 6. Dónde viven los límites de las funcionalidades re-dispuestas

Para no duplicar cifras, los topes que el usuario *experimenta* en móvil pero que **no pertenecen** a esta funcionalidad están en sus propios catálogos:

- **Tamaño de página y paginación numerada** (límite/offset, total exacto) → [listado-de-correos.md](listado-de-correos.md).
- **Tope de selección y lotes de las acciones masivas** → [acciones-sobre-correos.md](acciones-sobre-correos.md).
- **Tamaños y números de adjuntos** (por archivo / total / máximo) → [adjuntos.md](adjuntos.md).
- **Reglas del saneamiento y render del cuerpo del correo en su iframe** → [visualizacion-de-correos.md](visualizacion-de-correos.md).
- **Mínimo de caracteres, debounce y operadores de la búsqueda** → [lupa.md](lupa.md).

---

## 7. Resumen

> La disposición cambia en un **único punto de corte de 1024 px** (prefijo `lg:` de Tailwind), con un reflujo secundario menor a 640 px (`sm:`) solo para apilar tarjetas de cuenta y reordenar un par de formularios. La chrome móvil añade tamaños fijos propios —cajón de 280 px (máx. 85 vw), barra superior y botón flotante de redactar de 56 px, compositor y visores a pantalla completa frente a los 400 px / modal de escritorio— y oculta o transforma cabeceras de columna, columnas "Para"/"De", números de página, etiquetas de la barra de acciones, paneles de marca y menús laterales. La única cadena nueva es `nav.openMenu`. Queda fuera de alcance: app nativa, PWA, offline, gestos, push, scroll horizontal de la tabla y cualquier "modo móvil" persistido. El comportamiento completo está en [../features/diseno-responsive-movil.md](../features/diseno-responsive-movil.md).
