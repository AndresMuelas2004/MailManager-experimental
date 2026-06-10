# Límites de autenticación y gestión de cuentas

Catálogo cuantitativo de **hasta dónde llega** la autenticación y la gestión de cuentas: topes, valores por defecto, permisos OAuth exactos, garantías de seguridad y la lista de "qué NO soporta" con el porqué breve de cada limitación.

El **comportamiento** (flujos, UX, casos borde y el porqué de las decisiones de diseño) está en **[../features/autenticacion-y-cuentas.md](../features/autenticacion-y-cuentas.md)**. Aquí solo van los números y los límites.

Conviene distinguir dos planos a lo largo de este documento:
- **La sesión de la app** (login con Google) — afecta a la identidad del usuario.
- **Las cuentas de correo conectadas** (Gmail / Outlook) — afecta a los buzones que la app gestiona.

---

## 1. Topes y cantidades

| Límite | Valor | Dónde se aplica | Detalle |
|--------|-------|-----------------|---------|
| Proveedores de correo soportados | **2: `gmail`, `outlook`** | Frontend (desplegable) + backend (restricción a nivel de base de datos) | Cualquier otro valor de proveedor se rechaza al guardar la cuenta. |
| Longitud de la etiqueta de cuenta (`display_label`) | **1 a 120 caracteres** | Frontend (campo limitado) + backend (validación) + base de datos | Mínimo 1 carácter (no puede ser cadena vacía). El input del formulario corta a 120. |
| Etiqueta de cuenta obligatoria internamente | **NOT NULL** | Base de datos | En el formulario es **opcional**; si se deja vacío, se rellena con el nombre del proveedor antes de guardar. Nunca se persiste una cuenta sin etiqueta. |
| Cuentas de correo por buzón | **Sin límite** | — | No hay tope codificado. Ver sección 5. |
| Cuentas de correo por usuario | **Sin límite** | — | No hay tope codificado. Ver sección 5. |
| Buzones por usuario | **Sin límite** | — | No hay tope codificado. Ver sección 5. |
| Margen de desfase de reloj al verificar el token de Google | **10 segundos** | Backend (verificación OIDC) | Tolerancia para relojes ligeramente desincronizados; evita rechazar logins legítimos por unos segundos de diferencia. |
| Espera máxima del resultado de la ventana emergente OAuth | **5 minutos (300 segundos)** | Frontend (página de cuentas, ambos proveedores) | Si la ventana de consentimiento no comunica resultado ni se cierra en ese plazo, el intento se da por fallido y el registro de la cuenta se deshace (rollback). |
| Vida del flujo de conexión pendiente en el servidor | **600 segundos (10 minutos)** | Backend (registro en memoria de flujos OAuth pendientes) | El `state` emitido al iniciar la conexión caduca a los 10 minutos; un callback posterior responde "intento desconocido o caducado". El `state` es además de **un solo uso**, y un nuevo intento sobre la misma cuenta invalida el anterior. |
| Sondeo de cierre de la ventana emergente | **cada 500 ms** | Frontend | Frecuencia con la que se detecta que el usuario cerró la ventana sin completar el consentimiento. |
| URL de redirección OAuth de Gmail | **`http://localhost:8000/auth/google/callback` por defecto** (configurable con `GOOGLE_OAUTH_REDIRECT_URI`) | Backend (conexión de Gmail) | El cliente de Google es de tipo "Desktop app": acepta cualquier redirección `http://localhost` sin registrarla en la consola de Google. |
| URL de redirección OAuth de Outlook | **La declarada en el JSON de credenciales** (hoy `http://localhost:8000/auth/outlook/callback`) | Backend (conexión de Outlook) | Debe coincidir **exactamente** con la registrada en el App Registration de Azure; un cambio en cualquiera de los dos lados sin el otro rompe la conexión. |
| Procesos backend compatibles con el flujo de conexión | **1 worker** | Backend | El flujo pendiente entre el inicio y el callback vive en memoria del proceso; con varios workers el callback podría aterrizar en un proceso que no conoce el `state`. Un reinicio del servidor a mitad de flujo también lo invalida. |

---

## 2. Sesión de la aplicación

| Aspecto | Valor | Detalle |
|---------|-------|---------|
| Duración de la sesión | **7 días por defecto** (configurable, mínimo 1 día) | Se controla con la variable de entorno `AUTH_SESSION_LIFETIME_DAYS`. Un valor inferior a 1 se rechaza al arrancar. |
| Renovación por actividad | **No existe** | La caducidad es **fija desde el login** y no se prolonga por usar la app. Al cumplirse el plazo, la sesión deja de valer aunque el usuario estuviera activo. |
| Tipo de cookie | **`HttpOnly`**, **`SameSite=lax`** | Inaccesible desde JavaScript de la página. La duración (`max_age`) coincide con la duración de la sesión. |
| Cookie marcada como `Secure` | **No por defecto** (configurable) | Se controla con `AUTH_COOKIE_SECURE` (por defecto `False` para permitir desarrollo en HTTP local). En producción sobre HTTPS debe ponerse a `True`. |
| Contenido del identificador de sesión | **Opaco** | No contiene datos del usuario; es solo una referencia resuelta server-side contra el almacén de sesiones. |
| Validez de la sesión | **`expires_at > ahora`** | Una sesión caducada se trata como inexistente: la siguiente petición protegida redirige al login. |
| Limpieza de sesiones caducadas | **Best-effort en cada login** | Se purgan sesiones expiradas de forma oportunista; un fallo de limpieza no rompe el login. No hay un cron dedicado. |

---

## 3. Permisos OAuth por proveedor (asimetría Gmail vs Outlook)

Los dos proveedores piden conjuntos de permisos distintos. Esta diferencia es la causa de varias asimetrías de comportamiento.

| Proveedor | Permisos solicitados | Qué cubre |
|-----------|----------------------|-----------|
| **Gmail** | **Un único permiso**: modificación de Gmail (`gmail.modify`) | Cubre de una sola vez leer, enviar, gestionar borradores y mover mensajes entre etiquetas. No incluye borrado permanente de mensajes (ver sección 6). |
| **Outlook** | **Cuatro permisos separados**: leer/escribir correo (`Mail.ReadWrite`), enviar correo (`Mail.Send`), leer perfil básico (`User.Read`) y acceso prolongado (`offline_access`) | `Mail.ReadWrite` permite crear/editar borradores; `Mail.Send` es **independiente** y se necesita para enviar; `User.Read` permite descubrir el email de la cuenta; `offline_access` permite refrescar la sesión sin reconsentimiento. |

### Consecuencias de la asimetría de permisos

- **En Outlook, "enviar" es un permiso aparte de "leer/escribir".** Una cuenta a la que se le recorte `Mail.Send` podrá crear y guardar borradores pero fallará al enviarlos, y el fallo se manifiesta **en un momento distinto** (al enviar, no al conectar). En Gmail esto no ocurre: su permiso es único e indivisible.
- **`offline_access` (Outlook) es lo que permite el refresco silencioso de tokens.** Sin él, la sesión del proveedor no se podría renovar sin volver a molestar al usuario.

---

## 4. Seguridad y almacenamiento de credenciales

| Aspecto | Garantía | Detalle |
|---------|----------|---------|
| Tokens de proveedor en reposo | **Cifrados** (Fernet) | Se cifran con la clave `TOKEN_ENCRYPTION_KEY`. Una clave malformada hace fallar de inmediato — nunca se trata silenciosamente como "ausente". |
| Fallback a texto plano | **Controlado por variable de entorno** | `TOKEN_PLAINTEXT_FALLBACK_ENABLED` (por defecto `True`) permite leer tokens heredados sin cifrar. Si está desactivado y no hay clave de cifrado, guardar tokens falla en lugar de persistir en claro. |
| Migración perezosa a cifrado | **Best-effort en la lectura** | Cuando se lee un token guardado en claro y hay clave de cifrado disponible, se re-cifra de fondo. Un fallo de re-cifrado se registra y se reintenta en la siguiente lectura. |
| Exposición de tokens al cliente | **Nunca** | Ningún token de proveedor viaja en una respuesta de la API; el frontend nunca maneja credenciales de proveedor. |
| Token de sesión en el navegador | **Solo cookie `HttpOnly`** | El frontend nunca escribe sesiones ni tokens en `localStorage`/`sessionStorage`. |
| Email de la cuenta (`email_address`) en reposo | **Texto plano, puede ser `NULL`** | No es un secreto; es un dato cosmético de identificación. `NULL` significa que la consulta best-effort al proveedor falló. |
| CORS | **Orígenes configurables, credenciales permitidas** | `CORS_ALLOWED_ORIGINS` (por defecto `http://localhost:5173`) con envío de credenciales habilitado, necesario para que la cookie de sesión cruce origen. |

---

## 5. "Sin límite" — qué significa exactamente

No existe ningún tope codificado para:

- **Número de cuentas de correo por buzón.**
- **Número de cuentas de correo por usuario.**
- **Número de buzones por usuario.**

Es una decisión del MVP: no se ha implementado ninguna cuota porque el producto asume un único usuario / pocas cuentas. El único freno real es práctico (cada cuenta conectada consume cuota del proveedor al sincronizar y ocupa almacenamiento local), no una validación. Si en el futuro se quisiera limitar, sería una restricción nueva a añadir tanto en el frontend como en el servicio de creación de cuentas.

> Nota: existe un tope no relacionado de **100 borradores por cuenta** en la sincronización de borradores, pero pertenece a otra feature (composición/borradores), no a la gestión de cuentas.

---

## 6. Qué NO soporta (limitaciones aceptadas para el MVP)

| No soporta | Detalle | Por qué |
|------------|---------|---------|
| **Proveedores distintos de Gmail y Outlook** | No hay IMAP genérico, Yahoo, iCloud, Exchange on-premise, etc. | El MVP integra solo las dos APIs (Gmail API y Microsoft Graph). El proveedor está restringido a `gmail`/`outlook` a nivel de base de datos. |
| **Registro de usuario sin login interactivo** | No hay endpoint de "sign up" separado | El primer login con Google es lo que crea al usuario. No se puede crear un usuario por otra vía. |
| **Login con proveedores de identidad que no sean Google** | No hay "entrar con Microsoft", email/contraseña, magic link, etc. | La identidad de la app es exclusivamente Google OIDC en el MVP. (Conectar **cuentas de correo** de Outlook sí es posible; es otra cosa: no es el login de la app.) |
| **Renovación de sesión por actividad** | La sesión caduca a plazo fijo desde el login | Simplicidad del MVP; no hay refresco deslizante de la cookie. |
| **Reconexión automática tras revocación** | Si el usuario revoca el acceso en el proveedor, hay que reconectar manualmente | La app refresca tokens silenciosamente mientras el proveedor lo permita, pero no puede recuperar un acceso revocado sin un nuevo consentimiento. |
| **Cuentas registradas sin conectar ("tarjetas vacías")** | Si la autorización OAuth no se completa, el registro de la cuenta se deshace y no queda tarjeta | Una cuenta sin credenciales no puede hacer nada y la tarjeta no ofrece acción de "reconectar"; conservarla solo acumularía tarjetas muertas. Ver [../features/autenticacion-y-cuentas.md](../features/autenticacion-y-cuentas.md) § 2.2. |
| **Conexión de cuentas con varios workers de backend** | El flujo pendiente entre inicio y callback vive en memoria de un único proceso | Sería necesario un almacén compartido y un estado serializable; el despliegue del MVP usa un solo worker. |
| **Navegadores con ventanas emergentes bloqueadas** | La conexión avisa pidiendo permitir popups y no registra nada | El consentimiento OAuth ocurre en una ventana emergente; sin ella el flujo no puede arrancar. |
| **Recuperar el email de la cuenta si el proveedor no lo da** | La cuenta queda conectada con `email_address = null` | El email es best-effort; bloquear la conexión por un dato cosmético sería desproporcionado. |
| **Borrado permanente de mensajes en Gmail** | El permiso `gmail.modify` no incluye `messages.delete` | El borrado se realiza solo en la base de datos local (decisión documentada en la capa core); el permiso amplio de Gmail aun así no cubre el borrado definitivo en el proveedor. |
| **Cambiar la cuenta de origen de un borrador con adjuntos** | Tras adjuntar el primer archivo, el selector de cuenta se bloquea | Pertenece a la feature de adjuntos, pero afecta a la cuenta usada: mover adjuntos entre cuentas no está soportado. |
| **Dev login en producción / como vía de alta** | El atajo no aparece fuera de modo desarrollo y no crea usuarios | Es una puerta trasera de desarrollo fuertemente acotada (ver sección 7), no un mecanismo de producción. |

---

## 7. Atajo de desarrollo (dev login) — barreras exactas

El endpoint de dev login está acotado por **tres barreras encadenadas**, cada una con su propia respuesta para distinguir el motivo del rechazo:

| Orden | Condición | Variable / valor | Si no se cumple |
|-------|-----------|------------------|-----------------|
| 1 | Debe estar **habilitado** | `DEV_LOGIN_ENABLED` truthy (valores aceptados: `1`, `true`, `yes`, `on`) | **503** `dev_login_disabled` — "este despliegue no lo tiene activado" (se distingue de un problema de credenciales). |
| 2 | La petición debe venir de la **máquina local** | Host del cliente ∈ `DEV_LOGIN_TRUSTED_HOSTS` (por defecto `127.0.0.1`, `::1`, `localhost`) | **403** `dev_login_not_localhost`. |
| 3 | Debe haber un **email de desarrollo** configurado | `DEV_LOGIN_EMAIL` | **500** `env_var_error` (a esta altura ya se sabe que el operador lo habilitó a propósito, así que un email ausente es un bug de configuración). |

Además, el usuario nombrado por `DEV_LOGIN_EMAIL` **debe existir previamente** (se busca por email): el dev login **no crea usuarios**. Si no existe → **404** `user_not_found`. En éxito emite la **misma cookie de sesión opaca** que el login de Google.

---

## 8. Errores de esta área: código y estado HTTP

Correspondencia exacta entre cada situación y la respuesta de la API. El comportamiento percibido se explica en [../features/autenticacion-y-cuentas.md](../features/autenticacion-y-cuentas.md) § 6.

| Situación | Código | Estado HTTP |
|-----------|--------|-------------|
| Credencial de Google inválida / sin `sub` o `email` | `unauthorized` | 401 |
| Token de Google malformado o rechazado por el proveedor | `unauthorized` | 401 |
| Google inalcanzable al verificar (fallo de red) | `external_api_error` | 502 |
| Fallo de autorización al **conectar** una cuenta | `account_connect_auth_error` | **401** (no 409 — evita el bucle de reintentos sobre el mismo paso) |
| Cuenta no conectada al operar sobre ella | `account_not_connected` | 409 |
| Buzón inexistente | `mailbox_not_found` | 404 |
| Buzón ajeno (no eres el dueño) | `forbidden` | 403 |
| Cuenta inexistente (get / update / delete / connect) | `account_not_found` | 404 |
| Usuario inexistente (al borrar / al resolver dev login) | `user_not_found` | 404 |
| Proveedor mal configurado o datos de token/expiry inválidos | `account_misconfigured` | 400 |
| Credenciales de aplicación ausentes / inválidas | `app_credentials_missing` / `app_credentials_invalid` | 500 |
| Dev login deshabilitado | `dev_login_disabled` | 503 |
| Dev login desde host no confiable | `dev_login_not_localhost` | 403 |

> Nota sobre la distinción **404 vs 403**: el acceso a un **buzón** ajeno devuelve 403 (existe pero no es tuyo), mientras que una **cuenta** ajena o inexistente colapsa uniformemente a 404 `account_not_found`, para no filtrar la existencia de cuentas de otros usuarios mediante adivinación de identificadores.

---

## 9. Infraestructura de despliegue (no son límites de producto)

Estos valores son **configuración de despliegue/operación**, no topes que perciba el usuario; se documentan aquí por completitud porque se **validan al arrancar** (un valor inválido aborta el arranque).

| Parámetro | Valor por defecto | Variable de entorno | Detalle |
|-----------|-------------------|---------------------|---------|
| Conexiones mínimas del pool de BD | **1** | `DB_POOL_MIN_CONN` | Conexiones que el pool mantiene abiertas como mínimo. |
| Conexiones máximas del pool de BD | **10** | `DB_POOL_MAX_CONN` | Techo de conexiones concurrentes a PostgreSQL. El mínimo no puede ser mayor que el máximo: si lo es, el arranque falla. |
| Timeout de conexión a la BD | **10 segundos** | `DB_CONNECT_TIMEOUT_SECONDS` | Tiempo máximo para establecer una conexión nueva antes de fallar. |

---

> La autenticación y las cuentas llegan hasta: **login solo con Google OIDC** (única vía de alta de usuario), **sesión en cookie `HttpOnly` de 7 días fijos sin renovación por actividad**, **solo proveedores Gmail y Outlook** sin tope de cuentas ni de buzones, **etiqueta de 1–120 caracteres** (opcional en la UI pero obligatoria internamente), **email de cuenta best-effort que puede quedar `NULL`**, **tokens cifrados en reposo** y nunca expuestos al cliente, y un **dev login de desarrollo tras tres barreras** que jamás crea usuarios; con Gmail pidiendo un permiso único y Outlook cuatro permisos separados (el envío entre ellos). El comportamiento completo está en [../features/autenticacion-y-cuentas.md](../features/autenticacion-y-cuentas.md).
