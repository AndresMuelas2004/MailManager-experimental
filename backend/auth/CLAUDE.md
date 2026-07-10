# Reglas Generales de la Capa Auth

Este es el `CLAUDE.md` de la capa de **autenticación**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de manejo de errores y escalado, sus reglas estructurales y su comportamiento común. Todo lo que se cubre aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstica al proyecto por diseño.** Nada de aquí referencia un dominio, entidad o funcionalidad concreta. Toda regla se aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la arquitectura de la capa auth desde el primer día. La guía específica del proyecto extiende estas reglas con detalles de dominio, pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas tienen precedencia.
**Inmutable.** Este fichero nunca debe editarse. Todos los cambios específicos del proyecto van en el fichero `*_guide.md` referenciado al final de este documento.
## 1. Aislamiento de la Capa

El paquete `auth/` es una capa agnóstica al framework — **no tiene imports de `api/`**. Los servicios de la capa API traducen las subclases de `AuthError` a subclases de `ApiError` mediante una función de traducción (el mismo patrón usado para los errores de core y database).

## 2. Estructura del Paquete

```
auth/
├── __init__.py              # Public facade (re-exports everything below)
├── errors/                  # Auth-specific error hierarchy
│   ├── __init__.py          #   Re-exports all exceptions
│   └── errors.py            #   AuthError base + subclasses
├── settings.py              # Centralized env var reading and validation
└── <provider>_auth/         # One directory per identity provider
    ├── __init__.py
    └── <provider>.py         #   verify_<provider>_token — pure token verification
```

## 3. Fachada Pública

Todo el código externo importa desde la raíz del paquete (`from auth import ...`). El `__init__.py` reexporta:

- Clases de error (jerarquía completa)
- Dataclass de settings y su función loader
- Funciones de verificación de proveedores

Los consumidores externos **nunca importan desde submódulos internos** — solo desde la fachada.

## 4. Reglas de Settings

- `settings.py` es el **único módulo** que lee `os.environ`.
- Los settings se devuelven como una dataclass frozen.
- Las env vars requeridas ausentes o inválidas lanzan `AuthSettingsError`.
- Los settings se cargan por cada llamada de servicio (no se cachean globalmente), de modo que los cambios en las env vars surten efecto sin reinicio.

## 5. Contrato de Verificación de Token

Cada módulo de proveedor de identidad expone una única función de verificación:

```python
def verify_<provider>_token(raw_token: str, ...) -> dict:
    """
    Verify a token and return the decoded claims.
    Raises AuthTokenError subclasses on any verification failure.
    """
```

La función debe:

1. Aceptar el string del token en crudo y cualquier configuración específica del proveedor.
2. Devolver un `dict` de claims decodificados en caso de éxito.
3. Lanzar únicamente subclases de `AuthTokenError` ante un fallo — nunca excepciones crudas del proveedor.
4. Seguir la técnica de captura descrita en § 7.

La validación de claims (lógica de negocio como comprobar `sub`, `email`) permanece en la capa de servicio, no en la capa auth. La capa auth solo verifica la validez criptográfica.

## 6. Jerarquía de Errores

Todos los errores siguen el mismo patrón de clase base: cada clase tiene un `code`, `default_message`, `message` y un dict `detail`.

```
AuthError                           # Base for all auth errors
├── AuthSettingsError               # Missing/invalid auth env vars
└── AuthTokenError                  # Base for token verification failures
    ├── AuthTokenNetworkError       # Transport/network failure during verification
    ├── AuthTokenInvalidError       # Malformed or bad-format token
    └── AuthTokenProviderError      # Provider rejected the token
```

### Notas semánticas

- Los nombres de error son **agnósticos al proveedor** — reutilizables entre proveedores de identidad.
- `AuthTokenNetworkError` mapea a un equivalente de 502, no 401. Un fallo de transporte no es un token inválido — el token puede ser válido, pero el endpoint de verificación es inalcanzable. Esto permite a los clientes diferenciar «tu token es malo» de «el servicio de verificación está caído».

## 7. Técnica de Captura

Toda función de verificación de proveedor sigue estas reglas al capturar excepciones.

### Reglas

1. **Captura primero las excepciones específicas del proveedor.** Enumera los tipos de excepción concretos que la librería del proveedor puede lanzar, ordenados de más específico a más general.
2. **Mapea a la subclase de `AuthTokenError` correcta.** Cada excepción del proveedor mapea a la subclase que mejor describe el fallo *funcional*: errores de red → `AuthTokenNetworkError`, rechazos del proveedor → `AuthTokenProviderError`, errores de formato → `AuthTokenInvalidError`.
3. **Captura explícitamente el `ValueError` incorporado.** Algunas librerías de verificación lanzan `ValueError` para tokens malformados. Captúralo antes del handler genérico y mapéalo a `AuthTokenInvalidError`.
4. **El fallback genérico va el último.** Un `except Exception as exc` final con un mensaje que incluya `type(exc).__name__` asegura que ninguna excepción escape sin tipar. Mapea a `AuthTokenInvalidError` como el default más seguro. Las capas internas pueden incluir `type(exc).__name__` en los mensajes de error, ya que estos siempre se traducen antes de llegar al cliente.
5. **Preserva la cadena de causas.** Siempre `raise ... from exc`.
6. **Nunca envuelvas dos veces errores ya tipados.** Esta regla se aplica cuando el código dentro de un bloque `try` puede lanzar una subclase de `AuthError` — ya sea mediante un `raise` explícito o a través de un helper que lance una. Añade un `except AuthTokenError: raise` dirigido **antes** del handler genérico `except Exception`. **Si nada dentro del `try` puede producir un `AuthError`, el guard es innecesario.**
7. **Envuelve y relanza — nunca loguees el traceback.** Las funciones de verificación traducen y relanzan; no deben loguear las excepciones que envuelven. La cadena `raise ... from exc` transporta el error original del proveedor hasta los handlers globales de la capa API, el único lugar donde se loguean los fallos del lado del servidor — loguear aquí duplicaría ese registro.

### Patrón

```python
try:
    return provider_sdk.verify(token, ...)
except ProviderNetworkError as exc:       # 1. Most specific provider error
    raise AuthTokenNetworkError(...) from exc
except ProviderBaseError as exc:          # 2. Provider rejection
    raise AuthTokenProviderError(...) from exc
except ValueError as exc:                # 3. Malformed token
    raise AuthTokenInvalidError(...) from exc
except Exception as exc:                 # 4. Generic fallback
    raise AuthTokenInvalidError(
        f"Unexpected verification error ({type(exc).__name__}): {exc}"
    ) from exc
```

## 8. Traducción en la Capa de Servicio

La capa de servicio captura `AuthError` y traduce mediante una función de mapeo:

```python
try:
    claims = verify_<provider>_token(raw_token, ...)
except AuthError as exc:
    raise translate_auth_error(exc) from exc
except Exception as exc:
    logger.warning("Unexpected error (%s): %s", type(exc).__name__, exc)
    raise Unauthorized("Token verification failed.") from exc
```

No existe ningún context manager para la traducción de auth — normalmente solo hay unos pocos sitios de captura.

## 9. Checklist para Añadir un Nuevo Proveedor de Identidad

### Capa auth

- [ ] Crear `auth/<provider>_auth/<provider>.py` con `verify_<provider>_token(...)`.
- [ ] Seguir la técnica de captura — mapear las excepciones del proveedor a subclases de `AuthTokenError`.
- [ ] Añadir el guard de nunca-envolver-dos-veces solo si los helpers internos lanzan subclases de `AuthError`.
- [ ] Si se necesitan nuevas env vars, añadirlas a `settings.py` y a la dataclass de settings.
- [ ] Reexportar la función de verificación desde `auth/__init__.py`.

### Capa de servicio

- [ ] Añadir una función de servicio (p. ej. `<provider>_login`) en el servicio de auth.
- [ ] Capturar `AuthError` y traducir mediante la función de traducción.
- [ ] Añadir la validación de claims (lógica de negocio) en el servicio, no en la capa auth.

### Jerarquía de errores

- Las subclases existentes de `AuthTokenError` son agnósticas al proveedor y deberían cubrir la mayoría de escenarios. Crea nuevas subclases solo si un proveedor introduce un modo de fallo que requiera una respuesta HTTP diferente o un manejo distinto en el lado del cliente.

### Router / schema

- [ ] Añadir un nuevo endpoint (p. ej. `POST /auth/<provider>`) — un endpoint por proveedor.
- [ ] Añadir schemas de request/response.

### Tests y docs

- [ ] Tests unitarios para la función de verificación.
- [ ] Tests unitarios para la función de servicio.
- [ ] Tests de integración para el nuevo endpoint.
- [ ] Actualizar la documentación con el orden de excepciones del nuevo proveedor.

## 10. Principios de Diseño

- Mantén la lógica de verificación específica del proveedor dentro de los módulos de proveedor.
- Mantén las cuestiones de la capa API fuera del código de auth — sin imports de `api/`.
- Mantén los settings centralizados — el único módulo que lee env vars.
- Mantén los nombres de error agnósticos al proveedor — reutilizables entre proveedores.
- Mantén la validación de claims (lógica de negocio) en la capa de servicio, no en la capa auth.

## 11. Guía Específica del Proyecto

Este fichero cubre las reglas generales y transferibles de la capa de autenticación. Para los detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`auth_guide.md`](auth_guide.md).

La guía complementa estas reglas pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, luego la guía específica del proyecto auth_guide.md.
