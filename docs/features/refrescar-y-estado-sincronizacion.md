# Refrescar manualmente y ver el estado de sincronización — comportamiento (MVP)

Este documento describe **qué experimenta el usuario** con el control de actualización que aparece en la cabecera de las bandejas: el botón "Refrescar" que comprueba a demanda si ha llegado correo nuevo, y el texto "Última actualización: hace X" que cuenta cómo de reciente es lo que ve. Es una funcionalidad **nueva a nivel de usuario**, pero por debajo no introduce ninguna sincronización distinta: reutiliza exactamente el mismo proceso que ya ocurre solo al abrir una bandeja. Por eso este documento se limita a la **capa de control y visibilidad**; el motor de sincronización (bootstrap vs. incremental, qué baja, asimetrías Gmail/Outlook) vive en **[sincronizacion.md](sincronizacion.md)**.

Las cifras exactas (cada cuánto avanza solo el texto, los tramos "segundos / minutos / horas / días", dónde se guarda la marca y con qué clave) y la lista de "lo que NO hace" viven en su gemelo: **[../limits/refrescar-y-estado-sincronizacion.md](../limits/refrescar-y-estado-sincronizacion.md)**. Aquí solo se mencionan de pasada.

---

## 1. Qué problema resuelve

Antes de esta funcionalidad, la sincronización con Gmail/Outlook era **invisible**. La app ya sincronizaba sola al abrir cada listado (ver [sincronizacion.md](sincronizacion.md) § 2.1), pero no había ningún botón ni texto que lo contara, así que el usuario no tenía forma de:

1. **Forzar** una comprobación de correo nuevo ("busca ahora si ha llegado algo"), salvo recargando la página entera.
2. **Saber** si lo que ve está al día o es de hace rato.

El gesto "tirar para refrescar y ver si ya ha llegado" —constante en Gmail/Outlook— no existía. Esta funcionalidad lo añade, y de paso da la tranquilidad de un "última actualización: hace unos segundos" visible.

---

## 2. Qué ve el usuario

En la cabecera de las bandejas aparece un **control de actualización** con dos partes apiladas:

- **Botón "Refrescar"** (icono circular de recarga). Al pulsarlo, la app comprueba en ese momento si hay correo nuevo en las cuentas de esa vista y repinta el listado. Mientras trabaja, el icono **gira**, el botón queda **deshabilitado** y su texto pasa a **"Sincronizando…"**, de modo que no se puede pulsar dos veces. Ese mismo icono giratorio sirve además de indicador de actividad para la sincronización automática de apertura, no solo para el clic manual.
- **Texto "Última actualización: hace X"** justo debajo (p. ej. "hace unos segundos", "hace 5 minutos", "hace 2 horas"). El texto se **pone al día solo** con el paso del tiempo —sin lanzar ninguna sincronización— y se recalcula a la marca correcta cada vez que termina una sincronización, sea la manual o la automática de apertura. Antes de la primera sincronización del ámbito muestra **"Sin sincronizar todavía"**.

El texto relativo respeta el **idioma de la interfaz**: en inglés se lee "Last updated: 5 minutes ago", "Not synced yet", etc. Los tramos exactos de redondeo ("segundos" hasta un minuto, "minutos" hasta una hora, y así) están en [../limits/refrescar-y-estado-sincronizacion.md](../limits/refrescar-y-estado-sincronizacion.md).

> **Ejemplo.** El usuario entra a su bandeja unificada. Como abrir la vista ya dispara una sincronización, un par de segundos después lee "Última actualización: hace unos segundos". Deja la pestaña abierta cinco minutos sin tocar nada: el texto, por su cuenta, ha pasado a "hace 5 minutos" sin que se haya sincronizado nada. Pulsa "Refrescar": el icono gira, llega un correo nuevo, y el texto vuelve a "hace unos segundos".

Hay además un **tercer aviso** posible en este mismo control, de naturaleza distinta a los dos de arriba: mientras una cuenta recién conectada realiza su **descarga masiva inicial** en segundo plano (ver [sincronizacion.md](sincronizacion.md) § 3.5), bajo el botón aparece en **azul** un texto informativo *«Cargando tu histórico… N correos»* con el contador en vivo. No es un estado de error ni reemplaza a la "última actualización": es solo el progreso de esa carga, y desaparece al terminar. Es un canal **independiente** de los avisos de fallo (§ 5.1) — una cuenta en plena descarga queda fuera de la sincronización de apertura, así que nunca se cuenta como cuenta "a reconectar"; ambos avisos pueden incluso convivir si afectan a cuentas distintas.

---

## 3. Dónde aparece y dónde no

El control se añade a las **tres** vistas de listado de correo que ya se sincronizan al abrirse pero no ofrecían control manual:

- **Bandeja unificada** (y sus variantes Enviados / Spam / Papelera, que comparten cabecera). Refrescar sincroniza **todas las cuentas** del buzón.
- **Bandeja de una cuenta concreta** (vista por cuenta). Refrescar sincroniza **solo esa cuenta**.
- **Bandeja ficticia** (vista de una bandeja virtual; ver [bandejas-ficticias.md](bandejas-ficticias.md)). Refrescar sincroniza **las cuentas que la bandeja agrega**. Aquí antes ya existía un indicador pasivo "Sincronizando…" sin botón; ahora ese hueco lo ocupa el control completo (botón + "última actualización").

**Dónde NO cambia nada:**

- **Favoritos** (global y por cuenta) **no** recibe este control de refresco. Además, **ya no lleva ningún botón de sincronización**: los favoritos se capturan solos en la sincronización general y el antiguo botón **"Sincronizar favoritos"** se **retiró** de la página (ver [favoritos.md](favoritos.md)).
- **Ajustes → Datos** mantiene su botón **"Sincronizar todo"**, que recorre **todas** las bandejas del usuario (ver [ajustes.md](ajustes.md) § 5). El nuevo botón es complementario y de alcance opuesto: refresca **solo la vista visible**, no el buzón entero.

---

## 4. Cómo se comporta

### 4.1 Refresco manual, a demanda y acotado a la vista

Pulsar el botón sincroniza **únicamente la vista actual**, heredando su ámbito igual que la sincronización automática de apertura: la unificada todas las cuentas del buzón, la vista por cuenta solo esa cuenta, la ficticia las cuentas que agrega. No es un "sincronizar todo" encubierto.

Conviene distinguir dos cosas que el botón **no** confunde: "refrescar" aquí significa **ir al proveedor a buscar novedades** (una sincronización real), no simplemente re-leer la copia local que ya estaba en pantalla. Es el mismo trabajo que la app hacía sola al abrir la vista, ahora también a petición.

### 4.2 Sin auto-refresco periódico

No se introduce ningún temporizador que sincronice cada N minutos. Se mantiene exactamente el modelo de [sincronizacion.md](sincronizacion.md) § 2.4: la sincronización sigue siendo **reactiva** (al abrir una vista o al pulsar el botón), nunca en segundo plano. Lo único que avanza solo es el **texto** "hace X" —un refresco puramente visual del contador— y eso **no** lanza ninguna sincronización ni gasta cuota del proveedor.

### 4.3 Estado "al día" de un vistazo

Como la apertura de una vista ya sincroniza, el usuario verá típicamente "Última actualización: hace unos segundos" poco después de entrar —justo la tranquilidad que la funcionalidad busca dar—. El botón está ahí para los momentos en que quiere comprobar otra vez sin salir y volver a entrar.

---

## 5. Qué pasa si la sincronización falla

Si la sincronización **no logra traer nada** —el proveedor no responde, está caído o se alcanza un límite de peticiones (rate limiting, ver [autenticacion-y-cuentas.md](autenticacion-y-cuentas.md))—:

- El correo ya descargado **se sigue mostrando**: la lista **no se vacía**. El aviso de fallo es deliberadamente **no bloqueante** y vive en el control, separado del listado, para que un tropiezo transitorio del proveedor nunca borre lo que el usuario ya tenía delante.
- Bajo el botón aparece un aviso breve **"No se pudo actualizar"** en rojo, en lugar del texto de "última actualización".
- La marca de "última actualización" **no avanza**: sigue mostrando (cuando el aviso desaparece) la última sincronización correcta. Una sincronización fallida nunca se cuenta como reciente.
- El usuario puede **reintentar** con el botón.

Esto describe el **fallo total**: no se pudo sincronizar **ninguna** cuenta de la vista. Cuando la vista agrega varias cuentas y **solo algunas** fallan mientras el resto va bien, el comportamiento es distinto y más benévolo — lo cubre § 5.1.

### 5.1 El aviso según la vista: rojo bloqueante, ámbar informativo o silencio

Qué aviso ve el usuario ante un fallo depende de la vista y, sobre todo, de **cuántas** de sus cuentas fallaron. Nace de cómo sincroniza cada vista por debajo:

- **Vista de una sola cuenta.** Agrega una única cuenta, así que un fallo es siempre **total**: se muestra el aviso rojo "No se pudo actualizar" (§ 5) y la marca no avanza. Es lo correcto — no hay ninguna otra cuenta cuyo correo salvar.
- **Bandeja unificada, fallo total** (fallan **todas** sus cuentas). Igual que arriba: aviso rojo, marca congelada, listado intacto.
- **Bandeja unificada, fallo parcial** (van bien **algunas** cuentas y cae **otra**, típicamente por token caducado o revocado). Aquí el comportamiento cambia por completo: el listado **se actualiza igual** con el correo de las cuentas sanas, la marca de "última actualización" **avanza**, y **no** aparece el aviso rojo. En su lugar surge, bajo el botón, un aviso **sutil y no bloqueante** —en **ámbar**, no en rojo— que **nombra la(s) cuenta(s)** que no se pudieron sincronizar, por su dirección de correo, e invita a reconectarla ("No se pudo sincronizar: `otra@outlook.com` — reconéctala"). Los dos avisos son **mutuamente excluyentes**: el rojo del fallo total tiene prioridad y nunca conviven. Antes de este cambio, esa cuenta caída bloqueaba **toda** la bandeja unificada con el aviso rojo y sin repintar; ahora una cuenta rota deja de dejar sin actualización al resto (el porqué, en [sincronizacion.md](sincronizacion.md) § 7).
- **Bandeja ficticia** (ver [bandejas-ficticias.md](bandejas-ficticias.md)). Su refresco es un **abanico** que sincroniza cada cuenta agregada por separado y **tolera fallos parciales** desde siempre (misma filosofía que la unificada, [sincronizacion.md](sincronizacion.md) § 7). No muestra el rojo y **sí** avanza la marca en cuanto el intento termina, aunque alguna cuenta haya fallado por debajo. La diferencia con la unificada es que la ficticia lo hace **en silencio**: no nombra la cuenta caída ni muestra el aviso ámbar; simplemente agrega lo que sí se pudo traer. Es una simplificación aceptada: el valor de la vista ficticia es mostrar lo agregado, no frenarse por la cuenta más débil.

> **Ejemplo (fallo parcial en la unificada).** Buzón unificado con `amuelas30@gmail.com` (sana) y `otra@outlook.com` (token caducado). Al abrir la bandeja unificada, el listado se actualiza con lo nuevo de `amuelas30`, la marca pasa a "hace unos segundos" y debajo aparece el aviso sutil "No se pudo sincronizar: otra@outlook.com — reconéctala". Nada de pantalla de error; el correo de la cuenta sana está delante.

---

## 6. Dónde se recuerda la "última actualización"

La marca de "última actualización" se guarda **en el propio navegador**, no en el servidor. Esto tiene consecuencias que el usuario nota:

- Es **por dispositivo / navegador**: si sincroniza desde otro equipo, este no lo refleja. No es un dato de la cuenta que viaje entre dispositivos.
- Si se **borran los datos del navegador**, la marca desaparece y la vista vuelve a "Sin sincronizar todavía" hasta la siguiente sincronización.
- Es **por ámbito de vista**, no una marca global única. Cada buzón, cada cuenta y cada bandeja ficticia recuerda su propia "última actualización" de forma independiente.

### 6.1 La marca es independiente de la caja

Dentro de la bandeja unificada, cambiar entre **Recibidos / Enviados / Archivados / Spam / Papelera** muestra la **misma** "última actualización". No es un descuido: una sola sincronización trae el correo de **todas** esas cajas a la vez (la sincronización es por cuenta y abarca todas las bandejas — ver [sincronizacion.md](sincronizacion.md) § 3), así que tiene sentido que todas las cajas compartan la misma marca de frescura. Lo mismo aplica a la búsqueda, la paginación o el filtro de favoritos: ninguno de esos cambia el ámbito de la marca, que depende solo del buzón (y, en su caso, de la cuenta) o de la bandeja ficticia.

---

## 7. Lo que esta funcionalidad NO cambia del sistema

- **No añade permisos nuevos** a Gmail/Outlook ni amplía qué se sincroniza: es el mismo proceso de siempre, ahora con botón e indicador.
- **No cambia el motor de sincronización**: no toca bootstrap/incremental, ni los topes, ni la reconciliación de fantasmas, ni nada de [sincronizacion.md](sincronizacion.md). Solo añade un disparador manual y la visibilidad de cuándo fue la última vez.
- **No es un "sincronizar todo"**: refresca exclusivamente la vista visible. El "sincronizar todo" del buzón entero sigue siendo cosa de Ajustes → Datos ([ajustes.md](ajustes.md) § 5).

---

## Resumen en una frase

> En la cabecera de la bandeja unificada, la vista por cuenta y la bandeja ficticia aparece un control con un botón **"Refrescar"** —que dispara a demanda la **misma** sincronización con el proveedor que ya ocurría sola al abrir la vista, acotada a esa vista, girando y deshabilitándose mientras trabaja— y un texto **"Última actualización: hace X"** que avanza solo visualmente (sin sincronizar) y se guarda **en el navegador**, por dispositivo y por ámbito de vista; un fallo **total** deja la lista intacta, no avanza la marca y muestra "No se pudo actualizar", mientras que un fallo **parcial** de la unificada (una cuenta cae y las demás van bien) actualiza igual, avanza la marca y muestra un aviso **ámbar** sutil que nombra la cuenta a reconectar —sin rojo— (la bandeja ficticia también tolera fallos parciales, pero en silencio), sin auto-refresco periódico ni permisos nuevos. Las cifras exactas están en [../limits/refrescar-y-estado-sincronizacion.md](../limits/refrescar-y-estado-sincronizacion.md).
