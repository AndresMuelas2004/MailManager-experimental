# Límites del refresco manual y el estado de sincronización

Catálogo cuantitativo del control de actualización de las bandejas: cada cuánto avanza solo el texto "hace X", los tramos de redondeo del tiempo relativo, dónde y con qué clave se guarda la marca de "última actualización", y la lista de "qué NO soporta" con su porqué breve.

El **comportamiento** (el botón, el aviso de error, la asimetría de la bandeja ficticia, el alcance por vista) está en **[../features/refrescar-y-estado-sincronizacion.md](../features/refrescar-y-estado-sincronizacion.md)**. Aquí solo van los números y los límites.

Todos estos valores son del **frontend** (la funcionalidad no tiene backend propio: reutiliza la sincronización de [sincronizacion.md](sincronizacion.md), cuyos topes de volumen, reintentos y concurrencia siguen siendo los del proveedor y NO se repiten aquí). Los topes del límite de frecuencia (rate limiting) que puede devolver "No se pudo actualizar" están en [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md).

---

## 1. Cifras del control

| Aspecto | Valor | Dónde aplica | Detalle |
|---------|-------|--------------|---------|
| Refresco visual del texto "hace X" | **cada 30 s** | El control en las tres vistas | Un temporizador del propio control recalcula el texto relativo cada 30 segundos. Es **solo visual**: no lanza ninguna sincronización ni va al proveedor. 30 s basta porque, pasada la primera tanda de segundos, el texto ya cruza a minutos. |
| Auto-refresco periódico (sincronización) | **ninguno** | — | No hay temporizador que sincronice cada N minutos (ver § 4). |
| Idiomas del texto relativo | **es / en** | El texto "hace X" / "X ago" | Se genera con `Intl.RelativeTimeFormat(lang, { numeric: 'auto' })`, siguiendo el idioma de la interfaz. `numeric: 'auto'` produce formas idiomáticas (en español "ayer" en vez de "hace 1 día"). |

---

## 2. Tramos del tiempo relativo

El texto "Última actualización: hace X" redondea el tiempo transcurrido a la unidad más grande que no llegue al siguiente escalón:

| Tiempo transcurrido | Unidad mostrada | Ejemplo (es) | Ejemplo (en) |
|---------------------|-----------------|--------------|--------------|
| Menos de 1 minuto | **segundos** | "hace 30 segundos" / "ahora" | "30 seconds ago" / "now" |
| 1 minuto a < 1 hora | **minutos** | "hace 5 minutos" | "5 minutes ago" |
| 1 hora a < 1 día | **horas** | "hace 2 horas" | "2 hours ago" |
| 1 día o más | **días** | "ayer" (a 1 día) | "yesterday" / "2 days ago" |

### Notas sobre los tramos

- **Se redondea hacia abajo (floor)**: 25 horas se muestran como "ayer" (1 día), no como "1 día y 1 hora".
- **El tiempo futuro se recorta a cero**: si la marca quedara por delante de la hora actual (desfase de reloj, o una marca escrita por otra pestaña), se muestra "ahora" / "now" en lugar de un valor negativo.
- **No hay tramo de semanas / meses / años**: a partir de un día, todo se cuenta en días. En la práctica una vista recién abierta siempre se ha sincronizado hace segundos, así que los tramos altos son poco frecuentes.
- Antes de la primera sincronización del ámbito, el texto no es relativo sino fijo: **"Sin sincronizar todavía"** / "Not synced yet".

---

## 3. Dónde se guarda la marca de "última actualización"

| Aspecto | Valor | Detalle |
|---------|-------|---------|
| Almacenamiento | **`localStorage` del navegador** | No se persiste en el servidor; no es una columna de la cuenta ni un campo de ninguna respuesta de la API. |
| Prefijo de la clave | **`lastSync:`** | Cada entrada se guarda como `lastSync:<ámbito>`. |
| Valor guardado | **época en milisegundos** (entero) | Momento de la última sincronización correcta. Un valor ausente o no numérico se trata como "sin marca". |
| Ámbito — bandeja unificada / vista por cuenta | **`emails:<mailboxId>:<accountId>`** | El `accountId` es **`ALL`** en la vista unificada del buzón (todas las cuentas) y el id de la cuenta en la vista por cuenta. Independiente de la caja (Recibidos/Enviados/Spam/Papelera), de la búsqueda, del filtro de favoritos y de la página. |
| Ámbito — bandeja ficticia | **`vmbox:<virtualMailboxId>`** | Una marca por bandeja virtual, independiente de la búsqueda y de la página. |

### Notas sobre la persistencia

- **La marca solo avanza en una sincronización correcta.** Un fallo del proveedor (4xx/5xx) deja la marca intacta. **Excepción**: la bandeja ficticia sincroniza con un abanico tolerante a fallos parciales, así que su marca avanza en cuanto el intento termina aunque alguna cuenta haya fallado por debajo (ver el comportamiento en el gemelo § 5.1).
- **Es por dispositivo/navegador y por ámbito**, nunca global: hay tantas marcas como buzones, cuentas y bandejas ficticias el usuario haya abierto en ese navegador.
- **Un fallo de `localStorage`** (modo privado, cuota llena) se traga silenciosamente: la marca en memoria de esa sesión sí se actualiza, pero no sobrevive a recargar la página.

---

## 4. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Detalle | Por qué |
|------------|---------|---------|
| **Auto-refresco periódico** | No hay temporizador que sincronice cada N minutos; solo se sincroniza al abrir la vista o al pulsar el botón | Se respeta el modelo reactivo de [../features/sincronizacion.md](../features/sincronizacion.md) § 2.4. El único refresco automático es el **visual** del texto (cada 30 s), que no sincroniza. |
| **Marca compartida entre dispositivos** | La "última actualización" vive en `localStorage`; sincronizar desde otro equipo no se refleja aquí | Es un dato puramente local de visualización; persistirlo en el servidor exigiría infraestructura que el MVP no justifica. |
| **Marca global única** | No existe una "última sincronización" del usuario entero; cada vista recuerda la suya | Una sincronización es por buzón/cuenta/bandeja ficticia, no del usuario; una marca global mentiría sobre las vistas no sincronizadas. |
| **Control de refresco en Favoritos** | La página de Favoritos conserva solo su botón "Sincronizar favoritos"; no recibe este control | "Reconciliar estrellas" y "buscar correo nuevo" son acciones distintas; dos botones juntos confundirían (ver [../features/favoritos.md](../features/favoritos.md)). |
| **Aviso de error en la bandeja ficticia** | La bandeja ficticia no muestra "No se pudo actualizar" y avanza la marca aunque una cuenta falle | Su sincronización tolera fallos parciales por diseño; frenarse o avisar por la cuenta más débil contradiría el valor de agregar lo que sí se pudo traer. |
| **Refrescar todas las bandejas a la vez** | El botón refresca solo la vista visible | El "sincronizar todo" del buzón entero ya existe en Ajustes → Datos (ver [ajustes.md](ajustes.md)); duplicarlo aquí mezclaría alcances. |

---

> El control llega hasta: **texto relativo que se refresca solo cada 30 s** (sin sincronizar), en **es/en**, con tramos **segundos < 1 min, minutos < 1 h, horas < 1 día, días a partir de ahí** (futuro recortado a "ahora"); la marca de "última actualización" se guarda en **`localStorage`** bajo `lastSync:emails:<mailboxId>:<accountId|ALL>` o `lastSync:vmbox:<virtualMailboxId>`, **por dispositivo y por vista**, avanzando solo en sincronización correcta — sin auto-refresco periódico, sin marca compartida entre dispositivos ni global, y sin control en Favoritos. El comportamiento completo está en [../features/refrescar-y-estado-sincronizacion.md](../features/refrescar-y-estado-sincronizacion.md).
