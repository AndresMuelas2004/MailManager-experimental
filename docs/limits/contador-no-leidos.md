# Contador de correos no leídos (badge) — límites y alcance

Catálogo de **hasta dónde llega** el contador de no leídos: cifras exactas y la lista de "qué NO soporta", cada una con un porqué breve. El comportamiento narrado (dónde aparece, qué cuenta, cuándo cambia) vive en **[../features/contador-no-leidos.md](../features/contador-no-leidos.md)**.

---

## 1. Topes y valores exactos

| Concepto | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Bandejas con badge | **2**: bandeja de entrada (`ALL_MAIL`) y spam (`SPAM`) | Parámetro `box` del endpoint de conteo (por defecto `ALL_MAIL`) | Cualquier otro valor de `box` se rechaza con **422** (`ARCHIVE` incluido — Archivados no es una opción válida del contador). Son las únicas bandejas con noción de "no leído" en la UI; Enviados, Favoritos, Archivados, Borradores, Papelera y bandejas ficticias **no** tienen badge. |
| Qué se cuenta | **Mensajes individuales** sin leer (`is_read = FALSE`) en ese `box` | Backend (conteo sobre la copia local) | **No** se agrupa por conversación: a diferencia del listado en modo conversación, el contador no colapsa hilos. Por eso el badge puede superar al número de filas del listado (ver [../features/conversaciones.md](../features/conversaciones.md)). |
| Tope visual del badge | A partir de **100** se muestra **«99+»** (se abrevia cuando el número es **> 99**) | Componente de badge (frontend) | Evita romper el ancho del menú. Por debajo de 100 se muestra el número exacto. |
| Badge con valor 0 | **No se muestra** (la entrada queda sin badge; nunca aparece un «0») | Componente de badge (frontend) | El badge se oculta solo cuando el conteo es **≤ 0**. |
| Formato del título de la pestaña | **«(N) MailManager»** con sin leer; **«MailManager»** con 0 | Título del documento (frontend), buzón actual, bandeja de entrada | `N` usa el mismo abreviado: **«(99+) MailManager»** por encima de 99. El valor estático de respaldo del título (antes de calcular nada) también es «MailManager». |
| Superficies que muestran el badge | **2 dentro de la app** + **1** el título de la pestaña | Menú lateral en la vista unificada (Bandeja unificada, Spam) y dentro de una cuenta (Bandeja de entrada, Spam); título del navegador | Es el mismo menú lateral en ambos ámbitos: solo cambia si el número cuelga del total del buzón o del desglose de la cuenta activa. |
| Ámbito del conteo del menú lateral | **Total del buzón** en la vista unificada; **desglose de la cuenta activa** dentro de una cuenta | Backend: `total` del buzón + lista `accounts[]` | Al seleccionar una cuenta, el mismo badge pasa de usar el `total` a usar el `unread` de esa cuenta del desglose. |
| Desglose por cuenta | **Todas** las cuentas del buzón, incluidas las que tienen **0** | Backend: lista `accounts[]` con un `unread` por cuenta | Una sola respuesta por `(buzón, box)` alimenta a la vez el total del menú (vista unificada) y el badge por cuenta (dentro de una cuenta). |
| Llamadas al proveedor para calcular el conteo | **0** | Backend (Services → Database) | Igual que el listado y la lupa: lee solo de la base de datos local; no gasta cuota de Gmail/Outlook ni requiere permisos OAuth nuevos. |
| Frescura del conteo (cuándo se recalcula) | **Automática** tras cada acción que cambia el estado de lectura; reutiliza la caché del frontend dentro de la ventana global de frescura | Caché de datos del frontend | El conteo se invalida y se vuelve a pedir junto con los listados al abrir un correo, marcar leído/no leído (incluido en bloque), abrir una conversación, mover a papelera/spam y sincronizar. La ventana de frescura es la **global** del frontend — su valor exacto está en [listado-de-correos.md](listado-de-correos.md). |
| Migración de base de datos | **Ninguna** | — | No añade columnas ni tablas; cuenta sobre `email_metadata`, que ya existe. |
| Índice dedicado | **Ninguno** | Backend | El conteo se apoya en el índice por cuenta ya existente (`idx_email_metadata_account_id`), mismo compromiso de MVP que los listados filtrados y las sugerencias de destinatarios ([autocompletado-destinatarios.md](autocompletado-destinatarios.md)). |

---

## 2. Lo que el contador NO soporta (y por qué)

- **No cuenta conversaciones, solo mensajes.** No hay un modo "contar hilos no leídos". *Por qué:* el badge responde a «cuántos correos sin leer», alineado con Gmail/Outlook; agrupar por hilo aquí confundiría el número con el del listado, que sí agrupa.
- **No hay badge en Enviados, Favoritos, Archivados, Bandejas ficticias, Borradores ni Papelera.** *Por qué:* ninguna tiene una noción natural y única de "no leído" como bandeja activa — los enviados y borradores son del propio usuario, Favoritos/Papelera/ficticias son vistas transversales, y Archivados es por definición correo ya despachado fuera de la bandeja de entrada (igual que la Papelera, no lleva contador). Es una decisión de producto.
- **El número es el de la copia local, no el del buzón en vivo del proveedor.** Correo nuevo sin leer que aún no se ha sincronizado **no** cuenta todavía. *Por qué:* el contador comparte fuente con el listado (lectura local instantánea y resistente a caídas del proveedor); la frescura la aporta la sincronización, no el conteo.
- **No es un contador en tiempo real ni hace "polling".** No se actualiza solo si nadie hace nada en la app; se recalcula al actuar (abrir, marcar, mover, sincronizar) o al volver a la vista pasada la ventana de frescura. *Por qué:* evita gasto continuo; las acciones del usuario y la sincronización son los disparadores naturales.
- **No añade notificaciones de sonido, del sistema operativo ni push.** *Por qué:* eso pertenece a la funcionalidad de **Notificaciones** (feature aparte); aquí el único "aviso pasivo" es el título de la pestaña «(N) MailManager», con el que esa feature se coordina.
- **No marca correos como leídos ni cambia el estado de lectura.** Solo lo refleja. *Por qué:* el contador es de **solo lectura** sobre el estado; las acciones que cambian «leído/no leído» son las ya existentes ([../features/acciones-sobre-correos.md](../features/acciones-sobre-correos.md)).
- **No persiste preferencias ni es configurable.** No se puede desactivar, cambiar el umbral del «99+» ni elegir qué bandejas llevan badge. *Por qué:* alcance de MVP; no introduce ninguna preferencia nueva de usuario.
- **Un fallo del conteo no produce error visible: el badge simplemente no aparece.** *Por qué:* el badge es accesorio; la página tiene sus propias consultas con su propio manejo de error, y un conteo fallido no debe degradar la vista (se comporta como 0).

---

## 3. Enlaces

- Comportamiento del contador: [../features/contador-no-leidos.md](../features/contador-no-leidos.md)
- Listado de correos (misma copia local, ventana de frescura, bandejas): [../features/listado-de-correos.md](../features/listado-de-correos.md) · [./listado-de-correos.md](./listado-de-correos.md)
- Conversaciones (por qué el badge supera a las filas): [../features/conversaciones.md](../features/conversaciones.md)
- Acciones sobre correos (leído/no leído, papelera, spam): [../features/acciones-sobre-correos.md](../features/acciones-sobre-correos.md)
- Sincronización (de dónde llega el correo nuevo sin leer): [../features/sincronizacion.md](../features/sincronizacion.md)
- Buzones y vista unificada (cuenta vs. buzón): [../features/buzones-y-vista-unificada.md](../features/buzones-y-vista-unificada.md)
