# Reglas Generales de la Capa Frontend

Este es el `CLAUDE.md` de nivel superior del **frontend** de la aplicación. Describe la arquitectura, el stack tecnológico y las reglas que gobiernan cada directorio bajo `frontend/`. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la capa frontend desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio (incluidos los `CLAUDE.md` de subcapas), gana la regla de capa más específica **salvo** que este fichero establezca una restricción más estricta, en cuyo caso este fichero tiene precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas del frontend pasa por una nueva versión de este fichero.

## 1. Stack Tecnológico

- **React** con **TypeScript** en modo estricto (`strict: true`, `noUnusedLocals`, `noUnusedParameters`).
- **Vite** como herramienta de build y servidor de desarrollo.
- **React Router** para el enrutado del lado del cliente.
- **TanStack Query** para el estado de servidor y el data fetching. Es la única capa de caché aceptable.
- **Zod** para la validación en runtime de la forma de cada respuesta y petición de la API.
- **Tailwind CSS** para los estilos — solo clases de utilidad.
- **Vitest** + **Testing Library** + **MSW** para tests unitarios y de integración.
- **Playwright** para tests end-to-end.
- **Prettier** + **ESLint** + **lint-staged** + hooks de pre-commit para formateo y linting.

Ni Redux, MobX, Zustand ni ninguna librería similar de estado global salvo que una necesidad concreta lo justifique. El estado de servidor pertenece a TanStack Query; el estado de UI pertenece a `useState`.

## 2. Estructura de Paquetes

```
frontend/
├── src/
│   ├── app/            # Application shell: layout, providers, router
│   ├── api/            # HTTP client + endpoints + Zod-validated DTOs
│   ├── features/       # Vertical slices — one domain per subdirectory
│   ├── components/     # Shared UI: common (primitives) + ui (domain-aware)
│   ├── lib/            # Pure utilities, types, generic hooks
│   ├── test/           # Testing infrastructure (MSW, render helpers, setup)
│   ├── styles/         # Global CSS (Tailwind import + minimal resets)
│   └── main.tsx        # Entry point — mounts <Providers /> into root
├── e2e/                # Playwright end-to-end specs (own tsconfig)
└── <configs>           # vite.config.ts, vitest.config.ts, playwright.config.ts, etc.
```

Cada subdirectorio de `src/` y `e2e/` tiene su propio `CLAUDE.md` con las reglas específicas de esa capa. Este fichero es el resumen; aquellos son la autoridad en su propio ámbito.

## 3. Fronteras de Capa (Grafo de Dependencias)

Los imports fluyen en una única dirección. Las flechas se leen como "puede importar de":

```
            app/
             │
             ▼
          features/
         ╱   │   ╲
        ▼    ▼    ▼
 components/  api/  lib/
       │      │
       ▼      ▼
      lib/   zod
```

Reglas explícitas:
- **`features/<a>/` nunca importa de `features/<b>/`.** Las piezas compartidas van a `components/`, `lib/` o `api/`.
- **`api/` nunca importa de `features/`, `components/` ni `app/`.** Es una capa casi-hoja.
- **`lib/` nunca importa de ningún otro directorio de `src/`.** Es la hoja.
- **`components/common/` nunca importa de `components/ui/` ni de ninguna capa superior.**
- **`components/ui/` nunca importa de `features/` ni de `api/endpoints/`.**
- **`app/` nunca importa hooks de feature directamente** (las pages lo hacen dentro de la feature).
- **`src/` nunca importa de `src/test/` ni de `e2e/`.** Los helpers de test están acotados a los tests.

Consulta el `CLAUDE.md` de cada capa para las fronteras detalladas.

## 4. Estilos

Solo clases de utilidad de Tailwind. Sin CSS modules, sin styled-components, sin objetos `style` inline salvo que un valor de píxel realmente dinámico lo requiera. El único fichero CSS es `styles/globals.css` (import de Tailwind + resets mínimos). El diseño responsive usa los prefijos de Tailwind (`sm:`, `md:`, `lg:`). El modo oscuro, cuando existe, usa el prefijo `dark:` de Tailwind.

## 5. Gestión de Estado

| Tipo de estado                 | Vive en                                         |
|-------------------------------|-------------------------------------------------|
| Estado de servidor (datos remotos)    | **TanStack Query cache** (`useQuery`/`useMutation`) |
| Estado de UI (local a un componente)| `useState` dentro del componente                 |
| Estado transversal de la app       | React `Context` compuesto en `app/providers/`    |

Nunca almacenes estado de servidor en `useState` y nunca conviertas una caché de TanStack Query en una variable global. Si el estado de UI debe compartirse entre componentes, elévalo solo lo estrictamente necesario; recurre a `Context` únicamente cuando componentes muy anidados lo necesiten.

## 6. Manejo de Errores

Un único pipeline gobierna cada error que se muestra al usuario:

```
fetch or schema parse → ApiError / ValidationError (in api/client/errors.ts)
                        │
                        ▼
hook catches → toUiError(err) → UiError { message, code? }
                        │
                        ▼
component renders error.message
```

Reglas:
- Cada cuerpo de respuesta del backend se valida en la frontera de la API mediante un esquema Zod. La divergencia entre frontend y backend aflora como `ValidationError`, no como un bug de renderizado silencioso.
- Los hooks capturan; los componentes muestran. Los componentes nunca llaman a `toUiError`, nunca leen `Error.message` en crudo, nunca ramifican sobre `instanceof ApiError`.
- Los fallos de red se distinguen a sí mismos (código `network_error`) para que la UI pueda mostrar un mensaje específico cuando corresponda.

La jerarquía completa y las convenciones viven en `src/api/CLAUDE.md`.

## 7. Convenciones de Nombres

- **Componentes**: `PascalCase.tsx` — el export por defecto coincide con el nombre del fichero.
- **Hooks**: `camelCase.ts` empezando por `use` (p. ej. `useResourceList.ts`).
- **Ficheros de endpoint**: `camelCase.ts` que coincide con el recurso del backend.
- **Ficheros de tipos**: `camelCase.ts` (p. ej. `dto.ts`).
- **Tipos de props**: `Props` para tipos internos, `<ComponentName>Props` cuando se exportan.
- **Constantes**: `UPPER_SNAKE_CASE` para constantes de verdad.
- **Directorios**: en minúsculas, sin separadores (`components`, `endpoints`, `providers`).

## 8. Testing

El frontend sigue el **Testing Trophy**: comprobaciones estáticas (TypeScript + ESLint) como base, un nivel unitario moderado, un gran nivel de integración (el punto óptimo — MSW intercepta HTTP en la frontera de `fetch` mientras el resto de capas corre sin mocks), y un pequeño nivel E2E para los golden paths.

- Tests unitarios + de integración: `*.test.ts(x)` co-ubicados junto al fichero que cubren. Ver `src/test/CLAUDE.md`.
- Tests E2E: `e2e/specs/*.spec.ts`, runner y config separados. Ver `e2e/CLAUDE.md`.

## 9. Formateo, Linting, Commits

- Prettier impone un único estilo de formateo. El hook de pre-commit ejecuta `prettier --write` + `eslint --fix` sobre los ficheros staged mediante lint-staged.
- El modo estricto de TypeScript, ESLint con los plugins de React Hooks y React Refresh, y la integración de Prettier son innegociables.
- CI bloquea según `tsc --noEmit`, `eslint`, `prettier --check` y la suite de Vitest. E2E se ejecuta en los merges a la rama principal.

## 10. Orden de Lectura Antes de Añadir Funcionalidad

Al tocar el frontend, **lee el `CLAUDE.md` de nivel de capa de cada directorio en el que el cambio vive o que atraviesa** antes de escribir código. En la práctica:

- Contrato de API nuevo o modificado → `src/api/CLAUDE.md`.
- Page / hook / componente local de feature nuevo o modificado → `src/features/CLAUDE.md`.
- Primitiva o widget compartido nuevo → `src/components/CLAUDE.md`.
- Provider global, layout o ruta nuevo → `src/app/CLAUDE.md`.
- Helper puro, tipo o hook genérico nuevo → `src/lib/CLAUDE.md`.
- Tests nuevos → `src/test/CLAUDE.md` (y `e2e/CLAUDE.md` si son end-to-end).

Saltarse este paso es la fuente más común de deriva arquitectónica.

## 11. Añadir una Nueva Feature — Checklist End-to-End

- [ ] **Lee** los ficheros `CLAUDE.md` de capa de cada directorio afectado (ver §10).
- [ ] **DTOs**: añade esquemas Zod + tipos inferidos en `src/api/types/dto.ts`.
- [ ] **Endpoints**: wrappers finos alrededor de `request()` en `src/api/endpoints/`.
- [ ] **Slice de feature**: crea `src/features/<name>/{pages,hooks,components}/`.
- [ ] **Hooks**: `useQuery` para lecturas, `useMutation` con invalidación en `onSuccess` para escrituras; traduce los errores mediante `toUiError`.
- [ ] **Pages**: una por ruta, orquestan hooks, pasan los datos como props.
- [ ] **Componentes**: presentacionales, tipados por props, sin llamadas a la API.
- [ ] **Promoción a UI compartida**: si una pieza la usan ≥2 features, muévela a `components/`.
- [ ] **Routing**: regístrala en `src/app/routes/router.tsx`, `React.lazy()` salvo que sea ruta de arranque.
- [ ] **Handlers de MSW**: añade handlers de happy-path para los nuevos endpoints en `src/test/msw/handlers.ts`.
- [ ] **Tests**: unitarios para helpers puros, de integración para la page, E2E si el recorrido es crítico.
- [ ] **Sin imports prohibidos**: verifica que se respeta el grafo de dependencias del §3.

## 12. Anti-Patrones (No Hacer)

1. Llamar a `fetch()` fuera de `src/api/client/http.ts`.
2. Importar de otra feature.
3. Escribir a mano tipos de TypeScript para los DTOs en lugar de inferirlos de un esquema Zod.
4. Almacenar estado de servidor en `useState`.
5. Mockear funciones de endpoint o hooks de aplicación dentro de un test de integración — mockea siempre en la frontera de MSW (red).
6. Renderizar `error.message` en crudo de un `Error` o `ApiError` — pasa siempre por `UiError`.
7. Declarar rutas fuera de `src/app/routes/router.tsx`.
8. Escribir CSS fuera de Tailwind.
9. Añadir una librería de gestión de estado global sin una necesidad concreta que TanStack Query más `Context` no puedan cubrir.
10. Comentar *qué* hace el código en lugar de *por qué* se tomó una decisión no obvia.
