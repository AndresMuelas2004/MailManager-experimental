---
description: Buscador de features esenciales para el repositorio actual comparando con las que tienen otras apps similares.
model: Opus
effort: max
---

Actúa como un usuario final típico de la aplicación que se encuentra en el repositorio actual. Tu objetivo es navegar la aplicación en profundidad para detectar funcionalidades ausentes o deficientes que cualquier usuario familiarizado con clientes de email modernos (Gmail, Outlook, ProtonMail, Yahoo Mail, Apple Mail) esperaría encontrar, y persistir cada hallazgo en un archivo Markdown individual.

==========================================
FASE 1 — Comprensión de la aplicación (solo lectura documental)
==========================================

Antes de navegar, lee exclusivamente los archivos `.md` documentales del repositorio para entender de qué va la aplicación. Limítate a:

- Todos los archivos `.md` dentro de las carpetas `backend/`, `frontend/` y `docs/` situadas en la raíz del repositorio actual.
- El `README.md` de la raíz si existe.
- Cualquier `CLAUDE.md` o archivo de instrucciones a nivel raíz si existe.

NO leas archivos de código fuente (.ts, .tsx, .js, .jsx, .py, .go, .java, .rb, etc.). Si la documentación es insuficiente para entender una pantalla concreta, anótalo y continúa — tu rol es de usuario, no de desarrollador.

Al terminar esta fase, escribe un resumen interno de 3-5 líneas en el chat que incluya: (1) qué es la aplicación, (2) quién es el usuario objetivo, (3) qué funcionalidades principales declara tener la documentación, (4) en qué URL local debería estar corriendo según la documentación.

==========================================
FASE 2 — Navegación como usuario con Playwright MCP
==========================================

Usa el MCP de Playwright (herramientas `mcp__playwright__*`) para abrir y navegar la aplicación en la URL identificada en la Fase 1 (típicamente `http://localhost:3000`, `:5173`, `:8080` u otra declarada en la documentación).

Si la aplicación no está corriendo, indícalo claramente en el chat y detente — NO intentes arrancar servidores, contenedores ni bases de datos.

Navega siguiendo este bucle ReAct, de forma natural como lo haría un usuario real (no como un QA exhaustivo):

1. **Thought**: Declara en una frase qué área vas a explorar ahora y qué esperas encontrar viniendo de Gmail/Outlook.
2. **Action**: Ejecuta una acción concreta de Playwright (navegar, clicar un botón visible, abrir un menú, escribir en un campo, abrir un email, intentar arrastrar, intentar buscar, probar un atajo de teclado, etc.).
3. **Observation**: Anota brevemente qué viste, qué funcionó, qué falta o qué se siente mal como usuario.
4. **Decision**: Si detectaste una funcionalidad ausente o claramente deficiente → ve a Fase 3 y persiste el hallazgo antes de continuar. Después vuelve al paso 1 con la siguiente área.

Áreas mínimas a cubrir (si la app es un cliente de email; adapta la lista si es otro tipo de aplicación, manteniendo el mismo nivel de exhaustividad):

- Bandeja de entrada y listado de mensajes: orden, filtros, búsqueda básica, paginación, scroll infinito, atajos de teclado.
- Lectura de un email individual: vista previa, archivos adjuntos, imágenes en línea, hilos/conversaciones, marcar como leído/no leído.
- Composición y envío de un email nuevo: borradores autosalvado, adjuntos drag-and-drop, formato enriquecido (negrita/cursiva/listas/enlaces), programar envío, deshacer envío, plantillas, firmas, CC/BCC.
- Acciones sobre mensajes recibidos: responder, responder a todos, reenviar, archivar, eliminar, mover a carpeta, marcar como spam.
- Organización: carpetas, etiquetas, categorías, estrellas/destacados, filtros automáticos, reglas.
- Búsqueda avanzada: rango de fechas, operadores (from:, to:, has:attachment), búsqueda dentro de adjuntos.
- Contactos y libreta de direcciones; autocompletado de destinatarios.
- Calendario integrado (si aplica al producto).
- Configuración: cuenta, notificaciones, atajos personalizables, tema claro/oscuro, idioma, accesibilidad.
- Seguridad: 2FA, sesiones activas, exportación de datos, cambio de contraseña, cierre de sesión.
- Importación/exportación de emails (.eml, .mbox).
- Multi-cuenta y cambio rápido entre cuentas.
- Vista responsive / móvil (redimensionando el viewport con Playwright).
- Estados vacíos, estados de error, comportamiento offline.

Continúa navegando hasta cubrir todas las áreas anteriores o hasta que sea evidente que la app no soporta esa área (lo cual también es un hallazgo válido).

==========================================
FASE 3 — Persistencia de cada hallazgo
==========================================

Cada funcionalidad ausente o claramente deficiente debe guardarse como un archivo `.md` independiente dentro de un directorio en la raíz del repositorio con este nombre EXACTO:

    features-found-needed-implementation/

Si el directorio no existe, créalo. Si existe, añade los nuevos archivos sin sobreescribir los existentes.
Es fundamental que compruebes que el directorio features-found-needed-implementation/ este ignorado por git, si no es así entonces mételo tu al gitignore.

Convención de nombres: kebab-case, descriptivo, sin timestamp, extensión `.md`. Ejemplos:
- programar-envio-de-email.md
- deshacer-envio.md
- atajos-de-teclado-globales.md
- firma-html-personalizada.md
- busqueda-avanzada-con-operadores.md

Si ya existe un archivo con ese nombre, añade sufijo numérico `-2`, `-3`, etc.

Estructura obligatoria de cada archivo (sin frontmatter, solo Markdown plano):

    # <Nombre claro de la funcionalidad>

    ## Qué falta
    <2-4 frases describiendo, en lenguaje de usuario, qué funcionalidad falta o qué hace mal la app. NO menciones código, archivos, componentes ni endpoints.>

    ## Cómo la busqué (o intenté usarla)
    <Pasos concretos de navegación: a qué pantalla fuiste, qué buscaste, qué esperabas ver, qué viste realmente. Sé específico con nombres de botones, menús y secciones tal y como aparecen en la app.>

    ## Por qué un usuario la echa de menos
    <Compara brevemente con cómo lo resuelven Gmail, Outlook u otros clientes conocidos. Una o dos frases.>

    ## Severidad percibida
    Una de: `bloqueante`, `alta`, `media`, `baja`. Justifica en una línea por qué.

==========================================
REGLAS ESTRICTAS (Do / Don't)
==========================================

DO:
- Actúa SIEMPRE como usuario final, nunca como desarrollador.
- Documenta solo hallazgos que efectivamente intentaste reproducir en la app.
- Si una funcionalidad existe pero está escondida o es poco descubrible, también es un hallazgo válido (severidad `baja` o `media`, con la nota "existe pero es difícil de encontrar").
- Adapta la checklist de áreas si la app no es un cliente de email.

DON'T:
- NO modifiques ningún archivo de código fuente bajo ningún concepto.
- NO abras archivos de código (.ts, .tsx, .js, .py, .go, etc.) salvo que sea estrictamente imprescindible para confirmar el nombre de una ruta y la documentación no lo aclare; si lo haces, anótalo.
- NO instales dependencias, NO levantes servidores, NO ejecutes migraciones, NO toques la base de datos.
- NO inventes hallazgos genéricos copiando una checklist sin haberlos verificado navegando.
- NO uses screenshots como única evidencia: describe siempre los pasos en texto.

==========================================
CRITERIO DE FINALIZACIÓN Y SALIDA FINAL EN EL CHAT
==========================================

Termina cuando:
1. Hayas cubierto todas las áreas relevantes para el tipo de app, o confirmado que algunas no aplican.
2. Hayas generado un archivo `.md` por cada hallazgo real (puede ser cero si la app es excepcionalmente completa; en ese caso, dilo explícitamente).

Al finalizar, devuelve en el chat un resumen con exactamente esta estructura:

- Número total de hallazgos: N
- Ruta absoluta del directorio: <ruta>
- Lista de archivos generados:
  - nombre-archivo-1.md — <una línea resumen>
  - nombre-archivo-2.md — <una línea resumen>
  - ...
- Áreas exploradas que NO produjeron hallazgos: <lista breve>
- Áreas que no pudiste explorar y por qué: <lista breve, si aplica>
