# Autocompletado de destinatarios — comportamiento (MVP)

Este documento describe **qué hace** el autocompletado de destinatarios cuando un usuario redacta un correo y **qué experimenta** delante de la app, sin entrar en cómo está cableado el código. Es una guía de comportamiento para que cualquier persona del equipo entienda cómo se va a comportar la funcionalidad cuando se siente delante de la pantalla.

Es una ayuda **puramente aditiva**: nunca bloquea el tecleo manual ni cambia el envío. Si no hay sugerencias o algo falla, el campo se comporta exactamente como hoy (escritura manual). No es una libreta de contactos editable — es **memoria automática** de las direcciones que ya han pasado por el buzón sincronizado del usuario.

Los topes concretos (mínimo de caracteres, debounce, número de sugerencias, tope de palabras) y la lista exhaustiva de "qué NO soporta" viven en un documento aparte: **[../limits/autocompletado-destinatarios.md](../limits/autocompletado-destinatarios.md)**. Aquí se mencionan de pasada y se explica el *porqué*; allí están las cifras exactas.

Este documento es vecino de [composicion-y-envio.md](composicion-y-envio.md) (que describe el composer y la validación de direcciones que sigue actuando como red de seguridad) y comparte filosofía de búsqueda con la [lupa.md](lupa.md) (literal, insensible a mayúsculas y tildes).

---

## 1. Qué es y de dónde salen las sugerencias

Al redactar un correo, cuando el usuario escribe en cualquiera de los tres campos de destinatario — **Para**, **CC** o **CCO** — la app le ofrece direcciones en un pequeño desplegable bajo el campo. En lugar de teclear (o recordar y pegar) la dirección entera, el usuario ve una lista corta de candidatos y elige uno.

Las sugerencias salen de **la gente con la que el usuario ya se ha comunicado**, calculadas sobre los correos que la app **ya tiene sincronizados** en su base de datos local:

- **Quienes le han escrito** — los remitentes de los correos **recibidos** (su dirección y su nombre).
- **A quienes ya ha enviado** — los destinatarios de los correos **enviados** (la dirección y el nombre del primer destinatario "Para" guardado de cada correo enviado).

No se consulta a Gmail ni a Outlook ni a su libreta de contactos oficial: es un filtro sobre lo ya sincronizado, igual que la [lupa.md](lupa.md). Por eso es instantáneo y funciona aunque el proveedor esté caído. El usuario **no puede** dar de alta, editar ni borrar contactos a mano.

Hoy estos campos no ofrecen ninguna ayuda: hay que escribir la dirección completa cada vez, lo que es lento para contactos habituales y propenso a erratas. Esta funcionalidad cubre justo eso sin tocar el resto del envío.

---

## 2. Dónde aparece

Aplica al composer en **todos sus modos**: correo nuevo, borrador nuevo, edición de borrador, respuesta, responder a todos y reenvío. Es la misma ventana de redacción en todos los casos, así que la ayuda es idéntica en todos ellos.

Los tres campos reciben exactamente el mismo trato:

- **Para** está siempre visible.
- **CC** y **CCO** aparecen al pulsar el enlace "Añadir CC/BCC" que ya existía; una vez desplegados, autocompletan igual que "Para".

Solo el campo que el usuario está editando en ese momento muestra su desplegable. Los otros dos no abren nada aunque compartan por debajo la misma lista de candidatos.

---

## 3. Cuándo aparece el desplegable

El desplegable **no aparece de inmediato ni con una sola letra**. Hacen falta tres condiciones a la vez, la misma idea que en la [lupa.md](lupa.md):

- Un **mínimo de caracteres** en lo que el usuario está tecleando (ver [../limits/autocompletado-destinatarios.md](../limits/autocompletado-destinatarios.md)).
- Una **breve pausa** al teclear (debounce) — para no consultar en cada pulsación. La cifra exacta vive en el documento de límites.
- Que el campo esté **enfocado** (el usuario está editando ese campo concreto).

Casos borde de aparición:

- Con el campo **vacío o con muy pocos caracteres no se muestra nada**. En particular, **no** se ofrece una lista de "contactos top" al enfocar un campo vacío — es una decisión de MVP: sin texto no hay nada con qué acotar.
- Si **no hay coincidencias**, el desplegable muestra un texto discreto de **"Sin sugerencias"** mientras el campo siga enfocado y con texto suficiente. El usuario teclea la dirección a mano igual que hoy.
- Mientras la consulta está en vuelo se muestra un breve **"Buscando…"** si todavía no hay nada que enseñar.
- En una **cuenta recién conectada y aún sin sincronizar** simplemente no hay sugerencias; comportamiento idéntico al actual.

### Qué se considera "lo que el usuario está tecleando"

Un campo de destinatario admite **varias direcciones separadas por comas**. La sugerencia se calcula solo sobre el **fragmento activo**: el texto que hay **después de la última coma** (recortado de espacios). Lo que ya está escrito antes de esa última coma se considera "ya confirmado" y no dispara sugerencias — solo sirve para excluir (sección 6). Así, en `ana@x.com, amp` la app busca por `amp`, no por toda la línea.

---

## 4. Qué se busca y cómo coincide

La coincidencia es la **misma filosofía de la [lupa.md](lupa.md)**: texto literal en cualquier parte (subcadena), **ignorando mayúsculas/minúsculas y tildes/acentos**. No corrige erratas, no entiende plurales ni sinónimos.

- El usuario puede teclear **parte del nombre** (`amp`) o **parte de la dirección** (`amparo@`): ambas formas encuentran a la misma persona, porque cada palabra escrita se compara a la vez contra el nombre **y** contra la dirección.
- `jose` encuentra `José`; `JUAN`, `juan` y `Juan` dan lo mismo. Esta normalización de tildes es imprescindible para usar la app con normalidad en español.
- Si el usuario escribe **más de una palabra**, cada palabra es un token independiente y **todas** deben aparecer (en el nombre o en la dirección), exactamente como en la lupa con varias palabras. Hay un tope de palabras como salvaguarda (ver [../limits/autocompletado-destinatarios.md](../limits/autocompletado-destinatarios.md)).

> La insensibilidad a tildes se apoya en la misma extensión de PostgreSQL (`unaccent`) que la lupa, con la misma trampa de despliegue asociada (una base de datos sin esa migración hace fallar la consulta). Se documenta en [../limits/autocompletado-destinatarios.md](../limits/autocompletado-destinatarios.md).

---

## 5. Qué muestra cada entrada

- Cuando se conoce el nombre de la persona, la entrada muestra **nombre y dirección** juntos (por ejemplo, "Amparo López" sobre "amparo@ejemplo.com").
- Si no hay nombre asociado, se muestra **solo la dirección**.
- Una misma dirección aparece **una sola vez**, aunque figure en cientos de correos. Si esa dirección ha llegado con varios nombres distintos a lo largo del tiempo, se muestra **el nombre más reciente que no estuviera vacío** (un correo más nuevo con el nombre en blanco no borra un nombre que sí teníamos de un correo anterior).

---

## 6. Qué se excluye de las sugerencias

Tres exclusiones, por motivos distintos:

- **Las propias direcciones del usuario** (las direcciones de email de sus cuentas conectadas) no se sugieren — no tiene sentido proponerle escribirse a sí mismo. Esto se decide en el backend comparando contra las direcciones de las cuentas del usuario.
- **Las direcciones que solo aparecen en Spam o Papelera** (ni en correos eliminados) no se sugieren — suelen ser remitentes no deseados. Matiz importante: la exclusión es **por correo**, no por dirección. Si una dirección aparece **además** en un correo normal (bandeja principal o enviados), **sí** se sugiere; solo se descarta si **únicamente** vive en Spam/Papelera.
- **Las direcciones ya añadidas en el campo activo** (las que ya están escritas y "confirmadas" antes de la última coma de ese mismo campo) no se vuelven a sugerir. Esta exclusión la hace el **frontend** sobre la lista que devuelve el backend: el servidor no sabe qué lleva ya escrito el usuario, así que devuelve todos los candidatos y la ventana filtra los que ya están puestos en esa línea. Por eso la misma dirección puede seguir apareciendo en **otro** campo (p. ej. está en "Para" pero el usuario la teclea en "CC").

---

## 7. Cobertura multi-cuenta

- Las sugerencias abarcan **todas las cuentas conectadas del usuario**, en un **único desplegable**, sin importar desde qué cuenta esté redactando. Si el usuario tiene tres cuentas, los contactos de las tres se mezclan y se ordenan juntos.
- Un usuario **nunca** ve direcciones procedentes de cuentas que no le pertenecen: los candidatos se calculan exclusivamente sobre las cuentas que son suyas, así que no hay forma de que se filtren contactos de otro usuario.

---

## 8. Orden de las sugerencias

- Las sugerencias se ordenan por **relevancia combinando frecuencia y recencia**: primero las direcciones con las que el usuario **más se ha comunicado** (más correos), y a igualdad de frecuencia, las **más recientes**. Es un orden distinto al de la lupa (que ordena solo por fecha): aquí lo que el usuario quiere arriba es a su gente habitual.
- Solo se muestra un **número reducido** de candidatos (la cifra exacta vive en [../limits/autocompletado-destinatarios.md](../limits/autocompletado-destinatarios.md)). Si hay más coincidencias que ese tope, las que sobran simplemente no aparecen; el usuario afina escribiendo más letras.

---

## 9. Cómo se elige e inserta

- El usuario elige una sugerencia con el **ratón** (clic) o con el **teclado**: flechas **arriba/abajo** para moverse por la lista (con vuelta circular: bajar desde el último lleva al primero), **Enter** para confirmar la resaltada, **Escape** para cerrar el desplegable sin tocar el campo. **Tab** también cierra el desplegable y deja seguir la navegación normal del formulario.
- Al elegir, **se inserta únicamente la dirección de email**, no "Nombre &lt;dirección&gt;". El nombre solo se usa para **ayudar a reconocer** a la persona en el desplegable; no se escribe en el campo.
- El campo sigue admitiendo **varias direcciones separadas por comas**, como hoy. La dirección elegida **reemplaza el fragmento que se estaba tecleando** y se añade una coma y un espacio detrás, de modo que el usuario puede seguir tecleando para añadir otra. Eso vuelve a disparar el ciclo de sugerencias para el nuevo fragmento. Lo ya escrito antes (otras direcciones) se conserva intacto.
- Tras elegir, el foco vuelve al campo para no interrumpir el tecleo.

---

## 10. Degradación silenciosa (red de seguridad)

Esta es la garantía central de la funcionalidad: **nada de esto puede romper el redactor**.

- Cualquier **error o lentitud** al obtener sugerencias **no rompe el composer, no bloquea el tecleo ni impide el envío**. Si las sugerencias fallan, la lista simplemente se queda vacía y el campo se comporta como hoy (escritura manual). El usuario no ve un mensaje de error de sugerencias: la ayuda es opcional y su ausencia es indistinguible de "no hay contactos que casen".
- La **validación de direcciones inválidas que ya existe se mantiene intacta** como red de seguridad: el botón de enviar se sigue deshabilitando si alguna dirección tecleada no tiene forma de email válida (esa validación vive en [composicion-y-envio.md](composicion-y-envio.md)). El autocompletado no la sustituye ni la relaja — solo ayuda a escribir menos.

---

## 11. Qué pasa "por debajo" mientras el usuario escribe (resumen rápido)

Para entender el flujo de un vistazo:

1. El usuario teclea en el campo → la app aísla el **fragmento activo** (lo que hay tras la última coma).
2. Si el fragmento supera el mínimo y el usuario deja de escribir durante el debounce → se hace **una única** consulta al backend de MailManager. Cada nueva pulsación reinicia el contador; solo la última cuenta.
3. El backend consulta la base de datos local (PostgreSQL), agrega los candidatos de todas las cuentas del usuario con todas las reglas anteriores, y devuelve la lista corta ordenada. No se llama a Gmail ni a Outlook.
4. El desplegable se repinta — descontando las direcciones ya puestas en esa línea.
5. El usuario elige una (ratón o teclado) o sigue tecleando; al borrar por debajo del mínimo, el desplegable se cierra y la lista vuelve a vaciarse de inmediato.

---

## Resumen en una frase

> Al escribir en Para/CC/CCO de cualquier modo del composer, la app sugiere —tras una breve pausa y un mínimo de caracteres, solo sobre el fragmento que hay tras la última coma— direcciones de la gente con la que el usuario ya se ha comunicado en **todas** sus cuentas (excluyendo sus propias direcciones, las que solo viven en Spam/Papelera y las ya puestas en esa misma línea), mostrando cada dirección una sola vez con su nombre más reciente y ordenadas por frecuencia y recencia; el usuario elige con ratón o teclado y se inserta **solo** la dirección, sin que nada de esto bloquee jamás el tecleo manual ni el envío. Las cifras exactas y todo lo que deliberadamente no soporta viven en [../limits/autocompletado-destinatarios.md](../limits/autocompletado-destinatarios.md).
