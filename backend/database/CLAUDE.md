# Reglas Generales de la Capa de Base de Datos

Este es el `CLAUDE.md` de la capa de **persistencia en base de datos**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de gestión y escalado de errores, sus reglas estructurales y su comportamiento común. Todo lo que se cubre aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico al proyecto por diseño.** Nada aquí referencia un dominio, entidad o funcionalidad concreta. Cada regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero a un nuevo proyecto para establecer la arquitectura de la capa de base de datos desde el primer día. La guía específica del proyecto extiende estas reglas con detalles de dominio, pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas tienen precedencia.
**Inmutable.** Este fichero nunca debe editarse. Todos los cambios específicos del proyecto van en el fichero `*_guide.md` referenciado al final de este documento.
## 1. Aislamiento de la Capa

El paquete `database/` es una capa agnóstica al framework — **no tiene imports de `api/`**, `core/` ni `auth/`. Los servicios de la capa API traducen las subclases de `DatabaseError` en subclases de `ApiError` mediante una función de traducción.

## 2. Estructura del Paquete

```
  database/
  ├── __init__.py              # Public facade (re-exports everything below)
  ├── errors/                  # Database-specific error hierarchy
  │   ├── __init__.py          #   Re-exports all exceptions
  │   └── exceptions.py        #   DatabaseError base + subclasses
  ├── settings.py              # Centralized env var reading and validation
  ├── connection.py            # Connection pool management + transactional context manager
  ├── lifecycle.py             # Pool warmup and schema migration orchestration
  ├── contracts.py             # Abstract interfaces (Store contracts)
  ├── queries/                 # Raw SQL constants only — no Python logic
  ├── repositories/            # Concrete implementations of contracts
  ├── security/                # (optional) Credentials and token encryption utilities
  └── migrations/              # Schema evolution (Alembic or equivalent)

  
```

## 3. Fachada Pública

Todo el código externo importa desde la raíz del paquete (`from database import ...`). El `__init__.py` re-exporta:

- Instancias singleton de los Store
- Funciones de gestión del pool (close, warmup)
- Helpers de migración
- Funciones de carga de credenciales

Los consumidores externos **nunca importan desde submódulos internos**.

## 4. Flujo Interno de Datos

### Runtime (manejo de peticiones)

  repositories/
    → queries/          (SQL constants)
    → connection.py     (pool access)
    → contracts.py      (abstract interfaces)
    → security/         (optional — token encryption)

  security/
    → settings.py       (credential paths, encryption keys)

  connection.py
    → settings.py       (pool config)

  settings.py
    → os.environ        (the only module that reads env vars)

  ### Arranque (bootstrap)

  lifecycle.py
    → connection.py        (health-check / warmup)
    → settings.py          (migration config)
    → migrations/runner.py (schema evolution, when enabled)

## 5. Reglas de Frontera de la Capa

- **`settings.py`** — el único módulo que lee `os.environ`.
- **`connection.py`** — el único módulo que gestiona el connection pool.
- **`queries/`** — contiene únicamente constantes de cadenas SQL. Cero imports, cero lógica.
- **`repositories/`** — los únicos módulos que ejecutan SQL. Combinan una query con una conexión y lanzan subclases específicas de `DatabaseError` en caso de fallo.
- **`contracts.py`** — define interfaces abstractas que desacoplan los servicios de las implementaciones concretas.
- **`__init__.py`** — re-exporta todo. El código externo nunca importa submódulos directamente.

## 6. Jerarquía de Errores

La capa de base de datos usa su propia jerarquía `DatabaseError`, independiente de la jerarquía de errores de la API.

```
 DatabaseError                   # Base for all database errors
  ├── ConnectionPoolError         # Pool creation, warmup, connection exhaustion
  ├── QueryError                  # Any SQL execution failure (CRUD)
  ├── MigrationError              # Schema migration failures
  ├── SettingsError               # Missing/invalid env vars, malformed keys
  └── ...                         # Project-specific subclasses (see guide)
```


Cada clase tiene un `code`, un `default_message`, un `message` y un dict `detail` — el mismo patrón de clase base que otras capas.

## 7. Técnica de Captura

Cada bloque `try` de la capa de base de datos sigue este patrón ordenado:

```python
try:
    # ... operation ...
except specific_db_lib.SpecificError:       # 1. Specific DB library error first
    return None                             #    (graceful handling where applicable)
except DatabaseError:                        # 2. Never double-wrap
    raise
except db_lib.Error as exc:                 # 3. Domain-specific catch
      raise <ModuleError>("Failed to ...") from exc
except Exception as exc:                    # 4. Generic fallback last
      raise <ModuleError>(
          f"Unexpected error ({type(exc).__name__}): {exc}"
      ) from exc
```

### Reglas

1. **Errores de la librería de BD específicos primero** (paso 1) — solo donde aplique (p. ej. UUID inválido → `None`/`[]` de forma controlada).
2. **Nunca envolver dos veces un `DatabaseError`** (paso 2) — esta guarda solo hace falta cuando el código dentro del bloque `try` puede lanzar una subclase de `DatabaseError`, ya sea explícitamente o vía un helper interno. El `except DatabaseError: raise` lo relanza antes de que el `except Exception` genérico pueda capturarlo y envolverlo. **Si nada dentro del `try` puede producir un `DatabaseError`, esta guarda es innecesaria.**
3. **Captura específica de dominio** (paso 3) — todas las subclases de errores de la librería de BD se mapean a la excepción apropiada (`QueryError` para repositorios, `ConnectionPoolError` para el pool, `MigrationError` para migraciones).
4. **Fallback genérico al final** (paso 4) — garantiza que ninguna excepción escape sin tipar. El mensaje incluye `type(exc).__name__` para facilitar la depuración. Las capas internas pueden incluir estos detalles ya que los errores siempre se traducen antes de llegar al cliente.
5. **Preservar la cadena de causa** — siempre `raise ... from exc`.
6. **Envolver y relanzar — nunca loguear el traceback.** Los repositorios traducen y relanzan; no deben loguear las excepciones que envuelven. La cadena `raise ... from exc` transporta el error original del driver hasta los handlers globales de la capa API, el único lugar donde se loguean los fallos del lado del servidor — loguear aquí duplicaría ese registro. La única excepción: un error que esta capa captura y descarta deliberadamente (p. ej. un retorno controlado `None`/`[]`, paso 1) nunca llega a esos handlers, así que cuando la causa descartada importa para el diagnóstico, el propio punto de descarte debe loguearla con `exc_info=exc`.

### Dónde se lanza cada excepción

  - **`connection.py`** → `ConnectionPoolError` (creación del pool, agotamiento del pool)
  - **`lifecycle.py`** → `ConnectionPoolError` (warmup), `MigrationError` (migraciones)
  - **`repositories/*.py`** → `QueryError` (fallos de SQL)
  - **`settings.py`** → `SettingsError` (env vars ausentes/inválidas)
  - Los módulos específicos del proyecto (p. ej. `security/`) lanzan sus propias subclases de `DatabaseError` según se define en la guía del proyecto.

## 8. Patrón de Contrato

Las interfaces abstractas de store en `contracts.py` definen la API pública de cada dominio de datos:

- Cada contrato es una clase abstracta con firmas de método tipadas.
- Las implementaciones concretas viven en `repositories/`.
- Las instancias singleton se crean a nivel de módulo y se re-exportan vía la fachada.
- Los servicios dependen de los contratos abstractos, no de las implementaciones concretas.

## 9. Reglas de Settings

- `settings.py` es el **único módulo** que lee `os.environ`.
- Las env vars requeridas ausentes o inválidas lanzan `SettingsError`.
- Los settings se organizan por área: conexión, tuning del pool, migraciones, cifrado, credenciales.

## 10. Reglas de Migración

- Los cambios de esquema se gestionan con una herramienta de migración (Alembic o equivalente).
- Las migraciones son la fuente de verdad para la evolución del esquema — no los snapshots de `schema.sql`.
- El auto-migrate en el arranque está deshabilitado por defecto; se habilita vía env var para desarrollo.
- Recomendación de producción: ejecutar las migraciones en CI/CD antes del despliegue de la API.

## 11. Reglas de Seguridad

El sub-paquete security/ es opcional. Los proyectos que no almacenan tokens cifrados ni ficheros de credenciales en la capa de base de datos pueden omitirlo por completo junto con sus subclases de error asociadas. Cuando está presente,
aplican las siguientes reglas:
- Los datos sensibles (tokens, credenciales) se cifran en reposo.
- Las claves de cifrado se cargan desde env vars vía `settings.py`.
- Los ficheros de credenciales se cargan desde rutas especificadas en env vars.
- El fallback legacy a texto plano se controla mediante env vars explícitas — nunca de forma silenciosa.
- Una clave de cifrado malformada lanza `SettingsError` de inmediato — nunca se trata silenciosamente como ausente.

## 12. Traducción en el Lado del Servicio

Los servicios traducen los errores de base de datos mediante bloques `try`/`except` explícitos usando `translate_database_error`:

```python
try:
    record = store.get(resource_id)
except DatabaseError as exc:
    raise translate_database_error(exc) from exc
except Exception as exc:
    logger.warning("Unexpected <operation> error (%s): %s", type(exc).__name__, exc)
    raise ApiError("Failed to <operation>.") from exc
```

`translate_database_error` mapea las subclases de `DatabaseError` a subclases de `ApiError` mediante el mapeo. El fallback `except Exception` captura errores no-BD verdaderamente inesperados.

## 13. Guía Específica del Proyecto

Este fichero cubre las reglas generales y transferibles de la capa de persistencia en base de datos. Para detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`database_guide.md`](database_guide.md).

La guía complementa estas reglas pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, después la guía específica del proyecto database_guide.md.
