# Reglas Generales de la Capa Application Shell

Este es el `CLAUDE.md` del **application shell** — la capa que arranca el frontend, compone los providers globales y mapea las URLs a las pages de feature. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer el application shell desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas de la capa shell pasa por una nueva versión de este fichero.

## 1. Propósito

El shell es el "esqueleto" de la aplicación. **Arranca** la app, la **envuelve** en providers globales y **enruta** las URLs a las pages de feature. No posee lógica de negocio ni preocupaciones visuales de dominio más allá de los layouts estructurales que comparten todas las rutas.

## 2. Estructura

```
app/
├── layout/      # Layout components that render an <Outlet />
├── providers/   # React context providers composed into one shell
└── routes/      # Router configuration — one router.tsx
```

## 3. Reglas del Router (`routes/`)

### 3.1 Fuente única de verdad
- Todas las rutas se declaran en **un** `router.tsx` usando la factoría de router del framework. Ninguna feature declara sus propias rutas.

### 3.2 Apuntar a pages de feature
- El element de cada ruta referencia un page component exportado por una feature (`features/<x>/pages/...`). El shell nunca define page components él mismo.

### 3.3 Lazy loading
- Las pages fuera de la ruta de arranque usan `React.lazy()` (o el cargador lazy equivalente del framework) para dividir el bundle. Las pages de la ruta de arranque — las que se alcanzan antes de que el router haya resuelto una ruta de feature — pueden ser eager.

### 3.4 Paths de ruta
- Planos y descriptivos. Usa paths estilo recurso (`/resources`, `/resources/:id`). Evita el anidamiento profundo más allá de dos niveles.

### 3.5 Nada de lógica de enrutado en las features
- Las features exportan page components únicamente. Nunca importan el router, nunca construyen paths y nunca llaman a helpers de navegación que salten el router.

## 4. Reglas de los Providers (`providers/`)

### 4.1 Composición en un único fichero
- Todos los context providers globales se componen dentro de `Providers.tsx` (o un punto de entrada único equivalente). Los providers individuales se definen en ficheros hermanos y solo los consume `Providers.tsx`.

### 4.2 Solo transversales
- Un context vive aquí solo si es genuinamente transversal: identidad autenticada, caché de data fetching, theme, locale. El estado acotado a una feature no pertenece a un context global.

### 4.3 Instancias singleton
- Las instancias de larga vida (por ejemplo, un cliente de data fetching) se crean una sola vez con `useState(() => createClient())` dentro del provider para evitar la reinstanciación en cada re-render.

### 4.4 Devtools
- Los overlays opcionales de desarrollo (paneles de devtools, mock-switchers) se montan solo bajo `import.meta.env.DEV` (o el guard de modo dev equivalente).

## 5. Reglas de los Layouts (`layout/`)

### 5.1 Renderizar un `<Outlet />`
- Los layouts son React components que declaran la estructura del shell (chrome, navegación, boundaries de suspense) y delegan su región de contenido al `<Outlet />` del router.

### 5.2 Nada de lógica de dominio
- Un layout no hace fetch de datos, no conoce features concretas y no orquesta hooks que dependan del dominio. Si un layout necesita información contextual (p. ej. el id del recurso actual), la lee del router (params de la URL) o de un context global.

## 6. Punto de Entrada

El fichero `main.tsx` (o equivalente) monta `<Providers />` en el nodo DOM raíz y no hace nada más. Nada de fetching, nada de enrutado, nada de lógica condicional.

## 7. Debe / No Debe

### Debe
- Mantener el enrutado, el layout y los providers estrictamente estructurales.
- Cargar en lazy toda page que no sea de arranque.
- Componer todos los providers en un único punto de entrada.

### No Debe
- Importar hooks de feature directamente desde el shell (eso lo hacen las pages dentro de la feature, no aquí).
- Declarar rutas fuera de `routes/`.
- Mantener estado de dominio en un context global. El estado de dominio pertenece a `features/` o a la caché de data fetching.

## 8. Fronteras de Import

| Desde `app/`   | Puede importar de                                                  | No puede importar de                                          |
|----------------|--------------------------------------------------------------------|---------------------------------------------------------------|
| `layout/`      | `components/`, `lib/`                                              | `api/`, `features/*/hooks` o `features/*/components`          |
| `providers/`   | `api/client/errors`, `lib/`, providers de terceros pineados         | `features/`, `components/ui/`                                 |
| `routes/`      | `features/*/pages`, `layout/`                                      | `features/*/hooks`, `features/*/components`                   |

Ver `../features/CLAUDE.md` para las pages a las que apunta el router y `../api/CLAUDE.md` para los tipos de error que los providers pueden importar.

## 9. Añadir Algo al Shell — Checklist

- [ ] Si es una ruta nueva: añádela en `routes/router.tsx`, referenciando una page de `features/<x>/pages/`. Prefiere `React.lazy()`.
- [ ] Si es un provider global nuevo: añade su fichero bajo `providers/` y compónlo dentro de `Providers.tsx`. Justifica por qué es transversal.
- [ ] Si es un layout nuevo: colócalo bajo `layout/`, renderiza un `<Outlet />`, evita la lógica de dominio.
- [ ] Verifica que ningún import prohibido cruzó una frontera (ver la tabla de arriba).
