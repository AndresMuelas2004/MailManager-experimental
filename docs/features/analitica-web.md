# Analítica web y consentimiento

Este documento describe la medición de audiencia de missela.app: qué se mide, quién decide que se mida, y por qué el alcance es deliberadamente diminuto. Las cifras exactas (identificadores, cookies, caducidades, lista de rutas) viven en el gemelo [`../limits/analitica-web.md`](../limits/analitica-web.md).

## 1. Qué problema resuelve

Antes de esto no había forma de saber cuánta gente llegaba a la página pública ni desde dónde. La analítica responde a una única pregunta de producto —*¿cuánta gente nos visita y por qué camino llega?*— y se detiene ahí. No es un sistema de telemetría de producto: no mide qué funciones usa la gente dentro del cliente de correo, ni construye embudos, ni identifica usuarios.

## 2. El alcance: solo las páginas públicas

Solo se miden las páginas que un visitante anónimo puede ver: la portada, la política de privacidad, los términos y la pantalla de inicio de sesión. Todo lo que hay detrás del login —el buzón, los ajustes, las bandejas ficticias, las carpetas— es territorio no medido.

Esto no es una omisión que se pueda "ampliar luego sin más": es lo que la [política de privacidad](paginas-publicas.md) promete literalmente al usuario, y lo que permite afirmar que ninguna ruta de buzón llega a Google. La lista de rutas medidas es una constante cerrada en el código, y una ruta que no esté en ella no se envía aunque el visitante haya aceptado.

Hay una consecuencia técnica que es fácil romper sin darse cuenta: el propio script de Google sabe detectar los cambios de página de una aplicación de una sola página (SPA) y reportarlos él solo. Si esa detección automática estuviera activa, en cuanto el visitante aceptara el aviso y entrase a su correo, Google recibiría las rutas del buzón sin que nuestro código enviara nada. Por eso está desactivada en dos sitios a la vez —en la configuración de la propiedad de Google Analytics y en la llamada de configuración que hace el frontend— y **cada** página que se reporta es una llamada explícita nuestra.

## 3. Nada se carga sin permiso

La primera vez que alguien visita una página pública ve un aviso en la parte inferior con dos botones: aceptar o rechazar.

- **Mientras no responde**: no se descarga ningún script de Google, no se abre ninguna conexión a sus servidores y no se instala ninguna cookie de analítica. El aviso no bloquea la página; se puede leer y navegar el sitio con él en pantalla.
- **Si acepta**: se inyecta el script, se configura la propiedad y se reporta la página en la que está. A partir de ahí, cada navegación a otra página pública reporta una visita más.
- **Si rechaza**: no ocurre nada, ni en esa visita ni en las siguientes.

La elección se guarda en el almacenamiento local del navegador (no en una cookie, precisamente porque guardar la negativa en una cookie sería incoherente con lo que se está preguntando). El aviso no vuelve a aparecer salvo que se borren los datos del sitio, que es también la forma de cambiar de opinión.

El aviso **solo se ofrece en las páginas públicas**. Un usuario que ya ha iniciado sesión y está trabajando en su correo nunca lo ve interrumpir su trabajo, porque dentro de la aplicación no hay nada que consentir: ahí no se mide nada en ningún caso.

## 4. Se puede desplegar sin analítica

El identificador de la propiedad de Google es una variable de entorno que se hornea en el build. Si se despliega vacía —el caso por defecto, y el de cualquier entorno de desarrollo— la feature desaparece por completo: sin aviso, sin script y sin eventos. No hay que tocar código para apagarla, y no existe ningún camino en el que el aviso aparezca sin haber una propiedad configurada detrás.

## 5. Qué ve Google y qué no

Ve: la ruta pública visitada, el título de la página, y lo que su propio script deduce del navegador (país aproximado, idioma, dispositivo, página de procedencia).

No ve: ninguna dirección de correo, ningún identificador de usuario de Missela, ninguna ruta que contenga un identificador de buzón, cuenta o carpeta, y absolutamente ningún dato procedente de los correos. La aplicación nunca le envía identidad: no se usa el identificador de usuario de Analytics ni ningún parámetro propio.

## 6. Interacción con la política de seguridad de contenidos

La cabecera de seguridad del sitio (CSP) tiene que permitir de antemano los dominios de Google, porque una política de seguridad se decide en el servidor y no puede depender de lo que el visitante elija después en el navegador. Que el permiso esté concedido en la cabecera no significa que se use: sin consentimiento no se hace ni una sola petición a esos dominios.

## 7. Casos límite

- **Navegación en modo privado o con el almacenamiento bloqueado**: la elección no se puede guardar, así que el aviso reaparece en cada carga. La decisión sigue respetándose durante la visita en curso.
- **Un usuario autenticado que abre la portada**: la portada le reenvía a su correo. Si aún no había respondido al aviso, puede verlo un instante antes del reenvío; en cuanto la ruta deja de ser pública, el aviso desaparece solo.
- **Bloqueadores de anuncios**: si el navegador bloquea el script tras haber aceptado, la aplicación no se rompe — el reporte de páginas simplemente no ocurre.

## Resumen en una frase

> Missela mide con Google Analytics únicamente sus cuatro páginas públicas, y solo después de que el visitante lo acepte explícitamente en un aviso; sin ese "sí" no se descarga ni una línea de código de Google, y dentro de la aplicación no se mide absolutamente nada.
