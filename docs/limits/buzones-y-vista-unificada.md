# Buzones reales y vista unificada — límites y alcance (MVP)

Este documento cataloga **hasta dónde llega** la funcionalidad de buzones y vista unificada: los topes con cifras exactas y la lista de "lo que deliberadamente NO hace", con un porqué breve de cada limitación.

El **comportamiento** (qué es un buzón, cómo se crea, vista unificada vs. cuenta concreta, lógica de columnas "Para"/"De") se describe en **[../features/buzones-y-vista-unificada.md](../features/buzones-y-vista-unificada.md)**. Aquí solo están las cifras y los límites.

> Nota importante: esta funcionalidad tiene **muy pocos topes propios**. Es, esencialmente, un modelo de datos sencillo (un contenedor con un nombre) más una capa de presentación (qué columnas mostrar). La mayoría de los "límites" reales son **ausencias deliberadas de funcionalidad**, no cuotas numéricas. Las cuotas de las funcionalidades que se montan *sobre* el buzón (paginación del listado, debounce de la lupa, caps de adjuntos, caps de bandejas ficticias) viven en los documentos de esas funcionalidades, no aquí.

---

## 1. Topes numéricos

| Límite | Valor exacto | Dónde se aplica | Notas |
|---|---|---|---|
| Longitud mínima del nombre del buzón | **1 carácter** (tras recortar espacios) | Validación de cliente (botón deshabilitado) y de servidor; **al crear y al renombrar** | Un nombre vacío o solo-espacios se rechaza. |
| Longitud máxima del nombre del buzón | **120 caracteres** | Validación de servidor, columna de base de datos y atributo del campo de texto (los tres alineados en 120); **al crear y al renombrar** | Pasarse de 120 produce un error de validación en el servidor; el campo de la interfaz no deja teclear más de 120. |
| Cuentas conectadas por buzón | **Sin límite** | — | No hay ningún tope codificado. Un buzón puede agrupar tantas cuentas como el usuario conecte. Ver § 2. |
| Buzones por usuario | **Sin límite** | — | No hay ningún tope codificado. El usuario puede crear tantos buzones como quiera. Ver § 2. |

No hay TTLs, reintentos, concurrencia ni timeouts propios de esta funcionalidad: crear, listar, consultar, **renombrar** y borrar un buzón son operaciones de base de datos local de un solo paso, sin llamadas a proveedores externos.

> **Renombrar y eliminar buzones ya se exponen en la interfaz** (sección "Bandejas" de Ajustes y desplegable del selector). El renombrado usa un endpoint `PATCH` propio con las mismas validaciones de tamaño de la fila de arriba; el borrado reutiliza la cascada que ya existía. El flujo y a dónde va el usuario tras borrar la bandeja activa están en [../features/ajustes.md](../features/ajustes.md); la política de propiedad/carrera del `PATCH` y las cifras, en [ajustes.md](ajustes.md). Lo que sigue **sin** existir es compartir y mover cuentas entre buzones (§ 3.1).

---

## 2. Ausencia deliberada de cuotas de cantidad

Ni el número de **cuentas por buzón** ni el número de **buzones por usuario** tienen tope alguno en el MVP. Esto es una decisión consciente, no un olvido:

- **No es el cuello de botella del MVP.** Un usuario real maneja un puñado de cuentas y un puñado de buzones. Poner un límite artificial solo añadiría un caso de error que nadie alcanzaría.
- **El coste real está en otra parte.** Lo que consume cuota de proveedor y memoria es **sincronizar y listar correos**, no el número de contenedores. Esos costes se acotan en las funcionalidades de listado y sincronización, no en el modelo de buzón.

Consecuencia a tener en cuenta: una vista unificada con **muchas** cuentas conectadas hará tantas consultas de listado/sincronización como cuentas haya. El modelo de buzón no impone un freno; si en el futuro hiciera falta, el sitio para ponerlo sería el listado, no la creación de buzones.

---

## 3. Lo que NO soporta (y por qué)

### 3.1 Gestión del buzón

| No soporta | Porqué breve |
|---|---|
| **Compartir un buzón entre usuarios** | Un buzón pertenece a un único usuario y la propiedad se valida en cada acción. No hay modelo de permisos, invitaciones ni acceso multiusuario. Fuera del alcance del MVP. |
| **Mover una cuenta de un buzón a otro** | Una cuenta pertenece a exactamente un buzón desde que se conecta. No hay reasignación. Para "mover" una cuenta habría que desconectarla y volver a conectarla en el otro buzón. |
| **Reordenar o marcar un buzón como favorito/por defecto** | Los buzones se listan siempre por fecha de creación. No hay orden personalizado ni concepto de "buzón principal" más allá de que el onboarding entra al primero de la lista. |

### 3.2 Vista unificada y columnas

| No soporta | Porqué breve |
|---|---|
| **Una "súper-vista" de todos los buzones a la vez** | La vista unificada agrupa solo las cuentas de **un** buzón. Para combinar cuentas de buzones distintos existe otra funcionalidad: las **bandejas ficticias** ([../features/bandejas-ficticias.md](../features/bandejas-ficticias.md)). La unificada y la ficticia son cosas distintas a propósito. |
| **Mostrar más de un destinatario en la columna "Para"** | La columna "Para" muestra **únicamente el primer destinatario** del mensaje original, no la lista completa. La tabla tiene una sola columna "Para" y la semántica dominante ("a quién fue esto") se sirve bien con el destinatario principal. CC/BCC no se sincronizan y no se muestran. Es una simplificación de visualización, no una pérdida de datos: promover a lista completa sería una migración futura no destructiva. |
| **Elegir manualmente qué columnas ver** | El conjunto de columnas ("Para"/"De"/ambas) lo decide automáticamente el modo de la vista (cuenta concreta / unificado / mixto) y si la bandeja es de enviados. No hay configuración de columnas por parte del usuario. |
| **Ordenación por relevancia o personalizada en el listado** | El orden de los correos lo gobierna el listado (por fecha), no esta funcionalidad. Ver el documento de listado de correos. |

### 3.3 Modo "mixto"

| No soporta | Porqué breve |
|---|---|
| **Uso del modo mixto fuera de Favoritos** | El modo mixto (resolver "Para"/"De" fila a fila según si cada correo es enviado o recibido) existe hoy **solo** para la pantalla de Favoritos, que es el único listado que mezcla recibidos y enviados. Cualquier otro listado usa el modo unificado o el de cuenta concreta. |

---

## 4. Dónde viven los límites de funcionalidades vecinas

Para evitar duplicar cifras, estos topes —que el usuario *experimenta* dentro de un buzón pero que **no pertenecen** al modelo de buzón— están documentados en sus propios catálogos:

- **Paginación, orden y tope de resultados del listado de correos** → documento de listado de correos.
- **Debounce, mínimo de caracteres y tope de palabras de la lupa** → [../limits/lupa.md](../limits/lupa.md).
- **Caps de adjuntos** (tamaño por fichero, total, número, TTL del cache) → [../limits/adjuntos.md](../limits/adjuntos.md).
- **Caps y criterios de las bandejas ficticias** → [../limits/bandejas-ficticias.md](../limits/bandejas-ficticias.md).
- **Tope de borradores por cuenta** → documento de adjuntos/borradores.

---

## 5. Resumen

> El modelo de buzón tiene un único tope numérico real —el nombre, de 1 a 120 caracteres, tanto al crear como al renombrar— y **ninguna cuota** de cantidad (cuentas por buzón y buzones por usuario son ilimitados a propósito). Renombrar y eliminar un buzón **ya se exponen** en la interfaz (renombrar vía un `PATCH` propio; eliminar reutiliza la cascada existente — flujo en [ajustes.md](ajustes.md)). El resto de "límites" siguen siendo ausencias deliberadas de funcionalidad: no se comparte, no se mueven cuentas entre buzones, no existe una vista de todos los buzones a la vez (eso son las bandejas ficticias) y la columna "Para" muestra solo el primer destinatario. El comportamiento completo está en [../features/buzones-y-vista-unificada.md](../features/buzones-y-vista-unificada.md).
