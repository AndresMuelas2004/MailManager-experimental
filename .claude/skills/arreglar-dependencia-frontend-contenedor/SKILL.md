---
name: arreglar-dependencia-frontend-contenedor
description: >-
  Diagnostica y arregla el error de Vite "[plugin:vite:import-analysis] Failed
  to resolve import "<paquete>" ... Does the file exist?" en el frontend
  dockerizado (overlay rojo en http://localhost:5173, el frontend no carga).
  Causa típica: una dependencia npm declarada en frontend/package.json y
  package-lock.json pero AUSENTE del node_modules del contenedor, porque el
  volumen anónimo node_modules quedó desactualizado tras un merge/pull/cambio de
  rama que añadió la dependencia (p. ej. @azure/msal-browser tras el login de
  Microsoft). Úsala cuando: el frontend falle al resolver el import de un paquete
  de node_modules, aparezca "Failed to resolve import" / "Does the file exist?",
  el front no arranque tras instalar/actualizar dependencias, o justo después de
  levantar el stack con podman compose. Reinstala dentro del contenedor con los
  flags obligatorios y reinicia Vite. NO aplica si el import roto es una ruta
  relativa a un fichero propio del proyecto en vez de un paquete npm.
allowed-tools: Read, PowerShell, Bash
---

# arreglar-dependencia-frontend-contenedor — Reinstalar dependencias npm faltantes en el contenedor frontend

El frontend corre en el contenedor `mailmanager-frontend-1` (Podman compose, servicio `frontend`). Su `node_modules` se sirve desde un **volumen anónimo** (`/app/node_modules` en `compose.yml`) montado por encima del bind `./frontend:/app`. Ese volumen se pobló en un `npm install` durante el build de la imagen y **persiste entre reinicios**. Cuando un merge / pull / cambio de rama añade una dependencia nueva a `package.json`, el código nuevo (con el `import`) entra por el bind, pero el volumen sigue sirviendo el `node_modules` viejo → Vite falla con `[plugin:vite:import-analysis] Failed to resolve import "<paquete>"`.

## Cuándo aplica

- Overlay rojo de Vite en http://localhost:5173 con `Failed to resolve import "<paquete>"` / `Does the file exist?`.
- El `<paquete>` es una dependencia de **node_modules** (un paquete npm: `@azure/msal-browser`, `lucide-react`, etc.), no una ruta relativa.
- Suele aparecer justo después de levantar el stack tras un merge/pull que añadió dependencias.

## Cuándo NO aplica (no uses esta skill)

- El import roto es una **ruta relativa** (`./algo`, `../algo`): es un fichero que falta o una ruta mal escrita, no este problema.
- El `<paquete>` **no** está en `frontend/package.json`: falta declarar la dependencia (el fix es añadirla con `npm install <pkg> ...`), no solo reinstalar.

## Procedimiento

### Paso 0 — Contenedor arriba

```powershell
podman ps --filter "name=mailmanager-frontend" --format "{{.Names}}`t{{.Status}}"
```

Si no aparece o está `Exited`, levanta el stack: `podman compose up -d`. (El nombre asume el proyecto compose `mailmanager`; ajústalo si trabajas en otro stack/worktree.)

### Paso 1 — Confirmar la causa (diagnóstico, antes de tocar nada)

Toma el `<paquete>` del mensaje de error (lo que va entre comillas tras `Failed to resolve import`) y verifica los tres puntos de una vez:

```powershell
$pkg = "@azure/msal-browser"   # <-- sustituye por el paquete del error
podman exec mailmanager-frontend-1 sh -lc "echo -n 'package.json: '; grep -c '$pkg' /app/package.json; echo -n 'lock: '; grep -c '$pkg' /app/package-lock.json; ls -d /app/node_modules/$pkg 2>/dev/null && echo FOUND || echo MISSING"
```

Es **este** caso si: el paquete aparece en `package.json` (≥1) y en `package-lock.json` (≥1) pero `node_modules` da **MISSING**. Si da `FOUND`, el problema es otro (para). Si no está en `package.json`, primero hay que declarar la dependencia.

### Paso 2 — Reinstalar dentro del contenedor

```powershell
podman exec mailmanager-frontend-1 npm install --legacy-peer-deps --ignore-scripts
```

**Los dos flags son obligatorios** (documentado en el `CLAUDE.md` raíz, sección *Containerisation*):

- `--legacy-peer-deps`: sin él, npm 10 falla con `ERESOLVE` por el peer `@testing-library/react@16` (pide `@types/react@^18`) frente al `@types/react@^19` del proyecto.
- `--ignore-scripts`: sin él, el script `prepare` (husky vía `git config`) aborta con exit 127 porque el contenedor no tiene `git`.

### Paso 3 — Reiniciar Vite (optimizeDeps limpio)

```powershell
podman restart mailmanager-frontend-1
```

Necesario porque Vite mantiene el pre-bundle de dependencias (`optimizeDeps`) en memoria; el overlay de error persiste aunque el paquete ya esté instalado. El reinicio detecta el lockfile cambiado y reoptimiza limpio (verás en logs `Re-optimizing dependencies because lockfile has changed`).

### Paso 4 — Verificar (sin navegador)

No navegues la app con Playwright ni la abras en localhost para "comprobar": la regla §12 del `CLAUDE.md` del proyecto reserva la verificación en vivo al usuario salvo petición explícita. Verifica por CLI/HTTP:

```powershell
# Pide a Vite el módulo que fallaba, transformado (usa la ruta del mensaje de error tras "from")
Invoke-WebRequest "http://localhost:5173/src/features/auth/hooks/useMicrosoftLogin.ts" -UseBasicParsing | Select-Object -ExpandProperty StatusCode
```

- **200** = el import resuelve, error arreglado. **500** = sigue roto.
- Refuerzo: `podman exec mailmanager-frontend-1 sh -lc "ls -d /app/node_modules/$pkg"` debe existir, y `podman logs --tail 20 mailmanager-frontend-1` debe mostrar `ready in ...` sin errores de import posteriores al reinicio.

## Fallback si reinstalar no basta

Si el overlay persiste tras el restart (caché de Vite corrupta o `node_modules` inconsistente):

```powershell
podman exec mailmanager-frontend-1 sh -lc "rm -rf /app/node_modules/.vite"   # limpia la caché de optimizeDeps
podman restart mailmanager-frontend-1
```

Último recurso (más lento, recrea el volumen anónimo): `podman compose down`, eliminar el volumen `node_modules` del frontend y `podman compose up -d --build`. Evítalo salvo que lo anterior falle.

## Cierre

Resume al usuario: el paquete reinstalado, que se reinició Vite, y el resultado de la verificación (200 / módulo presente).
