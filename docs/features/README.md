# Funcionalidades — índice

Este directorio describe **qué hace** cada funcionalidad de MailManager y **qué experimenta** el usuario: disparadores, condiciones, casos borde y el *porqué* de las decisiones de diseño. Es documentación narrativa para el equipo y futuros mantenedores —no para usuarios finales— y sirve como catálogo de capacidades de la aplicación.

Cada documento tiene un **gemelo con el mismo nombre** en [`../limits/`](../limits/) que recoge las **cifras exactas** y la lista de "qué NO soporta". Aquí los límites solo se mencionan de pasada y se enlaza al gemelo; allí están los números. Las reglas de redacción de este directorio están en su `CLAUDE.md`.

## Índice por área

### Identidad, cuentas y buzones
- [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) — Login con Google y con Microsoft, sesión de la app, conectar / editar / desconectar cuentas de Gmail y Outlook, el límite de cuentas conectadas por usuario, y el límite de frecuencia anti-abuso (rate limiting).
- [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) — Qué es un buzón, la vista de una cuenta vs. la vista unificada, y la lógica de columnas "Para" / "De".
- [ajustes.md](ajustes.md) — El área de Ajustes: identidad del usuario, gestión de cuentas (con editar etiqueta), renombrar/eliminar bandejas, idioma de la interfaz (Español/English), "sincronizar todo" y "acerca de".

### Traer y leer correo
- [sincronizacion.md](sincronizacion.md) — Copia local primero: descarga masiva inicial en paralelo con auto-reintento, incremental de correos, reemplazo de borradores y captura automática de favoritos.
- [refrescar-y-estado-sincronizacion.md](refrescar-y-estado-sincronizacion.md) — El botón "Refrescar" y el texto "Última actualización: hace X" en la cabecera de las bandejas: refresco manual a demanda de la vista visible y visibilidad de cuándo fue la última sincronización.
- [listado-de-correos.md](listado-de-correos.md) — Las bandejas reales navegables (incluida Archivados), el orden, la navegación por páginas numeradas, y por qué cada acción va al buzón real del correo.
- [ordenar-y-filtrar-listado.md](ordenar-y-filtrar-listado.md) — Ordenar el listado de las bandejas reales por fecha / remitente / asunto (asc o desc) y filtrar de un clic por no leídos / con adjuntos / destacados, combinables y compatibles con la búsqueda.
- [contador-no-leidos.md](contador-no-leidos.md) — El badge numérico de correos sin leer en el menú lateral (según el ámbito activo: buzón completo o una cuenta) y el título del navegador; cuenta mensajes individuales (no conversaciones) de la copia sincronizada y se actualiza solo.
- [conversaciones.md](conversaciones.md) — Vista de conversación: los mensajes de un hilo colapsados en una fila agrupada (con selección en la bandeja de cuenta y la unificada) y el visor que reconstruye la cadena completa del proveedor.
- [visualizacion-de-correos.md](visualizacion-de-correos.md) — Abrir un correo: render del HTML saneado, imágenes embebidas (`cid:`→`data:`), proxy de privacidad para las imágenes remotas, y caché (en base de datos y en la memoria del navegador) para abrir al instante.
- [lupa.md](lupa.md) — Búsqueda local sobre asunto y remitente (texto libre insensible a tildes) más operadores estilo Gmail (`from: to: subject: has: before: after: is: in:`) combinables con AND.
- [favoritos.md](favoritos.md) — La estrella (Gmail) / bandera (Outlook), la pestaña de Favoritos y la captura automática del estado desde el proveedor en cada sincronización.

### Organizar
- [acciones-sobre-correos.md](acciones-sobre-correos.md) — Marcar leído / no leído, mover a papelera, spam, archivar / desarchivar, borrado definitivo y acciones masivas.
- [bandejas-ficticias.md](bandejas-ficticias.md) — Bandejas virtuales con filtros guardados que agregan varias cuentas.

### Escribir y enviar
- [composicion-y-envio.md](composicion-y-envio.md) — El composer en modo "Nuevo mensaje" y el envío directo.
- [autocompletado-destinatarios.md](autocompletado-destinatarios.md) — Sugerencias de direcciones en Para/CC/CCO a partir de la gente con la que el usuario ya se ha comunicado.
- [borradores.md](borradores.md) — Crear, editar, listar, sincronizar, enviar y borrar borradores.
- [responder-y-reenviar.md](responder-y-reenviar.md) — Responder / Responder a todos / Reenviar: threading y herencia de adjuntos.
- [firma.md](firma.md) — Firma por cuenta conectada con formato (editada en Ajustes), que se inserta sola y editable en el cuerpo al redactar, responder y reenviar.

### Adjuntos
- [adjuntos.md](adjuntos.md) — Recibir y descargar adjuntos, adjuntar al componer y heredarlos al reenviar.

### Experiencia e interfaz
- [diseno-responsive-movil.md](diseno-responsive-movil.md) — Disposición móvil por debajo de 1024 px: barra superior con cajón de navegación, botón flotante de redactar, compositor y visores a pantalla completa, listado como tarjetas, paginador compacto y demás ajustes de aspecto (sin cambios de backend).
- [paginas-publicas.md](paginas-publicas.md) — La landing de marketing en la raíz del sitio (anónimos la ven; los autenticados entran a su correo como siempre) y las páginas legales públicas de privacidad y términos, bilingües con detección inglés-por-defecto para navegadores no españoles y selector EN/ES que comparte la preferencia de idioma de la app.
