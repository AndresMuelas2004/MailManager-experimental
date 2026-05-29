# Borradores — límites y topes (MVP)

Catálogo cuantitativo de **hasta dónde llega** el ciclo de vida de los borradores: topes de sincronización, reintentos, longitudes de campo, paginación y la lista de "lo que NO soporta". El comportamiento y los flujos están en **[../features/borradores.md](../features/borradores.md)**.

Los límites de **adjuntos** de un borrador (tamaño por archivo, total del mensaje, número de adjuntos, blocklist de extensiones, atomicidad de la subida) NO se repiten aquí — son compartidos y viven en **[adjuntos.md](./adjuntos.md)**. Las validaciones del **envío directo** de un correo nuevo (mínimos de asunto/cuerpo/destinatarios, reintentos del envío directo) viven en **[composicion-y-envio.md](./composicion-y-envio.md)**. Esta página solo enlaza a ellos.

---

## 1. Tope de la sincronización (fetch desde el proveedor)

| Concepto | Valor exacto | Notas |
|---|---|---|
| Borradores que se bajan por cuenta y sincronización | **100** (los más recientes) | Mismo tope en Gmail y Outlook. Es un tope de **bajada**, no de visualización ni de cuántos puede tener la cuenta. |
| Tamaño de página de Outlook al paginar borradores | **100** por página (`$top=100`) | Outlook ordena por fecha de modificación descendente (`$orderby=lastModifiedDateTime desc`). Se pagina hasta llegar al tope de 100. |
| Tamaño de página de Gmail al listar ids de borradores | hasta **500** por página (o lo que reste hasta 100) | Gmail lista ids paginando y luego pide cada borrador completo; el tope efectivo total sigue siendo 100. |
| ¿Se incluye el cuerpo en la sincronización? | **Sí** | A diferencia del listado de correos (donde el cuerpo se baja solo al abrir), la sincronización de borradores trae el cuerpo completo de cada borrador. |

Asimetría de "los más recientes":

- **Outlook**: orden explícito y garantizado por la API (por fecha de última modificación).
- **Gmail**: orden **por convención** (cronológico inverso observado); la API de borradores de Gmail no expone parámetro de ordenación. Si esa convención dejara de cumplirse, la semántica del tope de 100 se rompería. Es una dependencia frágil, documentada como tal.

Asimetría de marcas de tiempo (afecta al orden del listado local, §5): la `Message` de Gmail **no** expone una marca de tiempo estable de creación/modificación del borrador, así que al sincronizar se sella `created_at` = `updated_at` = "ahora" (momento de la sincronización). Outlook sí devuelve `createdDateTime` / `lastModifiedDateTime` reales y se persisten tal cual. Consecuencia observable: tras sincronizar, el orden por `created_at DESC` de los borradores Gmail refleja el orden en que Gmail los devolvió en esa sincronización, no su cronología real de creación; para Outlook sí es cronología real.

---

## 2. Semántica de la sincronización (replace, no merge)

| Regla | Comportamiento exacto |
|---|---|
| Modo | **Espejo (replace)** por cuenta, atómico (upsert + borrado de ausentes en la misma transacción). |
| Borradores ausentes en el proveedor | Se **borran** de la base local. Un borrador eliminado en Gmail/Outlook web desaparece en MailManager tras sincronizar. |
| Metadata de respuesta/reenvío (threading) | Se **preserva** la local cuando la sincronización trae esos campos vacíos. El proveedor no expone el threading como propiedad del borrador, así que cada sincronización los pasa vacíos; sin la preservación, se borrarían en cada refresco. |
| Alcance | Una cuenta concreta, o **todas** las del mailbox si se entra en la vista de mailbox. |
| Errores por cuenta (vista mailbox) | Se acumulan por cuenta; **no abortan** la sincronización de las demás. |

---

## 3. Reintentos

| Operación | ¿Reintenta? | Intentos totales | Espera entre intentos | Qué errores se reintentan |
|---|---|---|---|---|
| **Enviar borrador** (Gmail) | Sí | **3** | **1 s → 2 s** (escalado lineal `1 s × nº de intento`; el 3.er intento no espera) | Solo `429` y `5xx` (`429`, `500`, `502`, `503`, `504`) |
| **Enviar borrador** (Outlook) | Sí | **3** | **1 s → 2 s** (escalado lineal `1 s × nº de intento`; el 3.er intento no espera) | **Cualquier** error externo del proveedor (su superficie de error es más estrecha) |
| **Sincronizar — página de Outlook** | Sí | **5** (1 intento + **4** reintentos) | **1 s** fijo | Errores externos transitorios del proveedor por página |
| **Sincronizar — lote de Gmail** | Sí | **5** (1 intento + **4** reintentos) por lote | **1 s** fijo | Fallos transitorios del lote |
| **Crear / editar / borrar** borrador | **No** (disparo único) | 1 | — | Un fallo se reporta de inmediato |

Notas:

- El número de intentos de **envío** (3) y el de **sincronización** (5) son **independientes**: tunear uno no mueve el otro.
- El envío de borradores —con o sin adjuntos— pasa **siempre** por el mismo camino unificado (`send_draft_with_attachments`); no hay un camino "texto plano" con espera fija aparte. Por eso el escalado `1 s → 2 s` aplica por igual a un borrador vacío de adjuntos y a uno con adjuntos. Con 3 intentos totales solo hay **2** esperas (tras el 1.er y el 2.º intento); el 3.er intento falla o tiene éxito sin esperar, así que la espera máxima es de 2 s, nunca 3 s.
- Gmail distingue dentro del `429`: el `429` con motivo "user-rate limit exceeded (mail sending)" es el **tope diario de envío** y NO se reintenta (reintentar dentro de la ventana solo quema cuota). Detalle en [composicion-y-envio.md](./composicion-y-envio.md).

---

## 4. Validaciones y longitudes de los campos del borrador

| Campo | Regla exacta | Dónde se aplica |
|---|---|---|
| Destinatarios "Para" / "Cc" / "Cco" | Listas **opcionales** (pueden ir vacías) | Contrato del borrador (a diferencia del envío directo, que exige ≥ 1 en "Para") |
| Asunto | **Opcional**, sin mínimo ni máximo | Contrato del borrador |
| Cuerpo | **Opcional**, sin mínimo ni máximo; **texto plano** | Contrato del borrador |
| `reply_kind` (tipo de respuesta) | Solo `reply`, `reply_all` o `forward` (o vacío) | Validado en backend (Pydantic) y a nivel de columna (CHECK) |
| Forma de cada dirección de email | Regex permisiva (`local@dominio.tld`), **solo cliente** | El backend del borrador no revalida la forma; ver [composicion-y-envio.md](./composicion-y-envio.md) |

Tope técnico de almacenamiento de la metadata de threading (no visible para el usuario, solo informativo): el identificador del mensaje original y el `In-Reply-To` se guardan en columnas acotadas; la cadena `References` se guarda sin tope práctico.

---

## 5. Listado y selección

| Concepto | Valor exacto |
|---|---|
| Tope de la **lectura** local de borradores | **Ninguno** — devuelve todos los borradores en la base local. El único tope es el de la sincronización (§1). |
| Orden del listado | Por fecha de **creación** descendente (más reciente primero). |
| Casilla "seleccionar" de cabecera | Selecciona los **50** borradores más recientes (tope de selección masiva). |
| Borrado en bloque | Sin tope explícito de cuántos a la vez; cada borrado es independiente y tolera fallos parciales. Pide confirmación previa. |

---

## 6. Códigos de error relevantes

| Situación | Código / estado HTTP |
|---|---|
| Cuenta de origen no encontrada en el mailbox | `account_not_found` / **404** |
| Borrador no encontrado (editar / enviar / borrar / adjuntar) | `draft_not_found` / **404** |
| Borrar un borrador que ya no existe localmente | **404** (no es error de servidor) |
| Fallo del proveedor al crear borrador | error de creación de borrador / **502** |
| Fallo del proveedor al editar borrador | error de actualización de borrador / **502** |
| Fallo del proveedor al enviar borrador | error de envío de borrador / **502** |
| Fallo del proveedor al sincronizar | error de sincronización de borradores / **502** |
| Sesión/permisos de la cuenta caducados | error de cuenta no conectada / **409** |
| Payload inválido (p. ej. `reply_kind` fuera de `reply`/`reply_all`/`forward`, o `reply_to_account_id` con forma de UUID inválida) | validación / **422** |

El detalle técnico de un `502` (clase de excepción, error concreto del proveedor) queda **solo en los logs del servidor**, nunca en pantalla.

> Nota — los esquemas del borrador (`DraftCreate` / `DraftUpdate`) **no** usan `extra="forbid"` (ni en Pydantic ni en el Zod del frontend), a propósito, por compatibilidad con clientes antiguos. Un **campo extra no esperado se ignora en silencio**, no produce 422. El 422 solo salta por un **valor** inválido de un campo conocido (p. ej. `reply_kind` no permitido) o un tipo incorrecto. Esto contrasta con las bandejas ficticias, que sí rechazan claves desconocidas — ver [bandejas-ficticias.md](../features/bandejas-ficticias.md).

---

## 7. Lo que NO soporta (limitaciones aceptadas del MVP)

| No soportado | Porqué breve |
|---|---|
| **Cuerpo rich-text / formato en el borrador** | El cuerpo es un `<textarea>` plano y el borrador se guarda y se manda como `text/plain`. Pegar HTML guarda el marcado en crudo. Un editor enriquecido introduciría un campo de formato aparte (no revivir el antiguo `body_html`); fuera del MVP. |
| **Bajar más de 100 borradores por cuenta** | La sincronización se queda con los 100 más recientes. Borradores antiguos más allá de ese tope no se traen. Suficiente para el MVP; subir el tope multiplicaría las llamadas al proveedor. |
| **Cambiar la cuenta de origen de un borrador existente** | Implicaría mover el borrador y sus adjuntos a otra cuenta del proveedor. La app bloquea el selector; la alternativa es descartar y empezar de cero. |
| **Merge en la sincronización** | La sincronización es un espejo del proveedor (replace): no fusiona estados ni resuelve conflictos campo a campo. El proveedor es la fuente de verdad. |
| **Editar el threading de una respuesta/reenvío desde el cliente** | Los campos de threading se leen de la fila local en el envío; el cliente los omite a propósito. Evita que un cliente manipulado reescriba el hilo. Ver [responder-y-reenviar.md](../features/responder-y-reenviar.md). |
| **Reintento automático al crear / editar / borrar** | Solo el **envío** y la **sincronización** reintentan. Crear/editar/borrar son disparos únicos; ante fallo transitorio el usuario reintenta a mano. |
| **Autoguardado periódico del borrador** | No hay autosave cada N segundos; guardar es una acción explícita (salvo el bootstrap silencioso que solo crea el contenedor al adjuntar, no guarda el contenido). |
| **Programar envío de un borrador (send later)** | No hay envío diferido; "Enviar" manda de inmediato. |
| **Orden de borradores garantizado en Gmail** | Gmail no expone ordenación en su API de borradores; el orden "más recientes" se apoya en una convención observada, no garantizada. |
| **Indicadores "este borrador responde a X" en el inbox** | La metadata de threading se persiste de forma defensiva (para re-hidratar el borrador), pero no alimenta ninguna pista en el visor; reservado a fases futuras. |

Si los usuarios reportan necesitar algo de lo anterior, hay un plan de fases futuras para añadirlo.
