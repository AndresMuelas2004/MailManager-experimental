# Páginas públicas (landing, privacidad y términos) — comportamiento

Este documento describe **las tres páginas de missela.app que se pueden ver sin iniciar sesión**: la landing de marketing en la raíz del sitio, la política de privacidad y los términos de servicio. Cubre quién ve qué según tenga o no sesión, en qué idioma sale cada página y cómo se cambia, y qué contienen. Las rutas exactas, la regla precisa de detección de idioma y la lista de lo que deliberadamente NO hacen viven en su gemelo: **[../limits/paginas-publicas.md](../limits/paginas-publicas.md)**.

Estas páginas existen por un requisito externo concreto: la verificación OAuth de Google exige una página de inicio que describa la funcionalidad real de la aplicación y una política de privacidad enlazada de forma visible y accesible sin cuenta. Los términos de servicio no son obligatorios para Google, pero el consent screen tiene un campo para ellos y completan el conjunto.

---

## 1. La raíz del sitio: landing para anónimos, aplicación para autenticados

Abrir la raíz de missela.app se comporta distinto según la sesión:

- **Visitante sin sesión:** ve la **landing de marketing** — una página estática de presentación del producto. No se le redirige a la pantalla de login; puede leer la landing entera y decidir.
- **Usuario con sesión:** no ve marketing. Se le reenvía automáticamente a la puerta de entrada de la aplicación (el gateway de bandejas, que a su vez lo lleva a su bandeja o al alta de la primera bandeja), exactamente como antes de existir la landing. El reenvío es instantáneo e invisible: entrar en missela.app con sesión sigue significando "entrar en mi correo".
- **Mientras se resuelve la sesión** (la comprobación inicial contra el servidor), se muestra el mismo spinner de carga que usa el resto de la aplicación.

Las demás superficies no cambian: la pantalla de login sigue en su ruta de siempre, y el resto de la aplicación sigue exigiendo sesión.

## 2. Qué contiene la landing

De arriba abajo:

1. **Barra de navegación fija** (se mantiene visible al hacer scroll): logotipo MISSELA (vuelve a la propia landing), enlaces a la sección de características y a las dos páginas legales, el selector de idioma EN/ES y el botón de iniciar sesión.
2. **Hero**: titular ("All your email. One inbox." / "Todo tu correo. Una sola bandeja."), subtítulo, botón principal de empezar y la nota de que es gratis durante la beta.
3. **Seis tarjetas de características**: bandeja unificada, Gmail y Outlook juntos, búsqueda con operadores, cliente de correo completo, bandejas virtuales y privacidad por diseño.
4. **Cómo funciona**: tres pasos (iniciar sesión, conectar buzones, listo).
5. **Bloque "Private by design"**: el bloque destacado que explica los permisos OAuth exactos que se piden y el uso que se hace del correo — la pieza pensada para los revisores de Google.
6. **Llamada final a la acción** y **pie** con el copyright, los enlaces a las páginas legales y el correo de soporte.

Los botones de "Empezar" / "Get started" y el de "Iniciar sesión" llevan todos a la pantalla de login. En pantallas estrechas la rejilla de tarjetas se apila y los enlaces de navegación del centro se ocultan (quedan logo, selector de idioma y botón de entrar); el detalle está en el gemelo.

## 3. Las páginas legales: privacidad y términos

- Son **públicas**: se pueden abrir con o sin sesión, tecleando la URL o desde los enlaces de la landing (nav, bloque de privacidad y pie). Nunca redirigen al login.
- Muestran el **texto legal aprobado** — la política de privacidad (con la declaración de Limited Use de Google) y los términos de servicio — con su fecha de "última actualización" fija. El texto es contenido aprobado tal cual: cualquier cambio pasa por un nuevo borrador revisado, no se reescribe sobre la marcha.
- Los **enlaces externos** del texto (la política de datos de usuario de Google, las páginas de revocación de permisos de Google y Microsoft, la web de la AEPD) se abren en una pestaña nueva. Los términos enlazan internamente a la política de privacidad, y el correo de soporte es un enlace de correo.
- Comparten con la landing la misma barra superior (sin el enlace de características, que solo tiene sentido dentro de la landing) y el mismo pie.

## 4. Idioma: inglés por defecto salvo navegador español

Las tres páginas públicas son bilingües (inglés / español) y deciden su idioma así:

1. **Si el visitante ya eligió idioma** (el selector de estas páginas o el ajuste de idioma de la aplicación — es la misma preferencia), esa elección manda.
2. **Si no hay elección guardada**, decide el idioma del navegador: un navegador en español ve las páginas en español; **cualquier otro navegador las ve en inglés**. Esto es deliberado y más agresivo que el resto de la aplicación (que por defecto cae a español): los revisores de Google navegan en en-US y deben encontrarse la landing y la política en inglés.
3. El **selector EN/ES** de la esquina cambia el idioma al momento y **guarda la elección** — la misma que usa la aplicación, así que iniciar sesión después conserva el idioma elegido en la landing. La detección automática, en cambio, nunca guarda nada: un idioma que el visitante no eligió no debe convertirse en su preferencia.

Consecuencia aceptada de no tocar el defecto de la aplicación: un navegador que no sea ni español ni inglés (francés, alemán…) ve la landing en inglés pero la pantalla de login en español, hasta la primera elección explícita. El detalle de la regla y dónde se guarda la preferencia están en el gemelo y en [ajustes.md](ajustes.md).

## 5. Qué NO cambia con esta entrega

- **Ningún contrato con el servidor**: no hay endpoints nuevos ni cambios en los existentes; las tres páginas son estáticas y solo la comprobación de sesión de la raíz habla con el backend (la misma llamada de siempre).
- **El flujo de login y la aplicación autenticada**: idénticos; solo cambia que la puerta de entrada tras el login pasa por la redirección de la raíz.
- **El idioma de la aplicación**: su detección por defecto no se ha tocado.

La lista exhaustiva de lo que las páginas públicas deliberadamente no hacen (SEO en servidor, sitemap, títulos por página, idioma en la URL…) está en [../limits/paginas-publicas.md](../limits/paginas-publicas.md).

---

## Resumen en una frase

> La raíz de missela.app muestra a los visitantes sin sesión una landing de marketing bilingüe (nav fija, hero, seis características, tres pasos, bloque de privacidad para los revisores de Google, CTA y pie) y reenvía a los usuarios con sesión a su correo como siempre, mientras que la política de privacidad y los términos de servicio viven en rutas públicas accesibles sin cuenta; las tres páginas salen en español solo para navegadores en español y en inglés para todos los demás — salvo elección explícita en el selector EN/ES, que guarda la misma preferencia de idioma que usa la aplicación — y las cifras, rutas y omisiones deliberadas viven en [../limits/paginas-publicas.md](../limits/paginas-publicas.md).
