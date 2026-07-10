# Reglas del Subdirectorio de Documentación de Features

Este es el `CLAUDE.md` del **subdirectorio `docs/features/`**. Define cómo deben escribirse los documentos de feature a nivel de comportamiento de esta carpeta. Se carga bajo demanda solo al trabajar sobre ficheros de aquí, y **complementa** al padre [`../CLAUDE.md`](../CLAUDE.md) — nunca debe repetir lo que el padre ya dice.

**Agnóstico del proyecto por diseño.** Nada aquí nombra una feature o dominio concretos. Toda regla es transferible a cualquier proyecto que adopte el patrón de documentación de dos niveles comportamiento/límites.

**Precedencia.** El `CLAUDE.md` raíz prevalece sobre este fichero, y el padre `docs/CLAUDE.md` prevalece sobre este fichero. Este fichero gobierna únicamente `docs/features/`.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio futuro de reglas pasa por una nueva versión de este fichero.

## 1. Qué es esta carpeta

Un fichero Markdown por feature, describiendo el **comportamiento**: qué hace la feature, qué experimenta el usuario, los disparadores, las condiciones, los casos límite y el *porqué* detrás de las decisiones de diseño. Audiencia: el equipo de ingeniería y los futuros mantenedores — no los usuarios finales, y no Claude como fuente de reglas arquitectónicas.

## 2. La división comportamiento / límites (la regla que más importa aquí)

Cada feature se documenta a través de **dos ficheros emparejados**:

- `features/<slug>.md` — **comportamiento**: cómo funciona y qué experimenta el usuario.
- `limits/<slug>.md` — **cifras exactas** (topes, tamaños, TTLs, reintentos, timeouts, mínimos, debounce) más la lista exhaustiva de «qué NO soporta».

El par comparte un **slug**: `features/lupa.md` ↔ `limits/lupa.md`. El emparejamiento es **1:1** — nunca crees ni conserves una mitad sin la otra.

**En `features/`, no indiques los valores de los topes como cifras.** Menciona un límite solo de pasada y difiere el número al gemelo (p. ej. «hasta un tope — la cifra exacta está en [`../limits/<slug>.md`]»). Los números viven en `limits/`; el comportamiento y el *porqué* viven aquí. Mantén el solape mínimo y deliberado.

## 3. Enlaces cruzados

- Al gemelo de límites: `[../limits/<slug>.md](../limits/<slug>.md)`.
- A una feature hermana: `[<other-slug>.md](<other-slug>.md)`.
- Prefiere enlazar **dentro de `docs/`** antes que enlazar a guías a nivel de código (`*_guide.md`, `repository_guide.md`) — esas se dirigen a una audiencia distinta.
- Nunca dejes un enlace roto: el destino debe existir ya o crearse en el mismo cambio.

## 4. Forma de un documento de feature

- Abre con un párrafo que exponga qué cubre el documento y un puntero a su gemelo de límites.
- Secciones numeradas (`## N. Título`), párrafos cortos y ejemplos concretos entrada→resultado.
- Cierra con una sección `## Resumen en una frase`: un único blockquote que condensa toda la feature.
- Idioma: coincide con los demás documentos ya presentes en esta carpeta; no mezcles idiomas dentro de un mismo fichero (padre `docs/CLAUDE.md` § 6).

## 5. Fuente de verdad — mantenla exacta

Un documento de aquí tiene la misma autoridad que un `*_guide.md`: cuando el código lo contradice, el documento se trata como correcto y el código es lo que debe cambiar (`CLAUDE.md` raíz § 9). Por eso **toda afirmación de comportamiento debe coincidir con el código real y actual.** Cuando escribas o edites un documento de feature, confirma la afirmación contra la implementación — un documento inexacto desvía en silencio a un futuro revisor hacia «arreglar» código que estaba bien.

## 6. Añadir o cambiar una feature

- Nueva feature → crea **ambos** `features/<slug>.md` y `limits/<slug>.md`, y añade la entrada a **ambos** índices `README.md`.
- Comportamiento cambiado → actualiza el documento de feature (y su gemelo de límites, si se movió una cifra) en el mismo cambio. Un documento obsoleto es peor que uno inexistente.
