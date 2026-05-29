# Límites y topes — índice

Este directorio recoge **hasta dónde llega** cada funcionalidad de MailManager: tamaños, cantidades, TTL, reintentos, concurrencia, mínimos, debounce, paginación, permisos exactos y la lista exhaustiva de "qué NO soporta" con su porqué. Es el complemento **cuantitativo** de [`../features/`](../features/).

Cada documento tiene un **gemelo con el mismo nombre** en [`../features/`](../features/) que describe el comportamiento. Aquí van las cifras; allí, los flujos. El solapamiento entre ambos es mínimo y deliberado. Las reglas de redacción de este directorio están en su `CLAUDE.md`.

## Índice por área

### Identidad, cuentas y buzones
- [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md) — Duración de sesión, permisos OAuth exactos por proveedor, validaciones de cuenta, dev login e infraestructura de despliegue.
- [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md) — Asimetrías multi-cuenta / multi-buzón y lo que la vista unificada no resuelve.

### Traer y leer correo
- [sincronizacion.md](sincronizacion.md) — Cap de bootstrap, umbral de eventos, tamaño de lote, workers, reintentos y paginación por proveedor.
- [listado-de-correos.md](listado-de-correos.md) — Tope de correos por carga, ausencia de paginación y tope de selección.
- [visualizacion-de-correos.md](visualizacion-de-correos.md) — Etiquetas, atributos, protocolos y reglas CSS permitidos, y umbrales del saneamiento.
- [lupa.md](lupa.md) — Mínimo de caracteres, debounce, tope de tokens y tope de resultados.
- [favoritos.md](favoritos.md) — Topes y asimetrías de conteo de la sincronización de favoritos.

### Organizar
- [acciones-sobre-correos.md](acciones-sobre-correos.md) — Tope de selección, lotes, política de reintentos y el alcance exacto del borrado (no-op).
- [bandejas-ficticias.md](bandejas-ficticias.md) — Criterios de filtro admitidos, validaciones y límites de la vista virtual.

### Escribir y enviar
- [composicion-y-envio.md](composicion-y-envio.md) — Validaciones mínimas del envío directo, reintentos y códigos de error.
- [borradores.md](borradores.md) — Cap de borradores por cuenta, reintentos de envío y asimetrías Gmail/Outlook.
- [responder-y-reenviar.md](responder-y-reenviar.md) — Recorte de la cita, permisos exactos, requisitos de threading y herencia de adjuntos.

### Adjuntos
- [adjuntos.md](adjuntos.md) — Tamaños (por archivo / total), número máximo, blocklist de extensiones, TTL del caché y concurrencia.
