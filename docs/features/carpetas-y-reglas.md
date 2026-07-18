# Carpetas propias y reglas de organización — comportamiento (MVP)

Este documento describe **qué hace** la organización por carpetas y reglas cuando un usuario la usa, y **qué experimenta** delante de la app, sin entrar en cómo está cableado el código. Son **dos capacidades que se entregan juntas**: las **carpetas** (organizar el correo a mano) y las **reglas** (automatizar que los correos caigan solos en la carpeta correcta). Se documentan en un solo fichero porque están íntimamente acopladas: la única acción de una regla es meter un correo en una carpeta, así que una regla no significa nada sin una carpeta.

Los topes concretos (longitudes, condiciones admitidas, tamaños de página, códigos de error, permisos) y la lista exhaustiva de "qué NO soporta" viven en un documento aparte para no repetir cifras aquí: **[../limits/carpetas-y-reglas.md](../limits/carpetas-y-reglas.md)**. Este fichero solo menciona los límites de pasada y enlaza al gemelo cuando hace falta.

---

## 1. Qué es una carpeta (y en qué se diferencia de una bandeja ficticia)

Una carpeta es una **etiqueta de organización que crea el usuario** para agrupar correo: "Universidad", "Trabajo", "Facturas". Tiene tres propiedades que definen todo su comportamiento:

- **Pertenencia múltiple, como las etiquetas.** Un correo puede estar en **varias carpetas a la vez**. Meterlo en "Universidad" no lo saca de su bandeja (Recibidos, Enviados…) ni de ninguna otra carpeta: la carpeta es una **marca adicional**, no un traslado. Es ortogonal a la bandeja, igual que "leído" o "favorito".
- **Es del usuario y abarca todas sus cuentas.** Una carpeta "Universidad" es **una sola** y puede contener correos de **cualquiera** de las cuentas Gmail/Outlook conectadas a la vez — el mismo modelo "a nivel de usuario, cruza cuentas" que las [bandejas ficticias](bandejas-ficticias.md).
- **Se refleja en la cuenta real del proveedor.** Cuando el usuario mete un correo de una Gmail en "Universidad", en esa cuenta de Gmail aparece una **etiqueta** "Universidad" sobre ese correo; en una Outlook aparece una **categoría** "Universidad". Lo que el usuario organiza desde la app también queda organizado si abre Gmail/Outlook por su cuenta, y **al revés** (§ 5).

> **Carpeta ≠ bandeja ficticia.** Una [bandeja ficticia](bandejas-ficticias.md) es una *búsqueda guardada*: un correo "aparece" en ella si cumple un filtro, pero no está guardado dentro. Una carpeta es *pertenencia real*: el correo está **metido** en ella, y esa pertenencia se refleja en la cuenta del proveedor. Ambas conviven; ninguna sustituye a la otra. A una bandeja ficticia no se puede "mover" un correo; a una carpeta sí.

---

## 2. Crear, renombrar y borrar carpetas

- **Crear.** El usuario da un **nombre** (obligatorio; los espacios sobrantes se recortan, y un nombre solo de espacios se rechaza) y, opcionalmente, un **color** para distinguirla. El nombre es **único por usuario sin distinguir mayúsculas/minúsculas**: intentar crear "universidad" cuando ya existe "Universidad" se rechaza con un error claro, no crea una duplicada. Crear una carpeta **no toca todavía el proveedor**: la etiqueta/categoría se crea de forma perezosa la **primera vez** que se mete un correo en ella (§ 5.1).
- **Renombrar.** Cambiar el nombre **se refleja en la cuenta real**: la etiqueta de Gmail se renombra en el sitio; en Outlook, como el nombre de una categoría no se puede editar, la app la vuelve a etiquetar en cada correo miembro (quita la vieja, pone la nueva). El renombrado local también revalida la unicidad del nombre.
- **Recolorear.** Cambiar solo el color es **local**: no viaja al proveedor (§ 5, y ver el gemelo de límites para qué se refleja y qué no).
- **Borrar.** Borrar una carpeta **se refleja en la cuenta real** (en Gmail se elimina la etiqueta, lo que la quita de todos sus correos; en Outlook se quita la categoría de cada correo miembro) y elimina la carpeta y sus reglas asociadas en la app. **Borrar una carpeta nunca borra los correos**: solo desaparece la marca de organización. La app pide confirmación antes de borrar.

> El reflejo al proveedor de renombrar y borrar es **best-effort y por cuenta**: si una cuenta tiene el token caducado o el proveedor falla, ese fallo se registra y se salta, y el cambio **igualmente se aplica** en la app y en las demás cuentas. Además, el reflejo solo toca las cuentas donde la carpeta **se ha usado de verdad** (donde ya existe su etiqueta/categoría) — en una cuenta donde nunca se metió un correo no hay nada que renombrar ni borrar.

---

## 3. Meter y sacar correos de una carpeta

El usuario mete o saca correos de una carpeta **a mano**:

- **Desde dónde.** Desde la **lista de correos** (un menú "Carpetas" en la fila) o desde el **correo abierto**. Y de **uno en uno o varios a la vez** con la selección múltiple de la bandeja (la acción "Añadir a carpeta" de la barra de acciones en bloque).
- **Meter no mueve ni archiva.** Como la pertenencia es ortogonal a la bandeja, meter un correo en una carpeta **no** lo saca de Recibidos, ni lo archiva, ni lo manda a spam. Convive con todas las acciones que ya existen sobre un correo (leído, favorito, archivar, spam, papelera).
- **Cada acción va a la cuenta real del correo.** Como una carpeta agrega correos de varias cuentas (y una bandeja ficticia puede sacar a la superficie un correo cuya cuenta vive en otra bandeja real), meter/sacar se dirige **a la cuenta y bandeja reales de ese correo concreto**, no a la vista desde la que se mira. Es la misma garantía que aplica a marcar favorito o mover a papelera desde una vista unificada.
- **Primero el proveedor.** Meter aplica la etiqueta/categoría **en el proveedor primero** y solo si tiene éxito guarda la pertenencia local (Regla Provider-First). Si el proveedor rechaza la operación, no queda una pertenencia "fantasma" en la app. Sacar hace lo simétrico: quita la etiqueta/categoría en el proveedor y luego borra la pertenencia local.
- **Chips en cada correo.** Cada correo del listado muestra **a qué carpetas pertenece** mediante pequeñas etiquetas de color junto a la fila. Es un adorno best-effort: si su cálculo falla, el correo se muestra igual, solo sin los chips.

---

## 4. Ver una carpeta

- **En la barra lateral.** Cada carpeta aparece en el menú lateral de navegación, con un **punto de su color** (o un icono si no tiene color). Es una entrada "global": apunta a la vista unificada, igual que las bandejas ficticias.
- **La vista de la carpeta.** Al pulsarla se abre la lista de **todos los correos de esa carpeta, de todas las cuentas del usuario**, con la misma presentación que el resto de listados: **agrupación por conversación**, **paginación numerada** y la **lupa** de búsqueda (mismo texto libre y operadores estilo Gmail — ver [lupa.md](lupa.md)). El detalle de la agrupación está en [conversaciones.md](conversaciones.md); el de la paginación en [listado-de-correos.md](listado-de-correos.md).
- **Muestra los miembros estén en la bandeja que estén.** A diferencia de una bandeja ficticia —que excluye papelera, spam y archivados por defecto—, la vista de carpeta enseña sus correos miembros **en cualquier bandeja real** (Recibidos, Enviados, Spam, Papelera, Archivados): la pertenencia persiste aunque el correo se mueva de bandeja, así que un correo que metiste en "Universidad" y luego archivaste sigue apareciendo en "Universidad". La única exclusión permanente es el estado interno "borrado definitivo" (DELETED), que no aparece en ningún sitio de la app. Un operador `in:` en la lupa (p. ej. `in:inbox`) **acota** la vista a esa única bandeja.
- **Si la carpeta ya no existe.** Abrir el enlace de una carpeta borrada (por el usuario en otra pestaña, o una URL incorrecta) muestra un estado dedicado "Esta carpeta ya no existe", nunca una pantalla rota. Intentar acceder a la carpeta de otro usuario responde "no encontrada", igual que si no existiera.

---

## 5. El reflejo en la cuenta real (en los dos sentidos)

Una carpeta se materializa en cada cuenta como una **etiqueta de usuario de Gmail** o una **categoría de Outlook**, y el proveedor es la **fuente de verdad de la pertenencia**, exactamente como ocurre con los favoritos.

### 5.1 Materialización perezosa y reutilización ("adopción")

- La etiqueta/categoría de una carpeta se crea la **primera vez** que se mete un correo de esa cuenta en la carpeta (no al crear la carpeta, ni en cuentas donde nunca se usa).
- Al materializarla, si ya existe una etiqueta de Gmail o categoría de Outlook **con el mismo nombre** (sin distinguir mayúsculas), la app la **reutiliza** en lugar de duplicarla. Esto vale tanto para meter un correo a mano como para la clasificación por reglas.

### 5.2 De la app al proveedor, y del proveedor a la app

- **De la app al proveedor:** ya visto — meter/sacar, renombrar y borrar reflejan al proveedor.
- **Del proveedor a la app:** en **cada sincronización**, la app reconcilia la pertenencia de sus carpetas contra las etiquetas/categorías que el proveedor reporta. Si el usuario, por su cuenta, pone la etiqueta "Universidad" a un correo en Gmail, en la siguiente sincronización ese correo aparece dentro de la carpeta "Universidad" en la app; si la quita, la pertenencia se retira. Esta reconciliación afecta **solo a las carpetas que la app gestiona** para esa cuenta: las etiquetas del sistema y las etiquetas/categorías ajenas que el usuario ya tuviera **no** se convierten en carpetas (§ 9 y el gemelo de límites). Es best-effort: nunca rompe la sincronización.

---

## 6. Reglas: qué son y qué condiciones admiten

Una regla es una instrucción **"si se cumple una condición, mete el correo en una carpeta"**. Es la forma de que los correos caigan solos en la carpeta correcta sin arrastrarlos a mano.

- **Condición.** Se indica **una de las dos, o ambas**; si se indican las dos, **deben cumplirse ambas** (AND):
  - el **remitente** es una dirección **exacta** (coincidencia exacta de la dirección completa, sin distinguir mayúsculas — no "contiene"), y/o
  - el **asunto contiene** un texto (subcadena, insensible a mayúsculas **y a tildes**, igual que la lupa).
- **Acción.** **Meter el correo en una carpeta** concreta — la única acción de esta versión. La carpeta destino debe ser del usuario.
- **Al menos una condición.** Una regla sin ninguna condición se rechaza. Esto se valida también al editar: si un cambio dejaría la regla sin ninguna condición, se rechaza con un error de validación (ver el gemelo de límites para los códigos exactos).
- **Activar/desactivar.** Una regla se puede **activar o desactivar** sin borrarla, además de crearse, editarse y borrarse. Solo las reglas **activas** clasifican correo.

Las reglas se gestionan desde **Ajustes → Reglas**.

---

## 7. Cuándo y cómo se aplican las reglas

- **Al sincronizar, no en tiempo real.** Las reglas se evalúan **cuando la app sincroniza** el correo (al abrir una bandeja, al refrescar, o en la sincronización automática). No es instantáneo en el momento exacto en que el correo llega al proveedor, sino en la **siguiente sincronización** — que para el usuario es, en la práctica, "cuando entro a mirar el correo".
- **Solo sobre el correo nuevo de esa sincronización.** En cada sincronización, las reglas activas se evalúan sobre los correos **recién traídos** en esa pasada (no sobre toda la copia local cada vez). Cada correo que cumple una regla se mete en su carpeta destino (Provider-First, igual que meterlo a mano).
- **Varias reglas, sin conflicto.** Como un correo puede estar en varias carpetas a la vez, si **dos reglas** coinciden sobre el mismo correo, este acaba en **las dos** carpetas, sin conflicto.
- **Best-effort.** La evaluación corre después de responder la sincronización: un fallo en un correo o una regla concreta se registra y no aborta el resto ni la sincronización.

---

## 8. "Aplicar a los correos existentes"

Al **crear o editar** una regla, además de aplicarse al correo nuevo que vaya llegando, el usuario dispone de un botón **"Aplicar a los existentes"** que recorre **todo el correo ya sincronizado** (de todas sus cuentas) y clasifica el que cumpla la condición.

- **En segundo plano, con progreso.** Como puede haber mucho correo, el proceso se ejecuta **en segundo plano**: el usuario ve un indicador "Aplicando… N" que va subiendo, y puede seguir usando la app. Los listados afectados **se refrescan solos** conforme la clasificación avanza, así que los correos van apareciendo en la carpeta sin recargar.
- **Reanudable e idempotente.** El trabajo va guardando su progreso: si el servidor se reinicia a mitad, retoma donde iba sin volver a empezar, y como asignar es idempotente, aplicar dos veces no duplica pertenencias. Se puede **volver a lanzar** ("Aplicar de nuevo") en cualquier momento.
- **Comparte el motor con la descarga inicial.** Este trabajo corre en el mismo worker de segundo plano que la [descarga masiva inicial](sincronizacion.md), con la misma política de reintento automático ante fallos transitorios. Una diferencia clave: la clasificación del **correo nuevo** al sincronizar (§ 7) **no** depende de ese worker; "aplicar a los existentes" **sí** — si el worker estuviera apagado, el trabajo se queda en espera hasta que arranque.

---

## 9. Relación con lo que ya existe

- **Convive con las acciones de siempre.** Meter/sacar de carpetas se suma a marcar leído, favorito, archivar, spam y papelera. Ninguna interfiere con otra: meter en una carpeta no cambia el estado de leído ni la bandeja.
- **No sustituye a las bandejas ficticias** (§ 1). Una carpeta es pertenencia real reflejada en el proveedor; una bandeja ficticia es una búsqueda guardada calculada al vuelo.
- **No absorbe tus etiquetas/categorías previas.** La app **no importa automáticamente** como carpetas todas las etiquetas de Gmail / categorías de Outlook que el usuario ya tuviera. Gestiona las carpetas que se crean en ella (y reutiliza una etiqueta/categoría existente si coincide el nombre al materializar — § 5.1).

---

## 10. Casos borde

- **Correo aún no sincronizado.** El correo que todavía no se ha traído a la app no se clasifica hasta que se sincronice. Las reglas trabajan sobre la copia local.
- **Carpeta borrada a mitad de "aplicar".** Si la carpeta destino de una regla se borra mientras su trabajo de "aplicar a los existentes" corre, el trabajo se cierra limpiamente (no hay carpeta a la que meter). Borrar la carpeta también elimina sus reglas.
- **Cuenta con token caducado.** Durante la clasificación (por regla o "aplicar a los existentes"), una cuenta cuyo token murió falla solo **sus** correos (se registra y se sigue con las demás); el resto de cuentas del usuario se clasifican igual.
- **El mismo correo por dos cuentas.** En la vista de carpeta, igual que en una bandeja ficticia, un mismo mensaje que llega por dos cuentas se muestra una sola vez y el total cuenta conversaciones distintas (ver [conversaciones.md](conversaciones.md) y [bandejas-ficticias.md](bandejas-ficticias.md)).

---

## 11. Qué NO hace (resumen)

Para fijar expectativas (la lista completa con el porqué de cada límite y las cifras exactas está en [../limits/carpetas-y-reglas.md](../limits/carpetas-y-reglas.md)):

- **La clasificación automática no es en tiempo real**: ocurre al sincronizar, no en el instante de entrega.
- **Las condiciones de una regla solo miran remitente y asunto** — no el cuerpo del mensaje ni otros campos.
- **La única acción de una regla es meter en una carpeta** — no hay (todavía) marcar como leído, mover a papelera, reenviar, etc.
- **El color de la carpeta es una pista visual solo dentro de la app** (punto en la barra lateral y chips): en esta versión **no se envía al proveedor** — ver el gemelo de límites para el detalle exacto y una discrepancia conocida con la ayuda que muestra el formulario.
- **No se importan** automáticamente las etiquetas/categorías previas del usuario (§ 9).

---

## Resumen en una frase

> Una carpeta es una etiqueta de organización del usuario, con pertenencia múltiple y ortogonal a la bandeja, única a nivel de usuario y compartida entre todas sus cuentas, que se **refleja en la cuenta real del proveedor en los dos sentidos** (etiqueta de Gmail / categoría de Outlook, materializada de forma perezosa al primer uso y reutilizando una homónima existente); el usuario mete/saca correos a mano (Provider-First, de uno o varios a la vez, cada acción a la cuenta real del correo, sin mover ni archivar) y ve cada carpeta en la barra lateral con su listado unificado, agrupado por conversación y paginado, que muestra los miembros en cualquier bandeja salvo los borrados definitivos; y las **reglas** automatizan el llenado —"si el remitente es exacto y/o el asunto contiene, mete en tal carpeta"— evaluándose al sincronizar sobre el correo nuevo (varias reglas caben sin conflicto por la pertenencia múltiple), con un "aplicar a los existentes" en segundo plano, reanudable e idempotente; las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/carpetas-y-reglas.md](../limits/carpetas-y-reglas.md).
