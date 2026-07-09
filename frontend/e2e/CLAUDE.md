# Reglas Generales de la Capa End-to-End (E2E)

Este es el `CLAUDE.md` de la **capa de tests end-to-end** del frontend. Gobierna los tests a nivel de navegador que ejercitan la aplicación compilada contra un backend real. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la capa E2E desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas de la capa E2E pasa por una nueva versión de este fichero.

## 1. Propósito

Esta capa es dueña de los **tests end-to-end a nivel de navegador**. Valida que la aplicación compilada, la red, el backend y la base de datos cooperan para entregar resultados visibles para el usuario. Complementa — pero no duplica — los niveles unitario y de integración documentados en `../src/test/CLAUDE.md`.

## 2. Estructura

```
e2e/
├── specs/                 # Test files — one file per user journey or flow
├── fixtures/              # Shared test data, auth helpers, setup utilities
├── .auth/                 # Saved authenticated browser state (git-ignored)
├── .artifacts/            # Test output: traces, screenshots, videos (git-ignored)
└── playwright.config.ts   # Runner configuration
```

## 3. Alcance

### 3.1 Lo que hacen los tests E2E
- Ejercitan recorridos de usuario que cruzan al menos dos páginas y dependen del stack completo (frontend compilado, red real, backend real).
- Se autentican a través del flujo de login **real** con un usuario de prueba sembrado — nunca a través de stubs.
- Verifican lo que el usuario ve en el DOM, no detalles internos de implementación.

### 3.2 Lo que no hacen los tests E2E
- Duplicar cobertura que un test de integración ya proporciona. Si un bug puede atraparse con MSW en el nivel de integración, pertenece ahí.
- Mockear el tráfico de red a nivel de navegador. La aplicación bajo test es el build real.
- Fingir ser rápidos. E2E es el nivel lento y de alto valor.

## 4. Reglas de los Specs (`specs/`)

### 4.1 Un recorrido por fichero
- Cada fichero de spec cubre un golden path (p. ej. "iniciar sesión y llegar a la vista principal", "crear y enviar un formulario"). Los ficheros se mantienen enfocados para que un fallo apunte con claridad al recorrido roto.

### 4.2 Nombrado de ficheros
- Los specs de Playwright usan `*.spec.ts`. La extensión la impone el glob del runner — no mezcles `.test.ts`.

### 4.3 Aserciones
- Verifica sobre el DOM visible (`expect(locator).toBeVisible()`, `toHaveText()`, `toHaveURL()`) y sobre efectos secundarios observables por un usuario real.
- No verifiques sobre estado computado, variables internas ni internals del framework.

### 4.4 Sincronización (timing)
- Prefiere los locators con auto-espera de Playwright. Evita `page.waitForTimeout(ms)` arbitrarios. Cuando un test necesite una condición específica, usa un `expect(...).toPass()` explícito o `page.waitForURL(...)`.

## 5. Fixtures y Estado Autenticado (`fixtures/`, `.auth/`)

### 5.1 Usuarios de prueba sembrados
- Los recorridos autenticados reales usan identidades de prueba pre-sembradas aprovisionadas por el backend. Las identidades y sus credenciales son responsabilidad de la capa E2E del backend; la capa E2E del frontend las **consume**.

### 5.2 Reutilización del storage state
- Tras autenticarse una vez en un setup global, el storage state del navegador resultante se escribe en `.auth/<name>.json` y lo reutilizan los specs posteriores vía `test.use({ storageState: ... })`. Esto mantiene la suite rápida y determinista.

### 5.3 Higiene de git
- `.auth/` y `.artifacts/` están git-ignored en la raíz del frontend. Contienen material de sesión y salida de ejecución respectivamente.

## 6. Contrato de Reset

### 6.1 Aislamiento por spec
- Ningún estado mutable puede filtrarse entre specs. Dos estrategias son aceptables:
  - La identidad de prueba sembrada se resetea vía un endpoint solo-para-tests invocado en `beforeEach`.
  - Cada spec opera dentro de su propio recurso acotado (p. ej. un registro recién creado con un id único) y limpia en `afterEach`.

### 6.2 El retry no es una tapadera
- `retries` en la config de Playwright es una herramienta de diagnóstico, no un sustituto de arreglar un test flaky. Si un spec necesita retries para pasar, haz triaje de la causa raíz.

## 7. Debe / No Debe

### Debe
- Ejecutarse contra un backend real y en marcha (local o staging determinista).
- Autenticarse a través del flujo de login de producción.
- Producir artefactos (traces, screenshots, vídeo) ante un fallo para depurar.

### No Debe
- Mockear HTTP, cookies ni storage a nivel de navegador.
- Compartir estado entre specs.
- Reproducir cobertura alcanzable en el nivel de integración.
- Importar código de `src/` — la suite tiene su propio `tsconfig` y ejercita la aplicación solo a través del navegador en marcha.

## 8. Fronteras de Import

| Imports permitidos                                                        | Imports prohibidos           |
|---------------------------------------------------------------------------|------------------------------|
| `@playwright/test`, ficheros dentro de `e2e/` (fixtures, helpers), config de entorno   | Cualquier ruta que empiece por `src/` |

## 9. Comandos y CI

- `npm run e2e` — ejecuta la suite de Playwright contra la base URL configurada.
- `npm run e2e:ui` — runner interactivo para depuración local.
- CI invoca `npm run e2e` en los merges a la rama principal, no en cada push. La flakiness de E2E nunca debe bloquear una PR pequeña.

## 10. Relación con Otros Niveles de Testing

La filosofía global (Testing Trophy, la frontera de MSW, la co-ubicación de los tests de Vitest) está documentada en `../src/test/CLAUDE.md`. Este fichero gobierna únicamente los aspectos específicos de E2E. Cuando un cambio afecte a ambos niveles — por ejemplo, añadir un recorrido crítico nuevo — lee ambos ficheros antes de escribir tests.
