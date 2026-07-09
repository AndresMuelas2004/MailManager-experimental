> **Regla permanente — léela antes de editar este fichero.**
>
> Este fichero se carga en el contexto en cada sesión de Claude. Una línea aquí solo justifica sus tokens si no puede reconstruirse leyendo el código.
>
> **Antes de escribir o conservar una línea, pregúntate: ¿podría reconstruir esto abriendo el/los fichero(s) relevante(s) durante ~30 segundos?**
> - **SÍ → bórrala.** El código es la fuente de verdad. Los catálogos de lo que hacen módulos / funciones / tests, las paráfrasis de nombres o cuerpos, las enumeraciones exhaustivas de kwargs / campos / config, las tablas de flujo que reflejan nombres de fichero o de símbolo ya existentes, y las recetas paso a paso para código que ya es legible por sí mismo caen todas aquí. Bórralas en cuanto las veas.
> - **NO → consérvala.** Trampas silenciosas al extender la capa, asimetrías entre ficheros (hermanos que no se comportan igual), reglas de orden / ciclo de vida cuya violación lo rompe todo, invariantes cuya regresión silenciosa se colaría en la revisión, decisiones históricas cuya razón de ser no está en el código, e identificadores fijos (UUIDs, datos sembrados, constantes mágicas) que no pueden recomputarse — esos sí ganan sus tokens.
>
> **Al actualizar este fichero, relee cada sección y borra todo lo que desde entonces haya migrado al código.** La obsolescencia es peor que el silencio.

# Guía de la Capa Auth

> **Reglas generales**: esta capa DEBE respetar todas las reglas definidas en
> [`CLAUDE.md`](./CLAUDE.md).
> El presente documento contiene detalles específicos del proyecto que complementan esas reglas.

**Regla de autoridad**: el código de esta capa debe respetar lo documentado aquí. Si hay una discrepancia entre esta guía y el código existente, esta guía es la referencia — corrige el código, no la guía. Cuando se añada nueva funcionalidad, actualiza esta guía al final de la tarea para reflejar la nueva realidad.

## Trampas

### Orden de captura de subclases — el error de red debe capturarse **antes** que su base

Una excepción de red/transporte que sea **subclase** del error genérico del proveedor debe capturarse primero, o una caída del endpoint de verificación se clasifica erróneamente como un token malo: el usuario ve un 401 "invalid token" cuando el fallo real es "servicio de verificación inalcanzable" (que debe aflorar como 502 vía `AuthTokenNetworkError`). Cada proveedor lo expresa de forma distinta:

- **Google** (`google.py`): `google.auth.exceptions.TransportError` es subclase de `GoogleAuthError`.
- **Microsoft** (`microsoft.py`): `jwt.exceptions.PyJWKClientConnectionError` es subclase de `PyJWKClientError` (ambas lanzadas por `PyJWKClient.get_signing_key_from_jwt`). La subclase de conexión → `AuthTokenNetworkError` (502); la base "kid not found" → `AuthTokenInvalidError` (401).

### Microsoft: JWK / JWKS / backend criptográfico roto es infra → 502, no una 4ª subclase de error

`microsoft.py` mapea `PyJWKError` / `PyJWKSetError` / `InvalidKeyError` (material de clave malformado, backend criptográfico roto) a `AuthTokenNetworkError`, **no** a `AuthTokenInvalidError`: una obtención de clave de firma rota es "nuestro lado / la infraestructura del proveedor", no "el token del usuario es malo". Esto reutiliza deliberadamente la subclase 502 existente en lugar de inventar una específica del proveedor (`CLAUDE.md` §9 — solo añade una subclase cuando un proveedor necesita una respuesta HTTP *diferente*).

### Guard de nunca-envolver-dos-veces — ahora REQUERIDO por `microsoft.py` (Google sigue sin necesitarlo)

El patrón del guard en `auth/CLAUDE.md` §7 regla 6 es condicional. `google.py` sigue sin necesitarlo: `id_token.verify_oauth2_token(...)` no puede producir un `AuthError`. `microsoft.py` **sí** lo necesita y lo lleva — el cuerpo de su `try` lanza `AuthTokenInvalidError` / `AuthTokenNetworkError` directamente (la pre-comprobación de `tid`, el mapeo por-cláusula de `get_signing_key_from_jwt`, la re-vinculación post-verificación de `iss`/`tid`), de modo que sin

```python
except AuthTokenError:  # re-raise before the generic catch
    raise
```

el `except Exception` final re-envolvería esos errores tipados como `AuthTokenInvalidError`, colapsando la distinción red/inválido (y convirtiendo un 502 en un 401).

### La tenancy `common` de Microsoft no tiene issuer fijo — vincula `iss` al propio `tid` del token

`verify_microsoft_token` apunta a la autoridad multi-tenant `common`, cuyo `iss` es `https://login.microsoftonline.com/{tid}/v2.0` y por tanto no es una constante. La función lee `tid` del payload **sin verificar** solo para construir el issuer esperado (re-validado con forma de GUID), verifica el token contra ese issuer, y luego re-comprueba `iss == .../{verified tid}/v2.0` sobre los claims ya verificados (cinturón y tirantes). Un revisor que "simplifique" esto a una constante de issuer hardcodeada rechazaría todo tenant legítimo o aceptaría tokens de uno ajeno.

### La validación de claims vive en el servicio, no en la capa auth

`verify_google_token` solo verifica la validez criptográfica y la emisión por el proveedor. Las comprobaciones de lógica de negocio (`sub` presente, `email` presente, etc.) pertenecen a `auth_service.google_login`, que lanza `Unauthorized`. Esta separación mantiene la capa auth reutilizable entre endpoints y libre de cuestiones de la capa API.

### Microsoft vs Google — asimetrías que viven en los ficheros de la capa auth

Dos asimetrías entre proveedores viven en los propios ficheros de la capa auth (las reglas de claims del *lado del servicio* — fallback `email` → `preferred_username` y `email_verified` NO comprobado — están documentadas en `api_guide.md` § "New identity provider"):

- **`MICROSOFT_CLIENT_ID` es opcional en `settings.py`** (por defecto `""`), a diferencia de `GOOGLE_CLIENT_ID`, cuya ausencia lanza `AuthSettingsError`. El guard "¿está Microsoft configurado?" vive en el punto de uso (`auth_service.microsoft_login` → `EnvVarError`), de modo que un despliegue solo-Google sigue cargando settings y arrancando. NO muevas el guard a `get_auth_settings` — rompería los despliegues solo-Google y todos los tests que solo establecen `GOOGLE_CLIENT_ID`.
- **`microsoft.py` usa `leeway = 60 s`; `google.py` usa `10 s`.** Microsoft Entra no especifica una tolerancia de desfase de reloj, así que la ventana más generosa es intencionada. No "alinees" los dos valores.

## Extensión — nuevo proveedor de identidad

Ver el checklist general en `auth/CLAUDE.md` §9. Añadidos específicos del proyecto:

- Las subclases existentes de `AuthTokenError` (`AuthTokenNetworkError`, `AuthTokenInvalidError`, `AuthTokenProviderError`) son agnósticas al proveedor. Crea una nueva subclase solo cuando un proveedor introduzca un modo de fallo que necesite una respuesta HTTP diferente o un manejo distinto en el lado del cliente.
- Al añadir un nuevo módulo de proveedor, extiende la trampa "Orden de captura de subclases" de arriba con la relación de subclases específica de ese proveedor (o confirma que no aplica).
