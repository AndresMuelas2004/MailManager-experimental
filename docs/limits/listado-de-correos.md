# Listado de correos (inbox) — límites y alcance

Catálogo de **hasta dónde llega** el listado de correos: topes con cifras exactas y la lista de "qué NO soporta", cada una con un porqué breve. El comportamiento narrado (flujos, UX, decisiones) vive en **[../features/listado-de-correos.md](../features/listado-de-correos.md)**.

---

## 1. Topes y valores exactos

| Concepto | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Tope de resultados por carga (efectivo) | **200 correos** | Lo que el frontend pide y muestra | El frontend nunca envía un tope explícito, así que rige el valor por defecto del backend (200). |
| Tope de resultados por carga (máximo aceptado por el backend) | **500 correos** | Validación del parámetro `limit` (rango `1`–`500`) | El backend acepta hasta 500 si alguien lo pidiera, pero el frontend del MVP siempre usa el defecto de 200. |
| Desplazamiento de paginación (`offset`) | Por defecto **0**; mínimo **0** | Backend | Aceptado por el backend (≥ 0) pero **el frontend nunca lo usa** → no hay paginación real (ver § 2). |
| Orden de resultados | `received_at` **descendente**, con desempate fijo por `(account_id, provider_message_id)` | Backend (misma consulta para bandeja real y ficticia) | El desempate hace el orden **total** y determinista; sin él, OFFSET produciría duplicados/saltos entre páginas. |
| Mínimo de caracteres de búsqueda | **2** | Parámetro `q` (rango `2`–`200`) | Detalle propio de la lupa — ver [lupa.md](lupa.md). |
| Tope de tokens de búsqueda | **10** palabras | Parámetro `q` | Detalle propio de la lupa — ver [lupa.md](lupa.md). |
| Ventana de frescura del caché (vista normal) | **30 000 ms (30 s)** | Caché de datos del frontend (global) | Dentro de la ventana, volver a la bandeja reutiliza la lista cargada en lugar de re-pedirla. |
| Frescura del caché (bandejas ficticias) | **0 ms** (siempre se vuelve a pedir al montar) | Caché del frontend, solo para vistas ficticias | Override deliberado; ver [bandejas-ficticias.md](bandejas-ficticias.md). |
| Tope de selección "seleccionar todo" | **50 correos** (los más recientes) | Selección en la UI para acciones en bloque | Es un límite de **selección**, no de carga ni de visualización. Detalle en [acciones-sobre-correos.md](acciones-sobre-correos.md). |
| Llamadas al proveedor para construir la lista | **0** | Backend | El listado lee solo de la base de datos local (Services → Database). |
| Bandejas navegables | **4** (`ALL_MAIL`, `SENT`, `SPAM`, `TRASH`) | Parámetro `box` (obligatorio) | El estado interno "eliminado definitivamente" **no** es navegable y nunca aparece en una lista. |
| Columnas de personas mostradas | **1** en vista de cuenta, **2** en vista unificada | Tabla del frontend | Asimetría deliberada para no repetir el correo propio del usuario (ver § 3). |
| Destinatarios "Para" mostrados por fila | **1** (solo el destinatario principal) | Tabla del frontend + metadata | Solo se almacena/enseña el primer `To`; CC/BCC no se sincronizan en metadata. |

---

## 2. Lo que el listado NO soporta (y por qué)

- **No hay scroll infinito ni botón "cargar más".** Una bandeja muestra hasta 200 correos y no hay forma de pedir la siguiente "página". *Por qué:* decisión de MVP; la vía prevista para alcanzar correos más antiguos es acotar con la lupa, no paginar. El backend ya soporta `offset`, pero el frontend no lo cablea.
- **No hay paginación visible.** Aunque el backend acepta `limit`/`offset`, el usuario no tiene controles de página. *Por qué:* mismo motivo; mantener la UI simple en el MVP.
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
