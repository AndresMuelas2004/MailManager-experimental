# Reglas del Subdirectorio de Documentación de Límites

Este es el `CLAUDE.md` del **subdirectorio `docs/limits/`**. Define cómo deben escribirse los documentos cuantitativos de «hasta dónde llega» que hay en esta carpeta. Se carga bajo demanda solo cuando se trabaja con ficheros de aquí, y **complementa** al `CLAUDE.md` padre [`../CLAUDE.md`](../CLAUDE.md) — nunca debe repetir lo que el padre ya dice.

**Agnóstico del proyecto por diseño.** Nada aquí nombra una feature o dominio concretos. Toda regla se transfiere a cualquier proyecto que adopte el patrón de documentación en dos niveles comportamiento/límites.

**Precedencia.** El `CLAUDE.md` raíz prevalece sobre este fichero, y el padre `docs/CLAUDE.md` prevalece sobre este fichero. Este fichero solo gobierna `docs/limits/`.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio futuro de reglas pasa por una nueva versión de este fichero.

## 1. Qué es esta carpeta

Un fichero Markdown por feature: el **complemento cuantitativo** de `features/`. Contiene topes exactos, tamaños, TTLs, reintentos, concurrencia, mínimos, debounce, paginación, permisos/scopes exactos, y la lista exhaustiva de «qué NO soporta» con un breve *por qué* para cada omisión deliberada. Audiencia: el equipo de ingeniería y los futuros mantenedores.

## 2. La separación comportamiento / límites (espejo de `features/`)

- `limits/<slug>.md` contiene las **cifras y los límites**; `features/<slug>.md` contiene el **comportamiento**. Mismo slug, emparejamiento **1:1**, nunca una mitad sin la otra.
- **Pon las cifras aquí, no en `features/`.** Este es el único sitio donde se pretende que aparezca como número un valor concreto (un tope de tamaño, un número de reintentos, un timeout, un mínimo).
- **No vuelvas a narrar el comportamiento aquí.** Un documento de límites es un catálogo, no una descripción de flujo. Si te encuentras explicando *cómo* funciona algo paso a paso, eso pertenece al gemelo de comportamiento — enlaza a él en su lugar.
- Mantén el solapamiento con el gemelo mínimo y deliberado.

## 3. Enlaces cruzados

- Al gemelo de comportamiento: `[../features/<slug>.md](../features/<slug>.md)`.
- A un documento de límites hermano: `[<other-slug>.md](<other-slug>.md)`.
- Una cifra que pertenece a otra feature vive en el documento de límites de **esa** feature — enlaza a él, no copies el número.
- Nunca dejes un enlace roto.

## 4. Forma de un documento de límites

- Abre con una frase de una línea sobre qué cubre el catálogo y un puntero a su gemelo de comportamiento.
- Prefiere **tablas** (`Límite | valor exacto | dónde aplica | nota`) y listas con viñetas antes que prosa.
- Incluye una sección dedicada que liste **«qué NO soporta»**, con un breve *por qué* por cada elemento.
- Toda cifra debe ser el valor **exacto** tomado del código, con contexto suficiente para localizar dónde aplica.
- Idioma: coincide con los demás documentos que ya hay en esta carpeta; un idioma por fichero (padre `docs/CLAUDE.md` § 6).

## 5. Fuente de verdad — las cifras deben ser exactas

Estos documentos se tratan como correctos cuando el código discrepa (`CLAUDE.md` raíz § 9). Un número equivocado aquí desviará a un futuro revisor hacia «arreglar» código que estaba bien. **Verifica cada cifra contra la implementación** cuando la escribas o edites; nunca copies un número de otro documento sin confirmarlo antes en el código.

## 6. Añadir o cambiar límites

- Nueva feature → crea el documento de límites junto con su gemelo de comportamiento, y añade ambos a los índices `README.md`.
- Un tope/número cambiado → actualízalo aquí en el mismo cambio. Si una cifra se ha colado en el gemelo de comportamiento, muévela de vuelta aquí y deja allí solo una mención de pasada más un enlace.
