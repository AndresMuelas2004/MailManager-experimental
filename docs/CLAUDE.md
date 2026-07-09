# Reglas Generales del Directorio de Documentación

Este es el `CLAUDE.md` del **directorio `docs/`**. Es la referencia general de qué contiene este directorio, quiénes son sus lectores y cómo debe escribirse cada fichero dentro de él. Todo aspecto tratado aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que adopte este directorio.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer el mismo directorio de documentación desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier otro documento dentro de `docs/`, este fichero tiene precedencia. El `CLAUDE.md` raíz siempre prevalece sobre este fichero.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio futuro de reglas pasa por una nueva versión de este fichero.

## 1. Propósito

`docs/` es el hogar de la **documentación narrativa, específica del proyecto, dirigida al equipo de ingeniería y a los futuros mantenedores** — no a los usuarios finales, ni a Claude como fuente de reglas arquitectónicas.

El directorio está organizado por subdirectorio, uno por cada tipo de documentación. Hoy contiene:

- `features/` — descripciones a nivel de comportamiento de features individuales (un fichero Markdown por feature).

Se pueden añadir nuevos subdirectorios cuando un tipo distinto de documentación necesite un hogar (por ejemplo decisiones de arquitectura, runbooks, post-mortems). Todo subdirectorio nuevo debe seguir el mismo espíritu establecido aquí: prosa narrativa, escrita para humanos, complementaria a (nunca sustituta de) los ficheros `CLAUDE.md` y `*_guide.md` que viven junto al código.

## 2. Audiencia

La audiencia principal es el **equipo de ingeniería y los futuros mantenedores** — personas que necesitan entender una feature, una decisión o un procedimiento sin bucear por cada fichero que lo implementa. Esto no es documentación de cara al usuario, y no es el lugar donde Claude busca reglas arquitectónicas o patrones de implementación.

## 3. Alcance — Qué Pertenece Aquí

- Descripciones a nivel de comportamiento de features (bajo `features/`): disparadores, condiciones, casos límite y resultados observables.
- Limitaciones aceptadas (especialmente en trabajo con alcance MVP): qué se deja fuera **deliberadamente** y por qué.
- Decisiones y trade-offs cuya justificación no es visible en el propio código.
- Ejemplos concretos de entradas emparejadas con el comportamiento resultante.
- Interacciones entre features o entre áreas que es fácil pasar por alto al leer cualquier módulo aislado.

### Qué NO pertenece aquí

- Reglas arquitectónicas o a nivel de capa → esas viven en el `CLAUDE.md` de la capa correspondiente.
- Detalles de implementación específicos del proyecto que reflejan el código uno a uno → esos viven en el `*_guide.md` correspondiente.
- Esquemas de petición/respuesta de la API, códigos de error o contratos de wire → esos viven junto al código (`schemas/`, definiciones de clases de error, etc.).
- Especificaciones de tests → esas viven en los ficheros de test.
- Cualquier línea que se limite a parafrasear el nombre de una función, un campo o una firma — si se pudriese en silencio en el momento en que el código se renombra, no paga sus tokens.

## 4. Estilo de Escritura

- **El comportamiento primero, no la implementación primero.** Describe lo que el usuario (u otro sistema) experimenta, no cómo está cableado el código internamente.
- **Prosa natural.** Habla a un compañero de equipo, no a un compilador. Se fomentan los encabezados, las listas con viñetas y los párrafos cortos; la notación densa estilo API no.
- **Ejemplos concretos.** Muestra entradas y el comportamiento resultante siempre que afile una regla.
- **Haz explícitos los trade-offs.** «Qué dejamos fuera y por qué» suele ser la parte más valiosa del documento.
- **Las referencias al código se ganan su sitio.** Apuntar a una ruta de fichero o a una función está bien cuando señala con precisión el origen de un comportamiento no obvio; en otro caso, la prosa es mejor.
- **Un tema por fichero.** Si un documento empieza a cubrir dos temas no relacionados, divídelo.

## 5. En Qué Se Diferencia Esto de Otra Documentación

| Documento                           | Audiencia principal          | Estilo                                                 |
|-------------------------------------|------------------------------|--------------------------------------------------------|
| `CLAUDE.md` raíz                    | Claude + mantenedores        | Reglas arquitectónicas supremas                        |
| `CLAUDE.md` de capa (p. ej. `api/`) | Claude + mantenedores        | Reglas estructurales agnósticas del proyecto           |
| `*_guide.md` (p. ej. `api_guide.md`)| Claude + mantenedores        | Reglas de implementación específicas del proyecto      |
| `docs/<area>/<topic>.md`            | Equipo y nuevos colaboradores| Descripción narrativa de una feature/decisión/runbook  |

Regla general: si quitar el documento del repositorio dejara a un nuevo colaborador incapaz de entender **qué se supone que hace algo o por qué se tomó una decisión** sin leer la implementación, el documento pertenece aquí. Si quitarlo solo ocultara una regla arquitectónica o de implementación, pertenece a un `CLAUDE.md` o a un `*_guide.md` en su lugar.

## 6. Nombrado de Ficheros e Idioma

- Un fichero Markdown por tema. El nombre del fichero coincide con el identificador corto del tema en minúsculas (p. ej. `lupa.md`, `attachments.md`, `email-search.md`). Usa kebab-case si el identificador necesita más de una palabra.
- Los ficheros dentro de `docs/` pueden escribirse en **cualquier idioma que use el equipo**, dado que los lectores son colaboradores humanos. Se exige consistencia dentro de un mismo fichero — no mezcles idiomas dentro de un documento.
- Este `CLAUDE.md`, como todo `CLAUDE.md` y `*_guide.md` del repositorio, puede escribirse en español.

## 7. Mantenimiento y Autoridad

- Añade un fichero nuevo cuando se lance una nueva feature, se tome una decisión significativa o se necesite un runbook, y cualquiera de sus detalles sea lo bastante no trivial como para merecer una explicación narrativa.
- Actualiza un fichero existente siempre que cambie la feature, decisión o procedimiento subyacente — incluso pequeños cambios de UX (tiempo de debounce, número mínimo de caracteres, orden de los resultados, límites de alcance) pertenecen al fichero. Un documento obsoleto es peor que uno inexistente.
- No borres un fichero a menos que el propio tema se elimine de la aplicación. Si el fichero está «desactualizado», actualízalo; no lo elimines.
- Un documento bajo `docs/` tiene la misma autoridad que un `*_guide.md`: cuando el código lo contradice, el documento es la fuente de verdad y el código es lo que debe cambiar. A la inversa, cuando el documento ya no refleja el comportamiento deseado, debe actualizarse como parte del mismo cambio que alteró el tema subyacente — nunca dejarse para «luego».
