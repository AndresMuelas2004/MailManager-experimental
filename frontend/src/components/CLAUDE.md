# Reglas Generales de la Capa de UI Compartida

Este es el `CLAUDE.md` de la **capa de UI compartida** — la biblioteca de dos niveles de componentes React que se reutilizan a lo largo de las features y el shell de la aplicación. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la capa de UI compartida desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas de UI compartida pasa por una nueva versión de este fichero.

## 1. Propósito

Los componentes usados por más de una feature, o por el shell de la aplicación, viven aquí. Los componentes usados por una única feature viven dentro del propio directorio `components/` de esa feature — no aquí.

## 2. Dos Niveles

La UI compartida se divide en dos niveles estrictos según si el componente entiende el dominio de la aplicación.

```
components/
├── common/   # Domain-agnostic primitives (Button, Modal, Input, Spinner)
└── ui/       # Domain-aware widgets that receive data by props
```

### 2.1 `common/` — primitivas
- No saben nada de la aplicación. Podrían vivir sin cambios en cualquier proyecto React.
- Aceptan solo props genéricas (`label`, `onClick`, `children`, `className`, etc.).
- Solo pueden importar de `lib/`.
- Ejemplos de lo que pertenece aquí: botones, modales, inputs, spinners, badges.

### 2.2 `ui/` — widgets conscientes del dominio
- Conocen el vocabulario de dominio de la aplicación (p. ej. saben que existe un concepto de "account" o "resource") porque múltiples features comparten el mismo patrón visual.
- Reciben cada pieza de datos de dominio a través de **props** desde la page que las renderiza. Nunca hacen fetch de datos, nunca abren contexts, nunca llaman a endpoints.
- Pueden importar de `components/common/` y `lib/`. No deben importar de `features/`, `api/` ni `app/`.

## 3. Reglas de Componente (aplican a ambos niveles)

### 3.1 Un fichero por componente
- El nombre del fichero coincide con el nombre del componente en `PascalCase.tsx`. El export por defecto es ese componente.

### 3.2 Props antes que estado interno
- Los datos fluyen a través de props. El `useState` interno se limita a asuntos de UI (abierto/cerrado, hover, borrador de input, índice seleccionado). Cualquier estado que represente datos de dominio se eleva fuera.

### 3.3 Props tipadas explícitas
- Cada componente declara un tipo `Props` (o `<ComponentName>Props` cuando se exporta). Sin `any`; prefiere `unknown` y estrecha en la frontera.

### 3.4 Composición antes que configuración
- Prefiere componer componentes pequeños antes que un único componente con muchas props condicionales. Un widget con diez `if`s es una señal para dividir.

### 3.5 Sin efectos secundarios sobre la app global
- Los componentes pueden mantener estado local y llamar a callbacks de las props; no pueden mutar estado global, leer cookies ni realizar navegación directamente. Los helpers de navegación (del router) llegan a través de la page o el shell.

### 3.6 Estilos
- Solo clases de utilidad de Tailwind. Ver el `CLAUDE.md` de la raíz del frontend para la política de estilos.

### 3.7 Renderizado seguro de contenido no confiable
- Cualquier string que provenga del backend, de la URL o de la entrada del usuario se renderiza como **texto** por defecto — React lo escapa automáticamente vía interpolación JSX. No eludas ese comportamiento por defecto.
- `dangerouslySetInnerHTML` está prohibido salvo que la entrada haya pasado por un pipeline de sanitización dedicado, y ese pipeline se identifique en el call site. "El backend ya lo sanitizó" no es justificación suficiente — la frontera de confianza termina en la capa que finalmente renderiza el HTML.
- Las URLs inyectadas en `href`, `src`, `formAction` o cualquier atributo equivalente deben validarse contra una lista de protocolos permitidos (típicamente `http`, `https`, `mailto`). Nunca interpoles una URL sin verificar en un enlace que pudiera resolver a `javascript:…` o `data:text/html,…`.

## 4. Debe / No Debe

### Debe
- Mantener `common/` libre de cualquier ruta de import que empiece por `../features/`, `../api/` o `../app/`.
- Mantener `ui/` libre de cualquier import de `../features/` o `../api/endpoints/`.

### No Debe
- Emitir llamadas HTTP. Los datos siempre llegan como props.
- Mantener estado que represente verdad sobre la aplicación (el estado de servidor). El estado de servidor vive en los hooks de feature; la UI aquí solo lo lee vía props.
- Cruzar la frontera de nivel hacia arriba: `common/` no debe importar de `ui/`.
- Pasar contenido no sanitizado a `dangerouslySetInnerHTML` (ver §3.7).
- Renderizar una URL de entrada de usuario o backend en `href`/`src` sin validar su protocolo (ver §3.7).

## 5. Fronteras de Import

| Directorio             | Puede importar de                          | No puede importar de                    |
|------------------------|--------------------------------------------|-----------------------------------------|
| `components/common/`   | `lib/`                                     | `ui/`, `features/`, `api/`, `app/`      |
| `components/ui/`       | `components/common/`, `lib/`               | `features/`, `api/endpoints/`, `app/`   |

Ver `../lib/CLAUDE.md` para la capa de utilidades de la que depende este nivel.

## 6. Decisión de Ubicación — ¿Dónde Va un Componente Nuevo?

- Usado dentro de una sola feature → `features/<feature>/components/`. No aquí.
- Usado por dos o más features o por el shell, sin conocimiento de dominio → `components/common/`.
- Usado por dos o más features o por el shell, con conocimiento de dominio en su API → `components/ui/`.
- Caso límite (p. ej. un botón que solo existe para componer un widget específico de dominio) → por defecto a la feature; promociona a `common/`/`ui/` solo con el segundo consumidor.

## 7. Añadir un Componente Compartido Nuevo — Checklist

- [ ] Decide el nivel (`common/` vs `ui/`) a partir de los criterios de arriba.
- [ ] Coloca el fichero como `PascalCase.tsx` con el export por defecto correspondiente.
- [ ] Define `Props` explícitamente; prohíbe `any`.
- [ ] Asegura que no hay imports prohibidos (ver la tabla de fronteras).
- [ ] Añade un test co-ubicado `PascalCase.test.tsx` cuando el componente tenga comportamiento interactivo. Ver `../test/CLAUDE.md`.
