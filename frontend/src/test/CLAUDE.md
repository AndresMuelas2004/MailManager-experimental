# Reglas Generales de la Capa de Testing del Frontend

Este es el `CLAUDE.md` de la **convención de testing del frontend**. Sirve como la referencia arquitectónica general de la capa de testing, describiendo qué tipos de test existen, qué cubren, qué mockean, dónde viven y en qué proporción deben escribirse. Todo aspecto cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la convención de testing del frontend desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las convenciones de testing pasa por una nueva versión de este fichero.

## 1. Forma del Testing — el Testing Trophy

El frontend **no** sigue la clásica pirámide de tests del backend (base ancha de tests unitarios, punta fina de E2E). Sigue el **Testing Trophy** (Kent C. Dodds), que refleja dónde viven los bugs reales en una aplicación React:

```
         ╱ E2E ╲              ← few, critical flows only
       ╱─────────╲
     ╱ INTEGRATION ╲           ← the majority — sweet spot
   ╱───────────────╲
  ╱      UNIT        ╲         ← moderate, pure logic only
 ╱─────────────────╲
╱  STATIC (TS + ESLint) ╲      ← base, already free
─────────────────────────
```

**Justificación.** En frontend, los bugs rara vez se esconden dentro de una única función pura. Se esconden en las costuras entre pages, hooks, componentes y la capa de API. Los tests de integración que ejercitan una porción completa de feature con el HTTP interceptado en la frontera de red capturan esos bugs sin la fragilidad ni el coste de los E2E. Los tests unitarios cubren la lógica pura de forma barata; los tests de integración cubren el comportamiento real; los tests E2E cubren los pocos golden paths que deben sobrevivir a un despliegue de extremo a extremo.

## 2. Categorías de Test

Tres categorías, cada una con un ámbito, unas herramientas y una estrategia de ubicación distintos.

### 2.1 Tests unitarios

- **Ámbito.** Lógica pura y aislada. Sin renderizado del DOM más allá de `renderHook`, sin HTTP, sin router, sin contexto.
- **Qué testean.** Funciones puras, hooks puros (hooks sin efectos secundarios), traductores de errores, reducers, formateadores, constantes/mapas.
- **Qué mockean.** Nada. Si un test unitario necesita un mock para funcionar, el código bajo prueba pertenece a la capa de integración.
- **Qué verifican (de verdad).** Pureza entrada → salida. Cobertura de ramas de la lógica de decisión. Nada más.
- **Herramientas.** Vitest como runner. `@testing-library/react` solo para `renderHook` sobre hooks puros.
- **Volumen esperado.** Moderado. Las apps pequeñas y medianas suelen situarse en el rango de 30–80 tests unitarios.

### 2.2 Tests de integración (tests de componente)

- **Ámbito.** Renderizan una page completa o una porción de feature dentro de los mismos providers usados en producción (router, query client, contexto de auth). Simulan al usuario con `user-event`. Interceptan el HTTP en la frontera de red — nada más se mockea.
- **Qué testean.** Que una page, cuando el usuario hace clic en X o envía Y, llama al endpoint correcto, refleja correctamente los estados de carga/error/éxito y navega como se espera.
- **Qué mockean.** Solo las respuestas HTTP, mediante MSW (Mock Service Worker). Las APIs de navegador que jsdom no puede implementar (`IntersectionObserver`, `matchMedia`) se polyfillan en el fichero de setup compartido — no se mockean por test.
- **Qué verifican (de verdad).** La integración interna completa: hooks, componentes, cliente de API (`request<T>()`), funciones de endpoint, traducción de errores, validación de esquema y comportamiento de caché se ejecutan todos sin mocks. Solo se sintetiza la respuesta de red. Esto es lo que hace de los tests de integración el punto óptimo del Trophy.
- **Herramientas.** Vitest + `@testing-library/react` + `@testing-library/user-event` + `@testing-library/jest-dom` + MSW.
- **Volumen esperado.** El grupo más grande. Las apps pequeñas y medianas suelen situarse en el rango de 50–200 tests de integración — aproximadamente uno por page más uno por cada interacción de usuario relevante dentro de esa page.

### 2.3 Tests E2E

- **Ámbito.** La aplicación compilada corriendo en un navegador real contra un backend real (o un backend de staging determinista con cuentas de prueba pre-sembradas). La autenticación pasa por el flujo de login real con un usuario de prueba.
- **Qué testean.** Recorridos de usuario de golden path que cruzan fronteras del sistema: login → navegar → realizar una acción de varios pasos → verificar el resultado en una vista diferente. Flujos que solo un stack de extremo a extremo puede garantizar.
- **Qué mockean.** Nada a nivel de navegador. El backend puede configurarse con cuentas de prueba o una base de datos de prueba, pero la aplicación bajo prueba es la build de producción real.
- **Qué verifican (de verdad).** Que la app desplegada, la red, el backend y la base de datos cooperan todos para entregar el resultado al usuario. Capturan bugs de build, enrutado, cookies y entorno que los tests de integración no pueden ver.
- **Herramientas.** Playwright (Chromium por defecto). Matrices multi-navegador solo para flujos donde las diferencias entre navegadores realmente importan.
- **Volumen esperado.** Pequeño. Típicamente 5–15 specs para toda la aplicación. Cubre los 3–7 golden paths; resiste la tentación de duplicar aquí los tests de integración. Si un bug puede capturarse en la capa de integración, ahí es donde pertenece — el tiempo y la fragilidad de los E2E son caros.

## 3. Ubicaciones — Co-ubicados vs Separados

| Tipo de test   | Estrategia de ubicación                        | Ruta de ejemplo                                    |
|-------------|------------------------------------------------|----------------------------------------------------|
| Unitario    | **Co-ubicado** con el fichero fuente           | `src/lib/formatters.test.ts`                       |
| Integración | **Co-ubicado** con la page/componente          | `src/features/<feature>/pages/<Page>.test.tsx`     |
| E2E         | Directorio **separado** de nivel superior      | `e2e/specs/login.spec.ts`                          |
| Helpers de test compartidos (no tests)   | Directorio **central**            | `src/test/setup.ts`, `src/test/msw/handlers.ts`    |

### Reglas

1. **Co-ubicación para los tests de Vitest.** Los tests unitarios y de integración viven junto al fichero que testean. Mover, renombrar o borrar código fuente mueve sus tests automáticamente — los refactors se mantienen seguros.
2. **Los E2E siempre están separados.** Playwright tiene su propio runner, su propio `tsconfig` y no importa de `src/` directamente. Todos los specs E2E viven bajo `e2e/` en la raíz del frontend, con su propio `playwright.config.ts`.
3. **La infraestructura de test compartida vive en `src/test/`.** Handlers de MSW, ficheros de setup de Vitest, factories de test, helpers de render personalizados. El código de producción en `src/` nunca debe importar de `src/test/`.
4. **Nombrado de ficheros.** Los specs de Vitest usan `*.test.ts` o `*.test.tsx`. Los specs de Playwright usan `*.spec.ts`. La elección de extensión la impone el glob de cada runner — no los mezcles.

## 4. La Frontera de MSW

Mock Service Worker intercepta las peticiones en la capa de `fetch`, no en la capa de funciones de endpoint. Esta frontera es innegociable y es la razón por la que los tests de integración son fiables:

- Se ejecuta el `request<T>()` real.
- Se ejecutan las funciones de endpoint reales.
- Se ejecuta la traducción de errores real (`toUiError`, validación de esquema).
- Se ejecuta la capa real de data-fetching/caché (p. ej. React Query).
- Solo se sintetiza la respuesta HTTP.

### Reglas

1. **Los handlers viven en `src/test/msw/`.** Un fichero por recurso de backend, reflejando `src/api/endpoints/`.
2. **Nunca mockees funciones de endpoint directamente.** No hagas `vi.mock("../../api/endpoints/<resource>")`. Mockear a nivel de endpoint saltea toda la capa del cliente de API — una de las fuentes más comunes de bugs reales — y frustra el propósito de los tests de integración.
3. **Nunca mockees `fetch` a mano.** Usa MSW. Los mocks de `fetch` hechos a mano derivan, se filtran entre tests y no ejercitan el contrato de petición/respuesta.
4. **Por defecto éxito; sobrescribe para el fallo.** El servidor MSW compartido devuelve respuestas de happy-path para cada handler. Los tests individuales instalan `server.use(...)` en línea para simular errores, timeouts o casos límite.
5. **Resetea entre tests.** El setup compartido llama a `server.resetHandlers()` en `afterEach` para que los overrides de un test nunca se filtren a otro.

## 5. Debe / No Debe — por tipo de test

### Tests unitarios
- **Debe** tomar entradas planas, devolver salidas planas y hacer aserciones sobre ellas. Rápido (< 10 ms cada uno).
- **No debe** renderizar el DOM completo, llamar a `fetch`, importar pages de feature ni tocar router/contexto.

### Tests de integración
- **Debe** renderizar el componente bajo prueba dentro de los mismos providers usados en producción, simular interacciones vía `user-event` y usar MSW para el HTTP.
- **No debe** mockear funciones de endpoint, mockear hooks de aplicación ni hacer aserciones sobre detalles internos de implementación (nombres de variables de estado, helpers privados). Haz aserciones sobre lo que el usuario ve.

### Tests E2E
- **Debe** autenticarse vía el flujo de login real con un usuario de prueba pre-sembrado, ejercitar un recorrido que cruce al menos dos pages y hacer aserciones sobre resultados visibles en el DOM.
- **No debe** duplicar cobertura que los tests de integración ya proporcionan. El tiempo de E2E es caro; mantén la suite pequeña y de alto valor.

## 6. Estado Requerido Antes de Cada Test

| Tipo        | Reset entre tests                                                                                       |
|-------------|---------------------------------------------------------------------------------------------------------|
| Unitario    | Nada — sin estado por diseño.                                                                            |
| Integración | Handlers de MSW reseteados a los valores por defecto; caché de queries limpiada; router reseteado. Automatizado en `src/test/setup.ts`.    |
| E2E         | El estado del usuario de prueba reseteado mediante fixtures sembradas o un endpoint de reset solo-para-test. Nunca compartas estado entre specs. |

## 7. CI y Comandos

### Reglas

1. **Tres comandos distintos.**
   - `npm test` — Vitest, todos los unitarios + integración, modo watch durante el desarrollo.
   - `npm run test:unit` — Vitest con un glob que excluye los specs de integración (gate de CI para feedback rápido).
   - `npm run e2e` — Playwright contra un backend real.
2. **`npm test` se ejecuta en cada commit y cada PR.** `npm run e2e` se ejecuta en CI para los merges a la rama principal, no en cada push. La fragilidad de los E2E nunca debe bloquear PRs pequeños.
3. **Las comprobaciones estáticas se ejecutan primero.** `tsc --noEmit` y `eslint` se ejecutan antes que cualquier test en CI. Un error de tipo o de lint hace fallar el pipeline antes de que se ejecute ningún test.

## 8. Añadir Tests para una Nueva Feature — Checklist

Al añadir una nueva feature, escribe los tests en este orden — no empieces la siguiente capa hasta que la anterior esté en verde:

- [ ] Un test unitario por cada nueva función pura o hook aislado en `lib/` o `features/<x>/hooks/`.
- [ ] Un test de integración para la nueva page y para cada interacción de usuario relevante (clic, envío de formulario, caso de error, actualización optimista).
- [ ] Si la feature cruza pages e importa para el recorrido del usuario, añade un único spec E2E para el golden path. No añadas E2E para cada permutación.

## 9. Anti-patrones (No Hacer)

1. **Testear detalles de implementación.** No hagas aserciones sobre nombres de variables de estado, nombres de funciones internas ni la forma de las props que se pasan entre componentes que el usuario no ve. Haz aserciones sobre lo que el usuario observa.
2. **Sobre-mockear.** Mockear hooks de aplicación, componentes o funciones de endpoint dentro de un test de integración lo reduce a un test unitario con ruido extra. Mantén la frontera de mock en MSW.
3. **Tests solo de snapshot.** Un `toMatchSnapshot` nunca es por sí solo un test de integración. Usa los snapshots con moderación y solo para salida estructural estable (p. ej. el markup de un pequeño componente presentacional).
4. **Estado mutable compartido entre tests.** Cada test es independiente. Si dos tests comparten una fixture, esa fixture se construye fresca por test — nunca se muta en el sitio.
5. **La cobertura como objetivo.** La cobertura es un diagnóstico, no un objetivo. Una línea cubierta por una aserción sin sentido es peor que una línea sin cubrir, porque esconde un hueco detrás de una barra verde.
