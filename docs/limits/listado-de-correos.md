# Listado de correos (inbox) — límites y alcance

Catálogo de **hasta dónde llega** el listado de correos: topes con cifras exactas y la lista de "qué NO soporta", cada una con un porqué breve. El comportamiento narrado (flujos, UX, decisiones) vive en **[../features/listado-de-correos.md](../features/listado-de-correos.md)**.

---

## 1. Topes y valores exactos

| Concepto | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Tamaño de página (correos por página) | **50 correos** | Constante `EMAILS_PAGE_SIZE` (frontend) → `limit` que envía el frontend | Fija e idéntica para toda lista de correos: bandeja real, unificada, de cuenta, favoritos, ficticia y resultados de búsqueda. |
| Valor por defecto de `limit` en el backend | **50** | Router (`limit` por defecto, rango `1`–`500`) | Antes era 200; ahora coincide con el tamaño de página del frontend. |
| Tope técnico de `limit` (máximo aceptado por el backend) | **500 correos** | Validación del parámetro `limit` (rango `1`–`500`) | El backend acepta hasta 500 si alguien lo pidiera, pero el frontend siempre envía 50 — no expone forma de subirlo. |
| Desplazamiento de paginación (`offset`) | Por defecto **0**; mínimo **0** | Backend; lo calcula el frontend como `(página − 1) × 50` | Es el mecanismo real de la paginación numerada (ver § 2). |
| Número de página (`?page=`) | **1-based**; cualquier valor inválido (ausente, no numérico, ≤ 0) se normaliza a **1** | Parámetro de URL del frontend | El backend no conoce `page`: solo recibe `limit`/`offset`. La página 1 se omite de la URL (no se escribe `?page=1`). |
| Total devuelto (`total`) | **Conteo exacto del conjunto filtrado completo** en la copia local | Campo `total` del envoltorio `EmailPageOut` (backend) | Es el tamaño de TODO el conjunto (mismo `box`/`q`/`favorite`/cuentas), no el de la página; refleja solo lo **sincronizado**, nunca el buzón en vivo del proveedor. |
| Ventana de números de página visibles | Primera + última + **±2** alrededor de la actual; el resto, puntos suspensivos | Barra de paginación (frontend) | Con **7 páginas o menos** se muestran todas sin puntos suspensivos. |
| Orden de resultados | `received_at` **descendente**, con desempate fijo por `(account_id, provider_message_id)` | Backend (misma consulta para bandeja real y ficticia) | El desempate hace el orden **total** y determinista; sin él, `OFFSET` produciría duplicados/saltos entre páginas. Es la garantía que hace segura la paginación por posición. |
| Mínimo de caracteres de búsqueda | **2** | Parámetro `q` (rango `2`–`200`) | Detalle propio de la lupa — ver [lupa.md](lupa.md). |
| Tope de tokens de búsqueda | **10** palabras | Parámetro `q` | Detalle propio de la lupa — ver [lupa.md](lupa.md). |
| Ventana de frescura del caché (vista normal) | **30 000 ms (30 s)** | Caché de datos del frontend (global) | Dentro de la ventana, volver a la bandeja reutiliza la lista cargada en lugar de re-pedirla. Cada **página** se cachea por separado (la página forma parte de la clave de caché). |
| Frescura del caché (bandejas ficticias) | **0 ms** (siempre se vuelve a pedir al montar) | Caché del frontend, solo para vistas ficticias | Override deliberado; ver [bandejas-ficticias.md](bandejas-ficticias.md). |
| Tope de selección "seleccionar todo" | **50 correos** (los de la página actual) | Selección en la UI para acciones en bloque | La casilla de cabecera marca como mucho la página visible (50 = tamaño de página). Es un límite de **selección por página**, no de carga; la selección **persiste entre páginas**. Detalle en [acciones-sobre-correos.md](acciones-sobre-correos.md). |
| Llamadas al proveedor para construir la lista o pasar de página | **0** | Backend | El listado lee solo de la base de datos local (Services → Database); pasar de página **no** dispara sincronización ni llamada al proveedor. |
| Bandejas navegables | **4** (`ALL_MAIL`, `SENT`, `SPAM`, `TRASH`) | Parámetro `box` (obligatorio) | El estado interno "eliminado definitivamente" **no** es navegable y nunca aparece en una lista. |
| Columnas de personas mostradas | **1** en vista de cuenta, **2** en vista unificada | Tabla del frontend | Asimetría deliberada para no repetir el correo propio del usuario (ver § 3). |
| Destinatarios "Para" mostrados por fila | **1** (solo el destinatario principal) | Tabla del frontend + metadata | Solo se almacena/enseña el primer `To`; CC/BCC no se sincronizan en metadata. |

---

## 2. Lo que el listado NO soporta (y por qué)

- **No hay scroll infinito ni botón "cargar más".** La navegación es por **páginas numeradas** (Anterior/Siguiente + números + "X–Y de Z"), no por scroll continuo. *Por qué:* es el modelo de Gmail/Outlook web; con un total exacto y saltos directos a página, da una experiencia más predecible que el scroll infinito.
- **El total "Z" es el de la copia local, no el del buzón real del proveedor.** Si el proveedor tiene 50 000 correos pero la app solo sincronizó los más recientes, el indicador muestra el total **sincronizado**. *Por qué:* la paginación numerada se construye sobre la copia local; ni Gmail ni Outlook ofrecen un total exacto en vivo (ver § 5).
- **No hay "cargar correos más antiguos" desde el proveedor al paginar.** La paginación recorre lo que ya está en local; al llegar a la última página no se baja más histórico de Gmail/Outlook. *Por qué:* pasar de página no debe gastar cuota del proveedor; bajar histórico es trabajo de la sincronización, no del listado. Recuperar algo más antiguo no sincronizado queda fuera de este alcance (igual que hoy), como posible ampliación futura.
- **No se puede reordenar la lista.** El orden es siempre fecha descendente; no hay ordenación por remitente, asunto, tamaño ni relevancia. *Por qué:* el orden cronológico inverso es el esperado en un cliente de correo y evita complejidad de UI/consulta innecesaria en el MVP.
- **No hay ranking por relevancia.** Ni siquiera con búsqueda activa: la fecha es el único criterio. *Por qué:* ver [../limits/lupa.md](../limits/lupa.md).
- **El listado no consulta al proveedor en vivo.** Muestra la última foto sincronizada, que puede ir por detrás del buzón real hasta que termina la sincronización en segundo plano. *Por qué:* listado instantáneo y resistente a caídas del proveedor; la frescura la aporta la sincronización, no el render de la lista.
- **No se filtra por más de un `box` a la vez en una bandeja real.** Cada vista enseña exactamente una de las cuatro bandejas; no hay una vista "todo junto incluyendo papelera y spam". *Por qué:* la clasificación es excluyente (un correo está en una sola bandeja) y mezclar papelera/spam con el resto confundiría. Las vistas multi-criterio son terreno de las bandejas ficticias ([bandejas-ficticias.md](bandejas-ficticias.md)).
- **Los correos eliminados permanentemente no aparecen en ninguna lista.** Quedan marcados como definitivamente eliminados, y ese estado no es una bandeja navegable. *Por qué:* para el usuario están borrados; conservar la fila marcada (en vez de borrarla) responde a necesidades de la gestión de papelera, no del listado.
- **El clip de adjuntos puede tardar en aparecer.** Un correo con adjuntos descargables que nadie ha abierto todavía puede mostrarse **sin** clip justo después de sincronizar; el clip se enciende al abrirlo por primera vez. *Por qué:* estrategia "lazy pura" para no gastar cuota descubriendo adjuntos en cada sincronización (ver [adjuntos.md](adjuntos.md)).
- **La columna "Para" no muestra la lista completa de destinatarios** ni CC/BCC: solo el destinatario principal. *Por qué:* la tabla tiene una sola columna "Para" y CC/BCC no se sincronizan en metadata; es una simplificación de visualización, no pérdida de datos.

---

## 3. Asimetrías Gmail vs Outlook relevantes para el listado

- **Clasificación en bandejas (`box`).** La prioridad de asignación es **`TRASH` > `SPAM` > `SENT` > resto = `ALL_MAIL`**, idéntica para ambos proveedores, pero parte de orígenes distintos: en Gmail de **etiquetas** (un correo puede llevar varias a la vez) y en Outlook de **carpetas**. El resultado para el usuario es el mismo: cada correo cae en una sola bandeja navegable.
- **Restaurar desde spam** deja el correo en `ALL_MAIL` (la bandeja principal), no en una carpeta "Recibidos" específica, coherente con que todo lo no especial es `ALL_MAIL`. (Detalle de la acción en [acciones-sobre-correos.md](acciones-sobre-correos.md).)
- **Estabilidad del identificador del correo.** No afecta al orden ni al render, pero sí a las acciones por fila: Outlook reasigna el identificador de un correo al moverlo entre carpetas (papelera, spam), mientras Gmail lo conserva. El listado almacena siempre el identificador vigente, por eso una acción disparada desde la lista acierta el correo aunque su id haya cambiado tras un movimiento previo.

---

## 4. Enlaces

- Comportamiento del listado: [../features/listado-de-correos.md](../features/listado-de-correos.md)
- Búsqueda de texto libre (lupa): [../features/lupa.md](../features/lupa.md) · [./lupa.md](./lupa.md)
- Favoritos: [../features/favoritos.md](../features/favoritos.md)
- Acciones sobre correos (leído, papelera, spam, selección en bloque): [../features/acciones-sobre-correos.md](../features/acciones-sobre-correos.md)
- Adjuntos (clip, estrategia lazy): [../features/adjuntos.md](../features/adjuntos.md) · [./adjuntos.md](./adjuntos.md)
- Bandejas ficticias: [../features/bandejas-ficticias.md](../features/bandejas-ficticias.md)
