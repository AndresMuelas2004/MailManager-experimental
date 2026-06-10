# Límites del autocompletado de destinatarios

Catálogo cuantitativo de **hasta dónde llega** el autocompletado de destinatarios: topes con cifras exactas y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/autocompletado-destinatarios.md](../features/autocompletado-destinatarios.md)**. Aquí solo van los números y los límites.

Todos estos valores están **hardcodeados** y aplican por igual a todos los usuarios y a ambos proveedores (Gmail y Outlook); las sugerencias se calculan **solo sobre la base de datos local** y nunca llaman al proveedor, así que ningún límite depende de cuotas externas. La búsqueda comparte la misma tokenización y la misma normalización (`unaccent` + `lower` + `ILIKE` de subcadena) que la [lupa.md](lupa.md), por lo que varios topes son intencionadamente los mismos.

---

## 1. Topes con cifras exactas

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Mínimo de caracteres para sugerir | **2** | Frontend (dispara la consulta) **y** backend (valida el parámetro `q`) | Se mide sobre el **fragmento activo** (lo que hay tras la última coma, recortado). Por debajo de 2 caracteres no se consulta y el desplegable no aparece. El backend rechaza un `q` de 1 carácter en el borde de la API. |
| Longitud máxima del fragmento (`q`) | **200 caracteres** | Backend (validación del parámetro `q`) | Un fragmento de más de 200 caracteres es rechazado en el borde de la API antes de tokenizar. |
| Debounce (pausa antes de consultar) | **250 ms** | Frontend | Tiempo que el usuario debe dejar de teclear para que salga la petición. Cada nueva pulsación reinicia el contador; solo la última cuenta. (Es 50 ms más corto que el debounce de la lupa, que son 300 ms.) |
| Máximo de palabras (tokens) por consulta | **10** | Backend (tokenización compartida con la lupa) | A partir de la 11.ª palabra, el resto se descarta **silenciosamente** (sin error ni aviso). Los espacios sobrantes no cuentan como tokens. |
| Número de sugerencias devueltas | **8** por defecto | Backend (`limit` por defecto) = valor que pide el frontend | El composer siempre pide 8. La lista se ordena por frecuencia y recencia y se corta a este número; las coincidencias que sobran no aparecen. |
| Rango admitido del parámetro `limit` | **1 a 20** | Backend (validación del parámetro `limit`) | El endpoint acepta un `limit` entre 1 y 20; fuera de ese rango devuelve error de validación. El frontend no expone forma de cambiarlo: siempre envía 8. |

### Notas sobre los topes

- **El mínimo de 2 caracteres está duplicado a propósito** en frontend y backend: el frontend evita disparar peticiones inútiles, y el backend lo valida igualmente como red de seguridad (cualquier cliente que llame directamente a la API con `q` de 1 carácter recibe un error de validación).
- **El tope de 10 palabras es silencioso** y es exactamente el mismo de la lupa (comparten la función de tokenización): pegar un párrafo entero no da error, simplemente se buscan las 10 primeras palabras.
- **Un `q` que pasa el `min_length` pero tokeniza a nada** (p. ej. solo espacios) devuelve **lista vacía con `200`**, no un error: no hay nada con qué buscar.

---

## 2. Alcance de la búsqueda (qué campos y qué datos mira)

| Aspecto | Alcance | Motivo |
|---------|---------|--------|
| Origen de los candidatos | **Remitentes de correos recibidos** (dirección + nombre) **y destinatarios de correos enviados** (dirección + nombre del primer "Para" guardado) | Es la "gente con la que el usuario ya se ha comunicado" reconstruida desde lo sincronizado. |
| Campos donde casa cada palabra | **Dirección de email y nombre**, en OR | El usuario puede teclear parte del nombre o parte de la dirección y encontrar a la misma persona. |
| Fuente de datos | **Solo la base de datos local** (PostgreSQL) | No se llama a Gmail ni a Outlook ni a su libreta de contactos; se filtra lo ya sincronizado. Por eso es instantáneo y funciona aunque el proveedor esté caído. |
| Cuentas | **Todas las cuentas conectadas del usuario** | Un único desplegable mezcla los contactos de todas sus cuentas, sin importar desde cuál redacte. Nunca incluye cuentas que no le pertenecen. |
| Boxes incluidos | **Todos menos Spam, Papelera y eliminados** | Los remitentes de Spam/Papelera suelen ser no deseados. La exclusión es **por correo**: si la dirección aparece también en un correo normal, sí entra. |
| Deduplicación | **Una entrada por dirección** (comparando en minúsculas) | Una dirección que figura en cientos de correos aparece una vez. El nombre mostrado es el **más reciente no vacío**. |
| Orden | **Frecuencia (descendente), luego recencia** | Arriba, la gente con la que más y más recientemente se ha comunicado. A igualdad, desempata la dirección alfabéticamente para que el orden sea estable. |

---

## 3. Exclusiones (qué se quita de la lista y dónde se decide)

| Exclusión | Dónde se aplica | Por qué |
|-----------|-----------------|---------|
| **Direcciones propias del usuario** (las de sus cuentas conectadas) | Backend (compara contra las direcciones de las cuentas del usuario) | No tiene sentido proponer escribirse a uno mismo. Si la dirección de una cuenta no se conoce (no se pudo leer del proveedor), esa dirección no puede excluirse por este criterio. |
| **Direcciones solo en Spam/Papelera/eliminados** | Backend (filtro de box) | Remitentes no deseados. Exclusión **por correo**, no por dirección: coexistir con un correo normal la rescata. |
| **Direcciones ya puestas en el campo activo** (las "confirmadas" antes de la última coma) | **Frontend** (filtra la lista que devuelve el backend) | El backend no sabe qué lleva escrito el usuario; devuelve todos los candidatos y la ventana descarta los que ya están en esa línea. Por eso la misma dirección puede seguir apareciendo en **otro** campo (Para vs CC vs CCO). |

---

## 4. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Ejemplo / detalle | Por qué |
|------------|-------------------|---------|
| **Libreta de contactos del proveedor** | No lee los Contactos de Google/Microsoft | Pediría datos nuevos al proveedor y permisos OAuth adicionales; fuera del MVP. Las sugerencias salen solo del correo ya sincronizado. |
| **Página/sección "Contactos" navegable** | No hay alta, edición ni borrado manual de contactos | Es memoria automática del buzón, no una agenda. El usuario no gestiona la lista. |
| **Grupos / listas de distribución / avatares** | No hay grupos ni fotos de contacto | Fuera de alcance del MVP. |
| **Ranking aprendido de la interacción** | El orden no aprende de qué sugerencias elige el usuario | El único criterio es la frecuencia/recencia de los correos ya sincronizados; no se registra qué candidato se acepta. |
| **Tolerancia a erratas (typos)** | `amapro` no encuentra `amparo` | Es subcadena literal; no hay distancia de edición ni corrección difusa (misma limitación que la [lupa.md](lupa.md)). |
| **Stemming / plurales / sinónimos** | No entiende variantes ni significados | No hay análisis lingüístico; se busca la secuencia exacta de letras. |
| **Lista de "contactos top" con el campo vacío** | Al enfocar un campo vacío no se sugiere nada | Decisión de MVP: sin texto no hay nada con qué acotar y se evita un desplegable ruidoso al abrir el composer. |
| **Inserción de "Nombre &lt;dirección&gt;"** | Solo se inserta la dirección de email | El nombre es ayuda visual para reconocer a la persona; el campo trabaja con direcciones separadas por comas. |
| **Sugerir más allá de lo sincronizado** | Un contacto de un correo que la app no bajó no aparece | Se agrega solo la copia local; no se baja histórico del proveedor para construir sugerencias. |

---

## 5. Degradación y red de seguridad (cifras de robustez)

- **Cualquier fallo o lentitud de la consulta degrada a lista vacía**, sin mensaje de error y sin bloquear el composer ni el envío: la ausencia de sugerencias es indistinguible de "no hay contactos que casen". El autocompletado es opcional por diseño.
- **La validación de forma de email no cambia**: el botón "Enviar" se sigue deshabilitando ante una dirección malformada. Esa regla (solo cliente, regex permisiva) vive en [composicion-y-envio.md](composicion-y-envio.md); el autocompletado no la sustituye ni la relaja.

---

## 6. Trampa de infraestructura

- **La insensibilidad a tildes depende de la extensión `unaccent` de PostgreSQL**, la misma que usa la lupa (migración `0020_create_extension_unaccent`). Una base de datos a la que no se le haya aplicado esa migración hace fallar la consulta de sugerencias en tiempo de ejecución (la función `unaccent(text)` no existiría). No es un límite "de producto" sino un requisito de despliegue compartido — ver [lupa.md](lupa.md) § 4.

---

> El autocompletado llega hasta: **2 caracteres mínimo, 250 ms de debounce, 10 palabras como máximo, 8 sugerencias (parámetro `limit` 1–20), una entrada por dirección con su nombre más reciente, ordenadas por frecuencia y recencia, sobre dirección/nombre de remitentes recibidos y destinatarios enviados de TODAS las cuentas del usuario en la base de datos local, excluyendo Spam/Papelera, las direcciones propias y las ya puestas en la línea** — y deliberadamente no lee la libreta del proveedor, no ofrece agenda editable, ni tolerancia a erratas/plurales/sinónimos, ni ranking aprendido, sin que un fallo rompa nunca el envío. El comportamiento completo está en [../features/autocompletado-destinatarios.md](../features/autocompletado-destinatarios.md).
