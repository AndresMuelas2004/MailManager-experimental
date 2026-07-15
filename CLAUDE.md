# CLAUDE.md

  Este fichero proporciona orientación a Claude Code (claude.ai/code) cuando trabaja con el código de este repositorio.

  Los detalles específicos del proyecto se mantienen en @repository_guide.md (auto-importado al contexto).

  ---

  ## Arquitectura General 

  Esta sección describe la arquitectura por capas, las reglas estructurales y las convenciones que aplican a cualquier proyecto que siga este patrón. Es agnóstica del proyecto y no debe
  modificarse por cambios específicos del dominio.

  ### 1 Reglas de Capa (Auto-Cargadas)

  Cada capa tiene su propio `CLAUDE.md` que Claude Code carga automáticamente al leer ficheros en ese directorio. Estos `CLAUDE.md` a nivel de capa son agnósticos del proyecto,
  transferibles, y **nunca deben modificarse**. Cada uno referencia internamente un `*_guide.md` con los detalles específicos del proyecto.

  Capas con su propio `CLAUDE.md`:
  - `backend/api/`
  - `backend/auth/`
  - `backend/database/`
  - `backend/core/`
  - `backend/Scripts/`
  - `backend/tests/unit/`
  - `backend/tests/integration/`
  - `backend/tests/e2e/`
  - `frontend/`
  - `docs/` (más sus subdirectorios `docs/features/` y `docs/limits/`, cada uno con su propio `CLAUDE.md`)

  El árbol `docs/` es la excepción a la referencia a `*_guide.md`: sus ficheros `CLAUDE.md` no referencian ningún `*_guide.md` — el contenido específico del proyecto son los propios documentos, indexados por el `README.md` de cada subdirectorio (ver § 5).

  **Regla estricta**: estas reglas de capa son innegociables y prevalecen sobre cualquier orientación específica del proyecto que las contradiga.

  ### 2 Estructura del Monorepo

  - `backend/` — Servidor API organizado en capas (FastAPI + Python).
  - `frontend/` — Aplicación cliente (React + Vite + TypeScript + Tailwind).
  - `docs/` — Documentación narrativa del proyecto para el equipo (comportamiento de las features + catálogos de límites). Ver § 5.

  ### 3 Directorios Excluidos

  - `backend/Scripts/` — scripts personales del desarrollador (pruebas manuales, utilidades puntuales). Claude **no** debe leer, editar ni referenciar ficheros de este directorio salvo que el usuario lo solicite explícitamente. Estos scripts no tienen relación con la lógica de negocio de la aplicación, son solo para probar ejecuciones manuales.

  ### 4 Capas del Backend y sus Relaciones

  → API (routers → services → resto de las capas)
  → Auth       (verificación de identidad, gestión de sesiones)
  → Database   (persistencia)
  → Core       (lógica de dominio, clientes de proveedor)

  Reglas de comunicación:

  - Solo los **Services** (dentro de API) hablan con Auth, Database y Core.
  - Auth, Database y Core son **independientes** — ninguno importa de otro.
  - Ninguna capa inferior importa de API.

  Cada capa define su propia jerarquía de errores. Los Services traducen los errores de las capas inferiores en errores de la capa API. Para los detalles concretos, consulta el `CLAUDE.md` de la capa (auto-cargado al leer ficheros en ese directorio).

  ### 5 Patrón de Documentación de Dos Niveles

  Cada capa tiene dos ficheros de documentación:

  - `CLAUDE.md` (en el directorio de cada capa) — reglas generales y transferibles. No se modifican por cambios del proyecto. Auto-cargado por Claude Code al leer ficheros en ese directorio.
  - `*_guide.md` — detalles específicos del proyecto. Claude los actualiza cuando el proyecto cambia.

  El `CLAUDE.md` de la capa referencia su guía. Este `CLAUDE.md` raíz lista las capas que tienen sus propias reglas (§ 1.1).

  Un tercer hogar de documentación complementa este patrón: `docs/` — documentación narrativa y específica del proyecto dirigida al equipo de ingeniería y a los futuros mantenedores, no a Claude como fuente de reglas de arquitectura. Su estructura: un `CLAUDE.md` a nivel de directorio (reglas generales), y dos subdirectorios emparejados — `docs/features/` (descripción a nivel de comportamiento de cada feature) y `docs/limits/` (cifras exactas y la lista exhaustiva de "lo que NO soporta") — cada uno con su propio `CLAUDE.md` y un índice `README.md`. Los documentos se emparejan 1:1 por slug (`features/<slug>.md` ↔ `limits/<slug>.md`); nunca crees ni mantengas una mitad sin la otra. Un documento bajo `docs/` tiene la misma autoridad que un `*_guide.md` (§ 9).

  ### 6 Estilo y Calidad del Código

  - Python: PEP 8, convenciones de FastAPI, `from __future__ import annotations` en todos los módulos.
  - TypeScript: configuración de ESLint en `frontend/eslint.config.js`.
  - Idioma: el idioma de trabajo del repositorio es el español — tanto para la documentación (ficheros `.md`, incluidos todos los `CLAUDE.md` y todos los `*_guide.md`) como para el código (identificadores, comentarios, docstrings). Ninguna parte del repositorio está obligada a escribirse en otro idioma.
  - Comentarios solo donde aclaren lógica no evidente; evita el ruido o la redundancia.

  ### 7 Ficheros Inmutables

  Todo `CLAUDE.md` dentro de este repositorio — tanto el `CLAUDE.md` raíz como cada `CLAUDE.md` a nivel de capa (listados en § 1.1) — está protegido por un hook de pre-edición que impide cualquier modificación. La protección está acotada solo a este repositorio; los ficheros `CLAUDE.md` fuera del repo no se ven afectados. Claude nunca debe proponer ediciones directas de estos ficheros. En su lugar, describe el cambio sugerido — qué, dónde y por qué — para que el desarrollador pueda aplicarlo manualmente.
  Los cambios específicos del proyecto siempre van en el fichero `*_guide.md` correspondiente, que no está protegido.

  ### 8 Reglas de Ejecución de Planes

  Todo plan producido en modo plan para la implementación de una feature no trivial debe incluir estos dos pasos finales, en este orden:

  **Paso penúltimo — Actualización de tests:**
  Revisa los cambios de código introducidos por el plan y añade tests nuevos o actualiza los existentes para cubrir la funcionalidad nueva o modificada.

  **Paso final — Actualización de la documentación (crítico):**
  Este paso es la base de todo el sistema de aseguramiento de la calidad. La regla de Prioridad de la Documentación (§ 9) establece que los ficheros `.md` son siempre la fuente de verdad: cuando el código contradice a la documentación, la documentación es la correcta y el código debe cambiar. El subagente de revisión md-reviewer se apoya en este principio para detectar y corregir errores de código.

  Esto solo funciona si la documentación refleja con precisión el comportamiento pretendido después de cada ejecución de un plan. Si un `*_guide.md` queda desactualizado o parcialmente actualizado, se rompen dos cosas:
  1. Código nuevo legítimo puede ser marcado como "erróneo" porque no coincide con el `.md` obsoleto.
  2. Errores de código reales pueden pasar desapercibidos porque el `.md` nunca describió la nueva funcionalidad.

  Por tanto, esta actualización de la documentación no es una formalidad — es el paso que mantiene fiable el modelo de `.md`-como-fuente-de-verdad.
  Claude debe revisar y actualizar directamente — sin lanzar ningún agente o comando externo — los siguientes ficheros para que reflejen con precisión la funcionalidad nueva o modificada:
  - El `repository_guide.md` raíz.
  - El `README.md` raíz.
  - Cada `*_guide.md` en los directorios afectados por los cambios del plan.
  - Cada pareja `docs/features/<slug>.md` / `docs/limits/<slug>.md` afectada por los cambios del plan — actualiza ambos gemelos, y los dos índices `README.md` cuando se añade o se renombra una feature.

  ### 9 Prioridad de la Documentación

  Cuando las reglas o la información entran en conflicto, aplica la siguiente precedencia (de mayor a menor):
  **Este `CLAUDE.md` raíz** — reglas generales de arquitectura. Autoridad suprema.
  **Ficheros `CLAUDE.md` de capa** (p. ej. `backend/api/CLAUDE.md`, `docs/CLAUDE.md` y los ficheros `CLAUDE.md` de sus subdirectorios) — reglas estructurales de esa capa. Prevalecen sobre todo lo inferior.
  **Ficheros `*_guide.md` y documentos de `docs/`** (p. ej. `api_guide.md`, `repository_guide.md`, `docs/features/<slug>.md`, `docs/limits/<slug>.md`) — detalles específicos del proyecto que complementan a los ficheros `CLAUDE.md`. Nunca contradicen a los niveles superiores.
  **El propio código fuente** — la implementación real. Cuando el código contradice a la documentación en cualquier nivel superior, la documentación es la correcta y el código es lo que debe cambiar.

  Esta jerarquía aplica a todas las decisiones: manejo de errores, límites entre capas, convenciones de nomenclatura, imports permitidos y cualquier otra regla. Si una fuente de menor prioridad entra en conflicto con una de mayor prioridad, sigue siempre la de mayor prioridad y señala el conflicto.

  ### 10 Seguimiento de Errores Comunes

  @common_mistakes.md

  Los errores recurrentes se registran en el fichero importado arriba. Claude debe tratar cada entrada como una regla estricta con la misma autoridad que este `CLAUDE.md`.

  **Señalización proactiva:** Cuando Claude note que está repitiendo un error — o el usuario corrija el mismo tipo de error más de una vez a lo largo de varias conversaciones — Claude debe preguntar explícitamente al usuario si la
  corrección debe añadirse a `common_mistakes.md`. No añadas entradas de forma autónoma; pregunta siempre primero.

  ### 11 Ejecución de Tests de Integración y E2E

  Nunca ejecutes las suites de tests de integración o E2E por iniciativa propia. Ejecútalas solo cuando el usuario lo solicite explícitamente; en ese caso puedes decidir ejecutarlas.

  ### 12 Arranque de la Aplicación y Verificación en Navegador en Vivo

  Nunca arranques ninguna parte del stack de la aplicación — base de datos, backend o frontend (vía Podman/compose, uvicorn, el servidor de desarrollo de Vite, o cualquier otro medio) — y nunca verifiques un cambio que hayas hecho navegando la aplicación en ejecución en localhost con el MCP de Playwright (o cualquier otra herramienta de automatización de navegador) por iniciativa propia. Estas acciones solo están permitidas cuando el usuario las solicita literalmente en la conversación. En particular, nunca las incluyas como pasos finales de verificación en los planes, y nunca las realices voluntariamente como un paso de "comprobar que mi cambio funciona" — la verificación en vivo pertenece al usuario salvo que se delegue explícitamente.

  ### 13 Ficheros de Memoria — Sin Escrituras Autónomas

  Nunca edites, reestructures ni añadas entradas al almacén de memoria persistente — `MEMORY.md` y los ficheros de memoria individuales bajo el directorio `memory/` de la sesión — por iniciativa propia. Esto anula el comportamiento por defecto del sistema de memoria de guardar hechos de forma proactiva: escribe en memoria solo cuando el usuario te pida explícitamente recordar algo. La misma disciplina de "nunca por iniciativa propia" que la § 12 (verificación en vivo) y la regla de la § 10 de que las entradas de `common_mistakes.md` nunca se añaden de forma autónoma.

  ### 14 Documentación de APIs Externas (Gmail / Microsoft Graph)

  Antes de responder a una pregunta o hacer un cambio que involucre la API de Gmail o la API de Outlook / Microsoft Graph, consulta PRIMERO la carpeta de investigación local `external-apis-used/` (ignorada por git). Abre el índice de clasificación del proveedor correspondiente — `external-apis-used/Gmail/CLAUDE.md` o `external-apis-used/Outlook/CLAUDE.md` — que enruta al fichero `.md` exacto por tema, y lee ese fichero para encontrar lo que necesitas. Solo si la información necesaria no está ahí, o parece incompleta, **avisa primero al usuario y espera** antes de ir a internet a buscar la documentación oficial de la API (developers.google.com / learn.microsoft.com).

  ### 15 Cuentas Propietarias de las Consolas Externas

  Cuando necesites saber con qué cuenta se administra cada consola o registro externo de la aplicación (el App Registration de Azure para Outlook/login Microsoft, el proyecto de Google Cloud para OAuth de Gmail/login Google, la bóveda de Bitwarden, los perfiles dedicados de Chrome o las credenciales en disco), consulta el fichero local `cuentas-de-la-aplicacion.md` en la raíz del repo (ignorado por git). Ese fichero es la fuente de verdad de qué cuenta posee cada cosa; no adivines la cuenta ni la busques por prueba y error.
