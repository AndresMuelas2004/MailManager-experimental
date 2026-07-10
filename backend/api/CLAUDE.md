# Reglas Generales de la Capa API
Este es el `CLAUDE.md` de la capa **HTTP API**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de manejo y escalado de errores, sus reglas estructurales y su comportamiento común. Todo lo que aquí se cubre es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico al proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o funcionalidad concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero a un nuevo proyecto para establecer la arquitectura de la capa API desde el primer día. La guía específica del proyecto extiende estas reglas con detalles de dominio pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas tienen precedencia.
**Inmutable.** Este fichero nunca debe editarse. Todos los cambios específicos del proyecto van en el fichero `*_guide.md` referenciado al final de este documento.

## 1. Estructura del Paquete

La capa API se organiza en cuatro subpaquetes:

```
api/
├── app.py              # Application factory, lifespan, CORS, router registration
├── errors/             # API error hierarchy + framework exception handlers
├── routers/            # Thin HTTP surface — one service call per endpoint
├── schemas/            # Pydantic request/response models
└── services/           # Orchestration, validation, error mapping
```
## 2. Framework

  Esta capa está construida sobre **FastAPI** (Python). Todas las convenciones — inyección de dependencias
  (`Depends`), gestión del lifespan, middleware CORS y manejadores de excepciones — descritas
  en este documento asumen FastAPI como framework subyacente.
## 3. Límites de las Capas

- **Routers** — superficie HTTP fina. Cero lógica de negocio. Cada endpoint declara schemas Pydantic y contiene una única llamada a un service.
- **Services** — orquestación, validación y mapeo de errores. La única capa que lanza subclases de `ApiError`. Los services llaman a las capas inferiores — nunca al revés; la única capa de la API que se comunica con capas externas como la database, el core y el auth es la capa de services; además, estas capas no se conocen entre sí—siempre son orquestadas por la capa de services.
- **Errors** — define la jerarquía `ApiError` y los manejadores de excepciones del framework que las traducen a respuestas HTTP.
- **Schemas** — subclases de `BaseModel` de Pydantic que definen el contrato de la API.

Regla estricta: los routers nunca contienen lógica de negocio, los services nunca exponen detalles HTTP (excepto recibir `Response` para la gestión de cookies).

## 4. Reglas de los Routers

Todo router sigue el mismo patrón:

1. **Una llamada a un service por endpoint.** La función de ruta llama a una única función del service y devuelve su resultado.
2. **Dependencia de auth** en todos los endpoints protegidos — devuelve la identidad autenticada.
3. **Sin lógica de negocio.** Sin condicionales, sin manejo de errores, sin transformación de datos.
4. **Los schemas Pydantic** declaran el contrato de request/response.

## 5. Reglas de los Services

- La **única capa** que lanza subclases de `ApiError`.
- **Comprobación de propiedad (ownership check)** — para cualquier acción acotada a un recurso, verifica que el usuario autenticado es su propietario antes de proceder.
- **Llamadas a la base de datos** — envuelve todas las llamadas a la base de datos en bloques `try`/`except` explícitos usando un helper de traducción que capture `DatabaseError` y excepciones inesperadas, de forma consistente con el patrón usado para los errores de core y auth.
- **Gestión de cookies** — los services que gestionan cookies de sesión reciben el objeto `Response` del framework desde el router. El establecimiento/borrado de cookies ocurre en la capa de services, no en los routers.

## 6. Jerarquía de Errores

Todos los errores de la API derivan de una única clase base con una cadena `code` estable. Un mapa de estados traduce los tipos de error a códigos de estado HTTP.

### Contrato de la clase base

Toda subclase de `ApiError` proporciona:
- `code` — identificador de cadena estable (p. ej. `"resource_not_found"`)
- `message` — descripción legible por humanos
- `detail` — dict opcional con contexto estructurado

### Mapeo de estados HTTP

Un dict `_STATUS_MAP` mapea cada clase de error a su código de estado HTTP. El `ApiError` base usa 500 por defecto.

### Envoltorio (envelope) de la respuesta

Todas las respuestas de error usan un envoltorio estándar:

```json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable message.",
    "detail": {}
  }
}
```

## 7. Unicidad del Mensaje de Error

Todo `ApiError` lanzado directamente en la capa de services (es decir, no escalado desde una capa inferior, que ya lleva su propio mensaje descriptivo) **debe** tener un `message` que:

1. **Describa qué ha pasado y dónde** — el mensaje debe ser lo bastante concreto para identificar la operación que falla y su contexto sin inspeccionar un stack trace.
2. **Sea globalmente único en todos los puntos de raise** — no puede haber dos sentencias raise en toda la capa de services que compartan la misma cadena de mensaje. Esto garantiza que un único mensaje de error basta para localizar exactamente dónde se originó el error.

Mal: `raise ResourceNotFoundError("Not found")` — genérico, duplicado en múltiples puntos.

Bien: `raise ResourceNotFoundError("Order not found while processing refund for the given transaction")` — específico de la operación y del punto.

## 8. Granularidad de las Subclases de ApiError

Toda subclase de `ApiError` debe representar el **significado semántico** del error desde el contexto donde se lanza. Leer únicamente el tipo de error debería dar una pista clara de qué salió mal y en qué área de la capa de services.

### Reglas

1. **Un concepto por clase** — no reutilices una clase genérica (p. ej. `OperationError`) en operaciones no relacionadas. Si dos errores describen fallos fundamentalmente distintos, merecen clases distintas.
2. **Tantas clases como haga falta** — crea tantas subclases de `ApiError` como sea necesario para mantener una relación estrecha entre el tipo de error y el contexto que lo lanza. Especificar de menos los tipos de error oculta información; prefiere más clases antes que menos.
3. **Nombres autodocumentados** — el nombre de la clase debería leerse como una breve descripción del dominio del fallo (p. ej. `ResourceOwnershipError`, `SessionExpiredError`, `DataSyncError`).
4. **Registra cada clase nueva** — añádela a `_STATUS_MAP` con el código de estado HTTP apropiado y documéntala en la guía de la API específica del proyecto.

## 9. Manejo de Errores — Técnica de Captura

Este es el patrón central para el manejo de errores en la capa de services. Todo bloque `try` en los services sigue la misma estructura ordenada.

### El patrón

```python
try:
    result = lower_layer_call(...)
except LayerError as exc:                   # 1. Typed layer error → translate
    raise translate_layer_error(exc, fallback=SpecificApiError) from exc
except Exception as exc:                    # 2. Unexpected error → log + generic ApiError
    logger.warning("Unexpected error (%s): %s", type(exc).__name__, exc)
    raise SpecificApiError("Failed to ...") from exc
```

### Reglas

1. **Captura la clase base de la capa** (`CoreError`, `AuthError`, `DatabaseError`) — la función de traducción usa `isinstance` para encontrar el mapeo más específico.
2. **Siempre `from exc`** — preserva la cadena de causas.
3. **El fallback encaja con el contexto** — usa la subclase de `ApiError` que mejor describa la operación fallida.
4. **Nunca expongas detalles internos en los mensajes de la API** — el fallback `except Exception` debe usar un mensaje genérico (sin `type(exc).__name__`, sin `str(exc)`). En su lugar, registra los detalles completos en el servidor con `logger.warning()`. Esto evita filtrar estado interno (nombres de clase, errores de librería, rutas) a clientes externos. Las capas internas (`core/`, `database/`, `auth/`) pueden incluir detalles en sus errores porque siempre se traducen antes de llegar al cliente.
5. **Nunca dejes escapar excepciones de capas inferiores** — todo bloque `try` tiene un fallback `except Exception`.
6. **Registra una sola vez — donde la excepción muere.** Una excepción traducida-y-relanzada NO debe registrarse con su traceback en el punto del raise: la cadena `raise ... from exc` ya lleva la causa completa a los manejadores globales, que la registran exactamente una vez (§ 10). El `logger.warning()` del fallback `except Exception` existe para añadir contexto de la operación a un mensaje de cliente deliberadamente genérico — mantenlo solo con el mensaje, nunca con `exc_info`. A la inversa, una excepción que se captura y se **traga (swallow)** (operaciones best-effort, tareas en segundo plano, agregación de éxito parcial) nunca llega a los manejadores — su punto de tragado es el único punto de observabilidad y DEBE registrarla con `exc_info=exc` para preservar la cadena de causas completa.

### Funciones y mapas de traducción

Las funciones de traducción convierten errores de capas inferiores en subclases de `ApiError`. Cada una usa una lista de mapeo basada en `isinstance` evaluada de más específico a menos. La entrada final es siempre `(LayerErrorBase, ApiError)` como catch-all.

## 10. Manejadores Globales de Excepciones

Dos manejadores de excepciones del framework forman la red de seguridad final:

1. **Manejador tipado** — captura cualquier `ApiError`, busca el estado HTTP en `_STATUS_MAP` (500 por defecto) y devuelve el envoltorio de error. Para cualquier estado del lado del servidor (>= 500) también registra el error a nivel ERROR con `exc_info`, de modo que la cadena de causas completa `raise ... from exc` — hasta la excepción original del driver/provider — llega a los logs exactamente una vez (§ 9.6). Las respuestas 4xx son resultados esperados del cliente y deliberadamente no se registran.
2. **Manejador genérico** — captura cualquier `Exception` no manejada ya, registra el traceback completo y devuelve un envoltorio de error 500 genérico. Esto nunca debería dispararse si todas las funciones de service siguen la técnica de captura.

## 11. Factoría de la Aplicación

La factoría `create_app()`:

1. Carga las variables de entorno (las variables de entorno del SO tienen precedencia sobre los ficheros `.env`).
2. Crea la instancia del framework con un context manager de lifespan.
3. Añade el middleware CORS.
4. Registra los manejadores de errores.
5. Incluye todos los routers en orden.

### Lifespan

- **Startup**: ejecuta auto-migraciones opcionales y luego precalienta el pool de conexiones.
- **Shutdown**: cierra el pool de conexiones.

## 12. Reglas de los Schemas

- Todos los schemas son subclases de `BaseModel` de Pydantic.
- Los schemas de request definen restricciones de validación (longitud mínima, valores permitidos, etc.).
- Los schemas de response definen el contrato de la API para los clientes.
- Los schemas de error definen el envoltorio de error estándar.

## 13. Reglas de los Helpers de Routers

- Los callables `Depends` compartidos viven en un módulo helper dedicado.
- La dependencia de sesión/auth valida la sesión y devuelve la identidad autenticada.
- Todas las rutas protegidas usan la dependencia de auth.
- Sobrescribe la dependencia de auth en los tests de integración para devolver una identidad de prueba fija.

## 14. Checklist para Añadir un Nuevo Endpoint

- [ ] **Schema** — añade los modelos de request/response en el paquete de schemas.
- [ ] **Service** — añade la función de service. Sigue las convenciones de service: ownership check, envoltura de errores de base de datos, traducción de errores de capa, fallback `except Exception`.
- [ ] **Router** — añade la ruta. Una única llamada a un service, dependencia de auth salvo que sea no autenticada.
- [ ] **Register** — incluye el router en la factoría (app.py) si es un módulo de router nuevo.
- [ ] **Error mapping** — si se necesitan nuevas subclases de `ApiError`, añádelas y registra su estado HTTP.
- [ ] **Unit tests** — Añade todos los unit tests que consideres necesarios, primero lee el CLAUDE.md dentro del directorio tests/unit
- [ ] **Integration tests** — Añade todos los integration tests que consideres necesarios, primero lee el CLAUDE.md dentro del directorio tests/integration
- [ ] **E2E tests** — Añade todos los e2e tests que consideres necesarios, primero lee el CLAUDE.md dentro del directorio tests/e2e
- [ ] **Docs** — actualiza la guía de la API específica del proyecto si los patrones cambian.

## 15. Guía Específica del Proyecto

Este fichero cubre las reglas generales y transferibles de la capa HTTP API. Para los detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`api_guide.md`](api_guide.md).

La guía complementa estas reglas pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, luego la guía específica del proyecto api_guide.md.
