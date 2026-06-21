# Funcionalidades — índice

Este directorio describe **qué hace** cada funcionalidad de MailManager y **qué experimenta** el usuario: disparadores, condiciones, casos borde y el *porqué* de las decisiones de diseño. Es documentación narrativa para el equipo y futuros mantenedores —no para usuarios finales— y sirve como catálogo de capacidades de la aplicación.

Cada documento tiene un **gemelo con el mismo nombre** en [`../limits/`](../limits/) que recoge las **cifras exactas** y la lista de "qué NO soporta". Aquí los límites solo se mencionan de pasada y se enlaza al gemelo; allí están los números. Las reglas de redacción de este directorio están en su `CLAUDE.md`.

## Índice por área

### Identidad, cuentas y buzones
- [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) — Login con Google y con Microsoft, sesión de la app, conectar / editar / desconectar cuentas de Gmail y Outlook, y el límite de frecuencia anti-abuso (rate limiting).
- [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) — Qué es un buzón, la vista de una cuenta vs. la vista unificada, y la lógica de columnas "Para" / "De".
- [ajustes.md](ajustes.md) — El área de Ajustes: identidad del usuario, gestión de cuentas (con editar etiqueta), renombrar/eliminar bandejas, idioma de la interfaz (Español/English), "sincronizar todo" y "acerca de".

### Traer y leer correo
- [sincronizacion.md](sincronizacion.md) — Copia local primero: bootstrap + incremental de correos, y reemplazo de borradores y favoritos.
- [listado-de-correos.md](listado-de-correos.md) — Las cuatro bandejas reales, el orden, la navegación por páginas numeradas, y por qué cada acción va al buzón real del correo.
- [conversaciones.md](conversaciones.md) — Vista de conversación: los mensajes de un hilo colapsados en una fila agrupada (con selección en la bandeja de cuenta y la unificada) y el visor que reconstruye la cadena completa del proveedor.
- [visualizacion-de-correos.md](visualizacion-de-correos.md) — Abrir un correo: render del HTML saneado, imágenes embebidas (`cid:`→`data:`) y caché tras la primera apertura.
- [lupa.md](lupa.md) — Búsqueda local sobre asunto y remitente (texto libre insensible a tildes) más operadores estilo Gmail (`from: to: subject: has: before: after: is: in:`) combinables con AND.
- [favoritos.md](favoritos.md) — La estrella (Gmail) / bandera (Outlook), la pestaña de Favoritos y la reconciliación con el proveedor.

### Organizar
- [acciones-sobre-correos.md](acciones-sobre-correos.md) — Marcar leído / no leído, mover a papelera, spam, borrado definitivo y acciones masivas.
- [bandejas-ficticias.md](bandejas-ficticias.md) — Bandejas virtuales con filtros guardados que agregan varias cuentas.

### Escribir y enviar
- [composicion-y-envio.md](composicion-y-envio.md) — El composer en modo "Nuevo mensaje" y el envío directo.
- [autocompletado-destinatarios.md](autocompletado-destinatarios.md) — Sugerencias de direcciones en Para/CC/CCO a partir de la gente con la que el usuario ya se ha comunicado.
- [borradores.md](borradores.md) — Crear, editar, listar, sincronizar, enviar y borrar borradores.
- [responder-y-reenviar.md](responder-y-reenviar.md) — Responder / Responder a todos / Reenviar: threading y herencia de adjuntos.

### Adjuntos
- [adjuntos.md](adjuntos.md) — Recibir y descargar adjuntos, adjuntar al componer y heredarlos al reenviar.
