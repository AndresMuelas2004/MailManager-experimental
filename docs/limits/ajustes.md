# Panel de Ajustes — límites y alcance (MVP)

Este documento cataloga **hasta dónde llega** el área de Ajustes: los pocos topes con cifras exactas (longitud de nombres, versión de la app, persistencia del idioma) y la lista de "lo que deliberadamente NO hace", con un porqué breve de cada limitación.

El **comportamiento** (las seis secciones, la identidad, el renombrado/borrado de bandejas, el selector de idioma, el "sincronizar todo", el "acerca de") se describe en **[../features/ajustes.md](../features/ajustes.md)**. Aquí solo están las cifras y los límites.

> Nota: muchos de los "límites" de Ajustes **no son cuotas propias** sino de las funcionalidades que el panel integra. La duración de sesión y los permisos OAuth viven en [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md); el modelo de bandejas, en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md); los topes de la sincronización, en [sincronizacion.md](sincronizacion.md). Aquí solo se recogen las cifras **propias** del panel y se enlaza al resto.

---

## 1. Topes numéricos propios

| Límite | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Longitud mínima del nombre de bandeja al **renombrar** | **1 carácter** (tras recortar espacios) | Cliente (botón de confirmar deshabilitado) y servidor (`MailboxUpdate.display_name`, `min_length=1`) | Igual mínimo que al crear la bandeja. Un nombre vacío o solo-espacios se rechaza. |
| Longitud máxima del nombre de bandeja al **renombrar** | **120 caracteres** | Servidor (`MailboxUpdate.display_name`, `max_length=120`) y atributo del campo de texto (`maxLength=120`) | Igual máximo que al crear. El campo de la interfaz no deja teclear más de 120; pasarse en el servidor es un error de validación. |
| Longitud máxima de la **etiqueta de cuenta** al editarla | **120 caracteres** | Atributo del campo de texto de la edición en línea (`maxLength=120`) | El mínimo efectivo es 1: una etiqueta vacía o solo-espacios no se guarda (se descarta en el cliente antes de llamar al servidor). |
| **Versión** de la app mostrada en "Acerca de" | **`1.0.0`** | Constante fija del frontend (no hay endpoint de versión) | Es texto fijo; no se lee de ningún sitio ni se calcula. Cambiarla es editar la constante. |
| Clave de persistencia del idioma | **`lang`** en `localStorage`, valores `es` / `en` | Navegador del usuario | No hay columna ni endpoint para el idioma. Ver § 3. |

No hay TTLs, reintentos, concurrencia ni timeouts **propios** del panel. El renombrado/borrado de bandeja y la edición de etiqueta son operaciones de base de datos local de un solo paso; "Sincronizar todo" reutiliza la sincronización existente y hereda sus topes (ver [sincronizacion.md](sincronizacion.md)).

---

## 2. Endpoint nuevo: renombrar bandeja

El renombrado de bandeja estrena un endpoint `PATCH` sobre la bandeja (el único endpoint **nuevo** de backend de esta tanda; el borrado de bandeja y la edición de etiqueta ya existían). Reglas de alcance:

| Aspecto | Comportamiento |
|---|---|
| Propiedad | Se valida que la bandeja pertenezca al usuario de la sesión **antes** de tocarla. Una bandeja ajena (o inexistente) colapsa a **404**, nunca a 403 — misma política anti-fuga que el resto de la superficie de bandejas y bandejas ficticias. |
| Carrera (la fila desaparece entre la comprobación de propiedad y el `UPDATE`) | Surface **404** (bandeja no encontrada), **nunca** un 200 silencioso ni un 500 genérico. Misma disciplina que el `UPDATE`/`DELETE` de bandejas ficticias. |
| Validación de tamaño | `display_name` 1..120 (ver § 1). El esquema de petición del cliente **no** valida en runtime (solo se validan las respuestas); el límite lo imponen el campo de la interfaz y la validación del servidor. |

---

## 3. Idioma: lo que es exacto

| Aspecto | Valor exacto / regla |
|---|---|
| Idiomas disponibles | **Dos**: Español (`es`) y English (`en`). No hay más. |
| Persistencia | `localStorage`, clave `lang`. **Local del navegador** — no viaja entre dispositivos ni se guarda en el servidor. |
| Precedencia en el primer arranque | (1) valor guardado válido (`es`/`en`) si existe → gana siempre; (2) si no, **detección**: `en` solo si el idioma del navegador empieza por `en`; (3) cualquier otro caso, incluido el desconocido o `localStorage` no disponible → **`es`** (Español por defecto). |
| Texto que falta en un idioma | Se cae primero al diccionario **español** y, si tampoco está, a la **clave cruda** (nunca un hueco en blanco). |
| Alcance | **Solo la interfaz.** No se traduce el contenido de los correos ni lo que el usuario escribe. Ver § 4. |

---

## 4. Lo que NO soporta (y por qué)

### 4.1 Idioma

| No soporta | Porqué breve |
|---|---|
| **Traducir el contenido de los correos** | El idioma es solo de la interfaz; los correos se muestran en su idioma original. Traducirlos está fuera del MVP. |
| **Traducir lo que el usuario escribe al redactar** | Igual que arriba: el composer no traduce el texto del usuario. |
| **Traducir la cabecera de cita de Responder/Reenviar** | La línea «El día … escribió:» y la fecha en UTC son **contenido del correo**, no interfaz: **permanecen en español** independientemente del idioma elegido (es la limitación R-05 de [responder-y-reenviar.md](responder-y-reenviar.md), que **sigue vigente**). Lo que esta funcionalidad relaja es la limitación general de "interfaz español-fija", no R-05. |
| **Operadores de la lupa en el idioma de la interfaz** | Los operadores (`from:`, `to:`, `subject:`, …) son **solo en inglés** por decisión propia, igual que Gmail (ver [lupa.md](lupa.md)); no dependen del selector de idioma. |
| **Sincronizar la preferencia de idioma entre dispositivos** | Vive en `localStorage` del navegador. El mismo usuario en otro equipo vuelve a la detección inicial. Llevarla al servidor exigiría columna + endpoint, fuera del alcance del MVP. |
| **Más idiomas que Español e English** | Solo dos idiomas en el MVP. Añadir otro es añadir un diccionario nuevo. |

### 4.2 Acerca de

| No soporta | Porqué breve |
|---|---|
| **Enlaces a ayuda / privacidad / términos** | Ocultos a propósito: no existen páginas reales todavía. Se añadirán cuando existan y sus URLs se puedan verificar. |
| **Versión dinámica (leída del backend o del build)** | Es una **constante fija** del frontend (`1.0.0`). No hay endpoint de versión ni se deriva del tag de git. |

### 4.3 Eliminar cuenta de usuario

| No soporta | Porqué breve |
|---|---|
| **Borrado con un solo clic** | Es total e irreversible, así que exige **teclear el propio email exactamente** para habilitar el botón. La fricción es deliberada. |
| **Recuperación tras el borrado** | El borrado cascada (bandejas → cuentas → correos) es definitivo. No hay papelera ni deshacer. |

### 4.4 Bandejas

| No soporta | Porqué breve |
|---|---|
| **Renombrar/eliminar bandejas de otro usuario** | La propiedad se valida en cada operación; una bandeja ajena colapsa a 404 (ver § 2). |
| **Deshacer el borrado de una bandeja** | El borrado arrastra en cascada sus cuentas y correos sincronizados; es definitivo. La confirmación previa (que enumera lo que se borra) es la única salvaguarda. |
| **Compartir, reordenar o mover cuentas entre bandejas** | Siguen sin existir: esta tanda solo añade renombrar y eliminar. El resto de ausencias del modelo de buzón están en [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md). |

### 4.5 Estructura del panel

| No soporta | Porqué breve |
|---|---|
| **Preferencias de producto configurables** | Fuera del MVP: firma de correo, tema claro/oscuro, notificaciones de correo nuevo, agrupar por conversación configurable, nº de correos por página, vista de inicio configurable, marcar leído automático configurable y liberar espacio / limpiar caché manual. El panel hoy cubre identidad, cuentas, bandejas, idioma, sincronizar y "acerca de", y nada más. |

---

## 5. Dónde viven los límites de funcionalidades vecinas

Para no duplicar cifras, estos topes —que el usuario *experimenta* dentro de Ajustes pero que **no pertenecen** al panel— están en sus propios catálogos:

- **Duración de sesión, permisos OAuth, validaciones de cuenta y rate limiting** → [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md).
- **Modelo de bandeja, mínimos/máximos de nombre al crear, ausencias del modelo de buzón** → [buzones-y-vista-unificada.md](buzones-y-vista-unificada.md).
- **Topes de la sincronización** (cap de bootstrap, umbral de eventos, lote, reintentos) que "Sincronizar todo" hereda → [sincronizacion.md](sincronizacion.md).
- **Cabecera de cita en español y fecha en UTC (R-05)** → [responder-y-reenviar.md](responder-y-reenviar.md).

---

## 6. Resumen

> El panel de Ajustes tiene muy pocas cifras propias: el nombre de bandeja al renombrar es 1..120 caracteres (igual que al crear), la etiqueta de cuenta tope 120, la versión es la constante fija `1.0.0`, y el idioma se guarda en `localStorage` bajo la clave `lang` con dos valores (`es`/`en`), por defecto Español. El único endpoint nuevo de backend es el `PATCH` para renombrar bandeja (propiedad validada → 404 ajeno/inexistente, 404 en carrera, nunca 200 silencioso). El resto son ausencias deliberadas: el idioma es solo de la interfaz (el contenido de los correos, la cabecera de cita R-05 y los operadores de la lupa siguen como están), no viaja entre dispositivos; "Acerca de" no enlaza a ayuda/privacidad/términos aún; eliminar la cuenta exige teclear el email; y no hay preferencias de producto configurables (firma, tema, notificaciones, etc.). El comportamiento completo está en [../features/ajustes.md](../features/ajustes.md).
