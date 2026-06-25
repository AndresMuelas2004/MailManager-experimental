# Límites y topes — índice

Este directorio recoge **hasta dónde llega** cada funcionalidad de MailManager: tamaños, cantidades, TTL, reintentos, concurrencia, mínimos, debounce, paginación, permisos exactos y la lista exhaustiva de "qué NO soporta" con su porqué. Es el complemento **cuantitativo** de [`../features/`](../features/).

Cada documento tiene un **gemelo con el mismo nombre** en [`../features/`](../features/) que describe el comportamiento. Aquí van las cifras; allí, los flujos. El solapamiento entre ambos es mínimo y deliberado. Las reglas de redacción de este directorio están en su `CLAUDE.md`.

## Índice por área

### Identidad, cuentas y buzones
- [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) — Duración de sesión, permisos OAuth exactos por proveedor (incluidos los scopes y el desfase de reloj del login con Microsoft), validaciones de cuenta, dev login, los topes del rate limiting (buckets, exenciones y Retry-After) e infraestructura de despliegue.
- [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) — Asimetrías multi-cuenta / multi-buzón y lo que la vista unificada no resuelve.
- [ajustes.md](ajustes.md) — Longitud de nombres (renombrar bandeja, etiqueta de cuenta), versión fija de la app, persistencia del idioma (clave/valores/precedencia), el endpoint `PATCH` de renombrar bandeja y lo que el panel NO soporta.

### Traer y leer correo
- [sincronizacion.md](sincronizacion.md) — Cap de bootstrap, umbral de eventos, tamaño de lote, workers, reintentos y paginación por proveedor.
- [refrescar-y-estado-sincronizacion.md](refrescar-y-estado-sincronizacion.md) — Cada cuánto avanza solo el texto "hace X", los tramos de tiempo relativo, dónde y con qué clave se guarda la marca de "última actualización", y qué NO soporta el control.
- [listado-de-correos.md](listado-de-correos.md) — Tamaño de página, paginación numerada (límite/offset, total exacto) y tope de selección.
- [contador-no-leidos.md](contador-no-leidos.md) — Bandejas con badge, qué se cuenta (mensajes, no hilos), tope visual «99+», formato del título, superficies, y por qué no lleva índice ni migración.
- [conversaciones.md](conversaciones.md) — Reglas de agregación de la fila-conversación, tamaño de lote del hilo por proveedor, asimetrías Gmail/Outlook y códigos de error del visor.
- [visualizacion-de-correos.md](visualizacion-de-correos.md) — Etiquetas, atributos, protocolos y reglas CSS permitidos, umbrales del saneamiento, y el TTL y la pre-carga del caché de contenido.
- [lupa.md](lupa.md) — Mínimo de caracteres, debounce, topes de tokens y de operadores, catálogo cerrado de operadores y formatos de fecha aceptados.
- [favoritos.md](favoritos.md) — Topes y asimetrías de conteo de la sincronización de favoritos.

### Organizar
- [acciones-sobre-correos.md](acciones-sobre-correos.md) — Tope de selección, lotes, política de reintentos (incluido archivar/desarchivar), códigos de error y el alcance exacto del borrado (no-op).
- [bandejas-ficticias.md](bandejas-ficticias.md) — Criterios de filtro admitidos, validaciones y límites de la vista virtual.

### Escribir y enviar
- [composicion-y-envio.md](composicion-y-envio.md) — Validaciones mínimas del envío directo, tope de tamaño del cuerpo, allowlist del saneador de texto enriquecido, reintentos y códigos de error.
- [autocompletado-destinatarios.md](autocompletado-destinatarios.md) — Mínimo de caracteres, debounce, número de sugerencias, exclusiones y qué NO soporta.
- [borradores.md](borradores.md) — Cap de borradores por cuenta, reintentos de envío y asimetrías Gmail/Outlook.
- [responder-y-reenviar.md](responder-y-reenviar.md) — Recorte de la cita, permisos exactos, requisitos de threading y herencia de adjuntos.

### Adjuntos
- [adjuntos.md](adjuntos.md) — Tamaños (por archivo / total), número máximo, blocklist de extensiones, TTL del caché y concurrencia.

### Experiencia e interfaz
- [diseno-responsive-movil.md](diseno-responsive-movil.md) — Punto de corte exacto (1024 px) y reflujo secundario (640 px), tamaños fijos de la chrome móvil (cajón, barra superior, botón flotante, pantalla completa), qué se oculta/transforma, la cadena nueva y lo que NO soporta.
