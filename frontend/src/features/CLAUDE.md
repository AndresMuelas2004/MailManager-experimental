# Reglas Generales de la Capa Feature Slice

Este es el `CLAUDE.md` de la **capa feature** del frontend — el directorio de vertical slices donde vive la mayor parte del código de la aplicación. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la capa feature desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas de la capa feature pasa por una nueva versión de este fichero.

## 1. Propósito

Cada subdirectorio directo de `features/` es un **vertical slice** de la aplicación: un dominio autocontenido, con sus propias pages, hooks y components. Las features son el lugar donde la UI, el estado local y el estado de servidor se ensamblan en comportamiento visible para el usuario.

```
features/
├── <domain-a>/
├── <domain-b>/
└── ...
```

Cada feature es autocontenida. Las features **nunca** dependen entre sí.

## 2. Estructura de una Feature

Cada directorio de feature tiene **exactamente tres** subdirectorios — ni más, ni menos:

```
features/<name>/
├── pages/        # Route-level components — one file per route
├── hooks/        # Feature-specific hooks (fetching, mutation, local state)
└── components/   # Feature-specific presentational components
```

Cualquier otra disposición es una violación. Si un fichero parece no encajar, pertenece a otra capa:
- Compartido por varias features → `components/` o `lib/`.
- Contrato HTTP → `api/`.

## 3. Reglas de Pages (`pages/`)

### 3.1 Un fichero por ruta
- Exactamente una ruta del router mapea a cada fichero en `pages/`. Un fichero de page sin ruta es código muerto.

### 3.2 Orquesta, no renderices
- Una page instancia los hooks de la feature y pasa los datos y callbacks resultantes como props a los components. Las pages contienen JSX mínimo — solo el esqueleto estructural y cualquier cuestión de layout de nivel superior.

### 3.3 Nada de HTTP directo
- Las pages nunca importan de `api/endpoints/`. Todo el acceso a datos fluye a través de los hooks de la feature.

## 4. Reglas de Hooks (`hooks/`)

### 4.1 TanStack Query primero
- Las lecturas van por `useQuery` con un `queryKey` estable.
- Las escrituras van por `useMutation`. En caso de éxito, invalida el `queryKey` correspondiente mediante `queryClient.invalidateQueries(...)` para que las queries dependientes se refresquen automáticamente.
- Las query keys siguen una forma consistente: `[<resource>, <scope>, ...<filters>]`. Los filtros que pueden ser `undefined` usan `filter ?? null` para mantener la key estable.

### 4.2 Propiedad de los efectos secundarios
- Todos los efectos secundarios adyacentes a `fetch`, incluidas las llamadas derivadas de esquema a `api/endpoints/`, viven en hooks. Nunca en components, nunca en pages directamente.

### 4.3 Traducción de errores
- Todo hook que expone errores a la UI los convierte mediante `toUiError()` de `../../api/client/errors`. Los consumidores reciben un `UiError`, nunca un `Error` o `ApiError` en crudo.

### 4.4 Contrato público
- Un hook de listado devuelve un objeto con `{ data, loading, error, refresh, ... }` o una forma plana equivalente. Las keys son derivadas, no objetos de TanStack Query en crudo — quien lo consume no debería necesitar entender los entresijos de TanStack Query para usar el hook.
- Un hook de mutación devuelve funciones invocables (más `loading` / `error`) en lugar de los valores de retorno en crudo de `useMutation`.

### 4.5 Flags derivados
- Cuando una feature necesita un indicador separado de "sincronización en segundo plano" junto al spinner de la primera carga, deriva `syncing` como algo tipo `mutation.isPending || (query.isFetching && !query.isLoading)` para que el indicador siga activo durante el refetch posterior a la invalidación.

## 5. Reglas de Components (`components/`)

### 5.1 Presentacionales
- Los components reciben datos vía props y emiten eventos vía callbacks. No importan de `api/` y no llaman a hooks que hagan fetch de datos.

### 5.2 Solo estado de UI local
- `useState` está permitido para cuestiones de UI (toggles, selección, borradores de input). Cualquier cosa que represente la verdad del servidor se queda en un hook.

### 5.3 Un fichero por componente
- `PascalCase.tsx` con un export por defecto que coincide. Props tipadas explícitamente.

## 6. Regla Entre Features (Frontera Dura)

Un fichero bajo `features/<a>/` **no** puede importar de `features/<b>/` bajo ninguna circunstancia. Si dos features necesitan lo mismo:

- JSX compartido → promuévelo a `components/common/` o `components/ui/`.
- Lógica compartida → promuévela a `lib/` (o, si está ligada a HTTP, a un nuevo endpoint en `api/`).
- Tipo compartido → promuévelo a `lib/types.ts`.

Las violaciones de esta regla son la causa más común de degradación por acoplamiento. Recházalas sin excepción.

## 7. Debe / No Debe

### Debe
- Mantener `pages/`, `hooks/` y `components/` dentro de cada directorio de feature.
- Encaminar toda llamada remota a través de un hook en `hooks/`.
- Traducir los errores con `toUiError` en la frontera del hook.

### No Debe
- Importar de otra feature.
- Llamar a `fetch()` o `useQuery`/`useMutation` desde components. Eso vive en hooks.
- Definir lógica de enrutado dentro de la feature. Las pages se exportan; el router decide dónde se montan.

## 8. Fronteras de Import

| Desde `features/<x>/`           | Puede importar de                                                | No puede importar de                        |
|---------------------------------|------------------------------------------------------------------|---------------------------------------------|
| `pages/`                        | `hooks/` propio, `components/` propio, `components/`, `lib/`     | `api/client/http` (pasa por endpoints)      |
| `hooks/`                        | `api/endpoints/`, `api/client/errors`, `api/types/`, `lib/`      | `components/ui/`, `components/common/`      |
| `components/`                   | `components/common/`, `components/ui/`, `lib/`                   | `api/`                                      |
| cualquiera de las tres          | `features/<y>/` — **nunca**                                      |                                             |

Ver `../api/CLAUDE.md` para el contrato de API y `../components/CLAUDE.md` para los niveles de UI compartida.

## 9. Añadir una Nueva Feature — Checklist

- [ ] Lee `../api/CLAUDE.md`, `../components/CLAUDE.md`, `../lib/CLAUDE.md` y `../test/CLAUDE.md` antes de escribir código.
- [ ] Crea `features/<name>/{pages,hooks,components}/`.
- [ ] Añade los esquemas Zod + tipos inferidos en `../api/types/dto.ts` (ver el checklist de la capa API).
- [ ] Añade el fichero de endpoint en `../api/endpoints/` (wrappers finos alrededor de `request()`).
- [ ] Implementa los hooks con `useQuery` / `useMutation`; invalida las query keys en el `onSuccess` de cada mutación.
- [ ] Construye la page para orquestar los hooks y pasar los datos a los components; construye los components como presentacionales.
- [ ] Registra la ruta en `../app/routes/router.tsx` (ver `../app/CLAUDE.md`). Carga con lazy-load salvo que la page esté en el camino de arranque.
- [ ] Añade tests unitarios para los helpers puros y tests de integración para la page. Ver `../test/CLAUDE.md`.
