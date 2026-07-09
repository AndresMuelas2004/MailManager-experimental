# Reglas Generales de la Capa Cliente de API

Este es el `CLAUDE.md` de la **capa cliente de API** del frontend — la única puerta entre la aplicación del navegador y la superficie HTTP del backend. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la capa cliente de API desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas de la capa de API pasa por una nueva versión de este fichero.

## 1. Propósito

`api/` es la **única** capa a la que se le permite invocar `fetch()` o comunicarse con el backend de cualquier forma. Cualquier otra capa alcanza la red exclusivamente a través de esta capa. El contrato con el backend — rutas, métodos, formas de petición/respuesta, envoltorio de error — se expresa aquí y en ningún otro sitio.

## 2. Estructura

```
api/
├── client/
│   ├── http.ts      # request<T>() — the single fetch wrapper
│   └── errors.ts    # ApiError, ValidationError, UiError, toUiError
├── endpoints/       # One file per backend resource, thin wrappers
└── types/
    └── dto.ts       # Zod schemas + inferred TypeScript types
```

## 3. Reglas del Cliente HTTP (`client/`)

### 3.1 Único `request<T>()`
- Exactamente **una** función emite llamadas HTTP en todo el código. Ningún otro fichero de `src/` puede llamar a `fetch()` directamente.
- `request<T>()` acepta: `path`, `method`, `body` opcional, `AbortSignal` opcional, `schema` opcional (validador Zod para la respuesta).
- Aplica `credentials: "include"` en cada llamada.
- Serializa los cuerpos como JSON y establece `Content-Type: application/json` solo cuando se proporciona un cuerpo.

### 3.2 Manejo de respuestas
- En un no-2xx, convierte la respuesta en un `ApiError` mediante el envoltorio de error estándar del backend.
- En un 2xx, si se proporcionó un `schema`, ejecuta `schema.safeParse(json)` y lanza un `ValidationError` si el payload falla. En caso de éxito, devuelve el valor parseado para que el llamante obtenga un tipo más estrecho que el contrato del wire.
- En un fallo a nivel de red, lanza un `ApiError` genérico con código `network_error`.

### 3.3 Jerarquía de errores
- `ApiError` es la clase base. Lleva `code`, `message`, `status` opcional, `detail` opcional.
- `ValidationError extends ApiError` añade el array `issues` de Zod y usa el código `schema_mismatch`.
- `UiError` es la forma de objeto plano (`{ message, code? }`) segura para consumo de la UI. `toUiError(err)` es el traductor canónico que toda capa superior debe usar.

### 3.4 Credenciales y almacenamiento de tokens
- Los tokens de sesión, tokens de acceso y cualquier material de credenciales **nunca deben** escribirse en `localStorage` ni `sessionStorage`. Ambos son legibles por cualquier script que corra en la página, lo que convierte un único XSS en un secuestro completo de la sesión.
- Cuando el backend lo soporta, la autenticación viaja en cookies `httpOnly` + `Secure` + `SameSite`, y `request<T>()` las hace efectivas mediante `credentials: "include"` (ver §3.1). El frontend nunca maneja el token en crudo.
- Cuando el backend fuerza un bearer token que el navegador tiene que retener, este vive solo en memoria (una variable de módulo o un closure dentro de `client/`) y se limpia en el logout. Nunca cruza a almacenamiento, `window`, ni ninguna otra superficie globalmente alcanzable.

## 4. Reglas de Endpoints (`endpoints/`)

### 4.1 Forma
- Un fichero por recurso del backend. El nombre del fichero refleja el nombre del recurso (p. ej. `users.ts`, `orders.ts`).
- Cada función es un **wrapper fino** alrededor de `request<T>()`: método HTTP, ruta, cuerpo opcional, esquema correspondiente. Sin lógica de reintentos, sin caché, sin ramificación, sin reglas de negocio.
- Las funciones devuelven `Promise<T>` donde `T` es el tipo inferido por Zod de `types/dto.ts`.

### 4.2 Paso del esquema
- Cada llamada a `request()` que devuelve datos pasa su esquema de respuesta. "Sin esquema" solo es aceptable para respuestas sin cuerpo (`204 No Content`).

## 5. Reglas de DTOs (`types/`)

### 5.1 Schema-first
- Cada DTO se define como un `z.object({...})` de Zod (o `z.array(...)`) exportado como `xxxSchema`.
- El tipo de TypeScript es **inferido**: `export type X = z.infer<typeof xSchema>`. Nunca escribas a mano el `type` junto al esquema.

### 5.2 Fidelidad al formato del wire
- Los nombres de campo coinciden **exactamente** con el backend, incluida la convención de casing (`snake_case` si el backend lo usa). El frontend no traduce nombres en esta frontera.
- La nulabilidad se expresa con `.nullable()` cuando el backend puede devolver `null`, y `.optional()` cuando el campo puede omitirse. Nunca ambos salvo que el backend produzca genuinamente tres estados.

### 5.3 Envoltorios reutilizables
- Las formas de respuesta compartidas (p. ej. `{ status: string }`, `{ message: string }`) se declaran una vez en la parte superior de `dto.ts` como `statusResponseSchema`, `messageResponseSchema`, etc., y se reutilizan a lo largo de los endpoints. No los inlines.

## 6. Debe / No Debe

### Debe
- Enrutar cada llamada de red a través de `request<T>()`.
- Validar cada cuerpo de respuesta que no sea `204` mediante un esquema Zod.
- Aflorar los errores como `ApiError` / `ValidationError`; nunca como `Error` o `Response` en crudo.
- Mantener el material de credenciales fuera de toda API de web-storage (ver §3.4).

### No Debe
- Llamar a `fetch()` fuera de `client/http.ts`.
- Mantener cachés en memoria aquí. La caché pertenece a la capa de data fetching que usan las features (TanStack Query).
- Importar de `features/`, `components/` o `app/`. Esta capa es estrictamente inferior a cualquiera de ellas.
- Escribir a mano tipos que deberían inferirse de un esquema.
- Persistir tokens, sesiones o credenciales en `localStorage`, `sessionStorage`, `IndexedDB` ni ningún otro almacenamiento legible por script.

## 7. Fronteras de Import

| Imports permitidos                   | Imports prohibidos                                 |
|--------------------------------------|----------------------------------------------------|
| `zod`, `lib/` (raro), ficheros propios | `features/`, `components/`, `app/`, `test/`       |

La capa de API se sitúa justo por encima de `lib/` en el grafo de dependencias. Ver `../lib/CLAUDE.md` para las reglas de la capa hoja.

## 8. Añadir un Nuevo Endpoint — Checklist

- [ ] **DTOs**: añade los esquemas Zod de petición/respuesta a `types/dto.ts` y los tipos inferidos junto a ellos.
- [ ] **Endpoint**: crea o extiende el fichero en `endpoints/` para el recurso; añade un wrapper fino alrededor de `request()` que pase el esquema correspondiente.
- [ ] **Errores**: si el backend introduce un nuevo código de error que a la UI le importa, aflóralo mediante `ApiError.code`; no añadas subclases sin una razón estructural.
- [ ] **Sin reintentos / caché / lógica de negocio** aquí — empuja eso al hook llamante en `features/`.
- [ ] **Tests**: añade un handler por defecto de MSW que refleje el nuevo endpoint en `src/test/msw/handlers.ts` para que los tests de integración sigan funcionando. Ver `../test/CLAUDE.md`.
