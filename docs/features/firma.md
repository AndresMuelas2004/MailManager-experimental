# Firma de correo — comportamiento (MVP)

Este documento describe **qué hace** la firma de correo de MailManager y **qué experimenta** el usuario: dónde se configura, cómo se edita, cómo se inserta sola al redactar y cómo se comporta al cambiar de cuenta o al responder/reenviar. No entra en cómo está cableado el código: es una guía de comportamiento para el equipo y futuros mantenedores.

La firma es **una por cuenta conectada** (cada Gmail/Outlook tiene la suya, independiente) y vive **solo en MailManager**: no se importa ni se escribe la firma del proveedor, y no se pide ningún permiso nuevo. Se escribe en **el mismo editor de texto enriquecido que el cuerpo de un correo** —su mecánica canónica está en [composicion-y-envio.md](composicion-y-envio.md) § 3— y al redactar se inserta **dentro del cuerpo**, editable como cualquier otro texto.

El tope de tamaño, las cifras exactas y la lista de "lo que deliberadamente NO hace" viven en su gemelo: **[../limits/firma.md](../limits/firma.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están los números.

---

## 1. El problema que resuelve

Hasta ahora no se podía configurar una firma: para firmar (nombre, cargo, teléfono, web…) había que **teclear los mismos datos a mano en cada mensaje**. Con varias cuentas conectadas el problema se agrava, porque cada cuenta suele tener su propia firma profesional. La firma por cuenta resuelve esto: cualquier correo saliente sale firmado solo, con la firma de la cuenta remitente, y editable antes de enviar.

---

## 2. Dónde se configura

La firma se gestiona dentro del **área de Ajustes** (el engranaje de la barra lateral), en una **sección propia llamada "Firma"**, junto a "Cuentas conectadas", "Bandejas", "Idioma", etc. El panel de Ajustes y sus secciones están descritos en [ajustes.md](ajustes.md); aquí solo se cubre lo propio de la firma.

La sección "Firma":

- Lista **cada cuenta conectada de la bandeja activa**, una debajo de otra, con su nombre (etiqueta de la cuenta) y, si se conoce, su dirección de correo.
- Para cada cuenta muestra un **editor con barra de formato** (el mismo del redactor), un botón **"Guardar"** y mensajes claros de estado: **guardando**, **firma guardada** y **error** si el guardado falla.
- Si la bandeja no tiene cuentas conectadas, la sección lo indica en lugar de mostrar editores vacíos.

> **Encuadre multi-cuenta / multi-bandeja.** Como todo el área de Ajustes, la sección "Firma" está enmarcada en la **bandeja** (`mailbox`) que el usuario tenga abierta y muestra las cuentas **de esa bandeja**. Un usuario con varias bandejas configura las firmas de las cuentas de cada bandeja desde los Ajustes de esa bandeja. Esto es coherente con el resto del panel (ver [ajustes.md](ajustes.md)).

### 2.1 Guardar, editar y borrar la firma

- **Guardar**: el usuario escribe la firma con formato y pulsa "Guardar". Mientras se guarda, el botón muestra un estado de guardado; al terminar bien, aparece "Firma guardada". Al editar de nuevo, esa confirmación desaparece.
- **Editar**: vuelve a abrir la sección, cambia el contenido del editor y guarda otra vez. Lo guardado sustituye por completo a la firma anterior de esa cuenta.
- **Borrar**: para quitar la firma de una cuenta, el usuario **vacía el editor y guarda**. No hay un botón "borrar firma" aparte: una firma vacía equivale a "esta cuenta no tiene firma". A partir de ahí, los correos nuevos de esa cuenta arrancan con el cuerpo vacío, como antes de configurar nada.

> **La firma se guarda ya saneada.** Lo que se persiste pasa por el **mismo saneador de salida estricto** que cualquier cuerpo redactado (la allowlist de etiquetas/atributos vive en [../limits/composicion-y-envio.md](../limits/composicion-y-envio.md) y se cataloga para la firma en [../limits/firma.md](../limits/firma.md)). Por eso, tras guardar, el editor se vuelve a sembrar con el valor ya limpio que devuelve el servidor: lo que el usuario ve guardado es exactamente lo que se insertará al redactar.

---

## 3. Qué formato admite la firma

La firma se escribe en el **editor de texto enriquecido del redactor** (su descripción canónica está en [composicion-y-envio.md](composicion-y-envio.md) § 3), así que admite exactamente lo mismo que el cuerpo de un correo: **negrita, cursiva, subrayado, listas (con viñetas y numeradas) y enlaces** (p. ej. la web de la empresa o un `mailto:`).

Por la misma razón, **no admite imágenes/logos, ni colores, ni tipografías personalizadas, ni tablas**: es una limitación deliberada del saneador de salida (el mismo que limita el redactor), no un olvido. Una firma con logo queda fuera del alcance de esta versión. El catálogo exacto de lo que sobrevive y lo que se limpia está en [../limits/firma.md](../limits/firma.md).

---

## 4. Cómo se inserta al redactar

El comportamiento clave: la firma de la cuenta remitente **aparece ya escrita en el cuerpo** al abrir el redactor, lista y **editable** antes de enviar (modelo WYSIWYG, igual que Gmail/Outlook). El usuario puede modificarla o borrarla **en ese mensaje concreto** sin que eso afecte a la firma guardada en Ajustes —lo de Ajustes es la **plantilla por defecto**, lo del mensaje es una copia editable—.

El criterio de inserción es simple y **no hay interruptor global "usar/no usar firma"**: si la cuenta tiene firma **no vacía**, se inserta; si está vacía, no se inserta nada (el cuerpo arranca como siempre).

### 4.1 Dónde queda la firma según el modo de redacción

- **Correo nuevo / borrador nuevo**: el cuerpo arranca con **una línea vacía arriba** (donde el usuario escribe) y **la firma debajo**. Si la cuenta no tiene firma, el cuerpo arranca vacío como hasta ahora.
- **Responder / Responder a todos / Reenviar**: el cuerpo arranca con una línea vacía arriba, **la firma debajo**, y **debajo la cita** del mensaje original. Es decir, el usuario escribe arriba, la firma queda **entre su texto y la cita**. La mecánica de la cita (atribución + recuadro) está en [responder-y-reenviar.md](responder-y-reenviar.md) § 4; lo que esta funcionalidad añade es la firma intercalada antes de esa cita.
- **Editar un borrador ya existente**: **no** se vuelve a insertar la firma. El borrador ya tiene el cuerpo que tuviera (incluida la firma que llevara al crearse), y reinsertarla cada vez que se reabre lo duplicaría. Por eso reabrir un borrador lo muestra **tal cual se guardó** (ver [borradores.md](borradores.md) § 3).

> **Ejemplo (correo nuevo).** La cuenta "Trabajo" tiene firma *"Jane Doe — Acme"*. El usuario pulsa "Redactar": el cuerpo aparece con una línea en blanco arriba y la firma debajo. Escribe su mensaje en la línea de arriba y envía; el destinatario recibe el mensaje seguido de la firma.

> **Ejemplo (respuesta).** El usuario responde a un correo desde la cuenta "Trabajo". El cuerpo arranca con: línea en blanco (donde teclea) → *"Jane Doe — Acme"* → la línea de atribución *"El … escribió:"* → el original citado. Puede borrar la firma en esa respuesta concreta sin tocar la guardada.

### 4.2 Cambiar de cuenta en el redactor

En un **correo nuevo / borrador nuevo** y **antes de haber tocado el cuerpo**, si el usuario cambia la **cuenta remitente**, la firma se **reemplaza** por la de la nueva cuenta (incluido el caso de cambiar a una cuenta sin firma, que deja el cuerpo vacío). Esto es lo esperado: aún no ha escrito nada, así que la firma que se ve debe ser la de la cuenta desde la que va a enviar.

En cambio, **si el usuario ya ha empezado a escribir** (el cuerpo difiere de lo que se sembró al abrir), cambiar de cuenta **no toca el cuerpo**: lo que escribió se respeta y la firma no se reemplaza. Lo mismo ocurre en cualquier modo donde el selector de cuenta esté **bloqueado** (un borrador ya existente, o un "Nuevo mensaje" que ya creó su borrador silencioso al adjuntar — ver [composicion-y-envio.md](composicion-y-envio.md) § 6): ahí no se puede cambiar de cuenta y la cuestión de reemplazar la firma no se plantea.

> **Por qué este matiz importa.** Reemplazar la firma solo cuando el cuerpo está "sin tocar" evita el peor caso: borrar el texto que el usuario ya había escrito al cambiar de remitente. La regla "si no has escrito nada, la firma sigue a la cuenta; en cuanto escribes, se respeta tu contenido" es la que hace que el cambio de cuenta sea seguro.

### 4.3 La firma auto-insertada no cuenta como "trabajo sin guardar"

Como la firma se siembra sola al abrir el redactor, una firma **que el usuario no ha tocado** se considera contenido "limpio", no trabajo a medias. Consecuencia visible: si el usuario abre un correo nuevo (que arranca con la firma) y lo **cierra sin escribir nada**, el composer se cierra directamente, **sin** el diálogo de "¿Guardar / Descartar?". Solo si el usuario modifica el cuerpo (escribe, edita o borra la firma sembrada) el cierre con cambios dispara ese diálogo, igual que cualquier otro contenido (ver [composicion-y-envio.md](composicion-y-envio.md) § 5).

---

## 5. La firma viaja dentro del cuerpo

La firma **no es un campo aparte** ni una cabecera: es texto del **cuerpo** del correo. Al enviar, ese cuerpo (firma incluida) se sanea con el mismo filtro de salida que cualquier mensaje redactado, así que el destinatario la recibe como parte natural del mensaje. En **Gmail**, además, se genera automáticamente la variante en **texto plano** del correo —firma incluida— para los clientes que no muestran HTML (el detalle del transporte por proveedor está en [composicion-y-envio.md](composicion-y-envio.md) § 4).

Como la firma viaja en el cuerpo y se inserta como copia editable, **lo que se envía es lo que el usuario ve** en el redactor en ese momento: si la ajustó o la borró en ese mensaje, se envía ajustada o sin firma, sin que cambie la plantilla guardada en Ajustes.

---

## 6. Qué experimenta el usuario, de principio a fin

1. Entra en **Ajustes → Firma** del buzón activo y ve sus cuentas conectadas, cada una con su editor.
2. Escribe la firma de una cuenta con formato (negrita, enlaces, listas…) y pulsa "Guardar"; ve "Firma guardada".
3. Pulsa "Redactar": el correo nuevo arranca con una línea en blanco arriba y su firma debajo, ya editable.
4. Si cambia de cuenta remitente antes de escribir, la firma se reemplaza por la de la nueva cuenta; si ya escribió, su texto se respeta.
5. Al **Responder/Reenviar**, la firma queda entre lo que escribe y la cita del original.
6. Puede ajustar o borrar la firma en ese mensaje concreto; lo guardado en Ajustes no cambia.
7. Para quitar la firma de una cuenta, vacía su editor en Ajustes y guarda.

---

## 7. Resumen en una frase

> Cada cuenta conectada puede tener **su propia firma con formato** (negrita, cursiva, subrayado, listas y enlaces —nada de imágenes, colores ni tablas, por el saneador de salida estricto—), configurada en **Ajustes → Firma** de la bandeja activa (vaciar y guardar la borra), que se **inserta sola y editable dentro del cuerpo** al redactar: línea en blanco arriba + firma debajo en un correo nuevo, y entre el texto del usuario y la cita al responder/reenviar; cambiar de cuenta antes de escribir reemplaza la firma, una vez escrito se respeta el contenido, editar un borrador existente no la reinserta, y la firma auto-insertada sin tocar no dispara el diálogo de cierre; viaja **dentro del cuerpo** (saneado como cualquier mensaje, con variante de texto plano en Gmail) y vive **solo en MailManager** sin sincronizar con el proveedor ni pedir permisos nuevos. Las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/firma.md](../limits/firma.md).
