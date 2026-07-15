import ExternalLink from './ExternalLink';

// Copy legal aprobado (UtilidadesDelProgramador/despliegue-app/verificacion-oauth/
// BORRADOR-privacy-policy.md, versión en español). Cualquier cambio de contenido
// pasa por un nuevo borrador aprobado — no reescribir sobre la marcha.
export default function PrivacyContentEs() {
  return (
    <>
      <h1>Política de privacidad</h1>
      <p>
        <strong>Última actualización: 15 de julio de 2026</strong>
      </p>
      <p>
        Missela ("nosotros") es un cliente de correo que te permite leer y gestionar tus cuentas de
        Gmail y Outlook desde una única bandeja de entrada. Lo opera Andrés Muelas Tenorio,
        desarrollador de software individual con base en España. Puedes escribirnos a{' '}
        <a href="mailto:support@missela.app">support@missela.app</a>.
      </p>
      <p>
        Esta política explica, en lenguaje llano, a qué datos accede Missela cuando conectas tus
        cuentas de correo, qué almacenamos en nuestros servidores, para qué lo usamos y cómo puedes
        borrarlo. Aplica a la aplicación web de missela.app.
      </p>
      <p>
        La versión corta: accedemos a tus buzones solo para mostrártelos y que puedas actuar sobre
        ellos. No vendemos datos, no mostramos publicidad, no entrenamos modelos de IA con tu correo
        y ningún humano lee tus mensajes.
      </p>

      <h2>1. A qué datos accedemos y por qué</h2>
      <p>
        <strong>Tu cuenta de Missela.</strong> Al iniciar sesión con Google o Microsoft recibimos tu
        perfil básico del token de identidad: un identificador de usuario, tu dirección de correo,
        tu nombre y tu foto de perfil. Lo usamos para crear tu cuenta de Missela y mostrar quién ha
        iniciado sesión. Nunca vemos tu contraseña.
      </p>
      <p>
        <strong>Buzones conectados.</strong> Al conectar una cuenta de Gmail, Missela solicita estos
        scopes de OAuth:
      </p>
      <ul>
        <li>
          <code>gmail.modify</code> - leer, redactar, enviar y gestionar etiquetas de tus mensajes.
          Es el scope mínimo de Gmail que soporta un cliente de correo completo (leer, marcar
          leído/no leído, favoritos, mover a spam/papelera/archivo, gestionar borradores).
          Deliberadamente NO permite borrar mensajes de forma permanente.
        </li>
        <li>
          <code>openid</code>, <code>email</code>, <code>profile</code> - para identificar la cuenta
          conectada.
        </li>
      </ul>
      <p>
        Al conectar una cuenta de Outlook, Missela solicita los permisos delegados de Microsoft
        Graph <code>Mail.ReadWrite</code>, <code>Mail.Send</code>, <code>offline_access</code> y los
        scopes básicos de perfil, que cubren la misma funcionalidad.
      </p>
      <p>
        Puedes revocar este acceso cuando quieras desde la configuración de seguridad de tu
        proveedor (Google:{' '}
        <ExternalLink href="https://myaccount.google.com/permissions">
          myaccount.google.com/permissions
        </ExternalLink>{' '}
        · Microsoft:{' '}
        <ExternalLink href="https://account.live.com/consent/Manage">
          account.live.com/consent/Manage
        </ExternalLink>
        ), y también desconectando la cuenta dentro de Missela.
      </p>

      <h2>2. Qué almacenamos en nuestros servidores</h2>
      <p>
        Missela mantiene una copia de trabajo de partes de tu buzón para que la aplicación sea
        rápida y se pueda buscar. En concreto:
      </p>
      <ul>
        <li>
          <strong>Tokens OAuth</strong> de cada cuenta conectada, cifrados en reposo. Son las
          credenciales que permiten a Missela hablar con Gmail/Outlook en tu nombre.
        </li>
        <li>
          <strong>Metadatos de mensajes</strong>: asunto, remitente, destinatario principal, fecha,
          estado leído/favorito, carpeta e identificador de conversación. Con esto se construyen tu
          lista de correo y la búsqueda.
        </li>
        <li>
          <strong>Contenido de mensajes</strong>: el cuerpo HTML saneado de los mensajes que abres
          (más una pequeña precarga de los recientes no leídos). Las copias en caché expiran a los 7
          días sin abrirse.
        </li>
        <li>
          <strong>Adjuntos</strong>: los ficheros que descargas o previsualizas se cachean hasta 30
          días desde el último acceso. El nombre y tamaño se conservan como metadatos.
        </li>
        <li>
          <strong>Borradores</strong> que redactas en Missela, con sus adjuntos, sincronizados con
          tu proveedor.
        </li>
        <li>
          <strong>Tu configuración</strong>: lista de cuentas conectadas, firma por cuenta, bandejas
          virtuales (vistas guardadas) y nombres de tus bandejas.
        </li>
        <li>
          <strong>Imágenes remotas</strong> referenciadas dentro de los correos: se cargan a través
          de nuestro proxy de imágenes y se cachean por URL. Es una medida de privacidad: el
          servidor del remitente ve a nuestro servidor, no tu dirección IP, navegador ni ubicación.
          Esta caché guarda solo los ficheros de imagen, indexados por URL, y no está vinculada a tu
          buzón.
        </li>
      </ul>
      <p>
        Algunas preferencias (idioma de la interfaz, marcas de última sincronización) viven solo en
        el almacenamiento local de tu navegador y nunca llegan a nuestros servidores.
      </p>
      <p>
        La copia autoritativa de tu correo permanece siempre en tu proveedor. Missela es una ventana
        a tu cuenta de Gmail/Outlook, no un archivo sustitutivo: borrar datos de Missela nunca borra
        nada de tu buzón real.
      </p>

      <h2>3. Para qué usamos tus datos</h2>
      <p>
        Para una sola cosa: darte las funciones que ves en la interfaz. Leer y buscar tu correo,
        enviar y responder, gestionar borradores y adjuntos, favoritos, spam/papelera/archivo,
        firmas, bandeja unificada y bandejas virtuales.
      </p>
      <p>No hacemos nada de esto:</p>
      <ul>
        <li>vender, alquilar o ceder tus datos a terceros;</li>
        <li>usar tu correo para publicidad de ningún tipo;</li>
        <li>usar tu correo para entrenar modelos de IA o aprendizaje automático;</li>
        <li>crear perfiles sobre ti o rastrearte por la web.</li>
      </ul>
      <p>En missela.app no hay analítica de terceros ni scripts de rastreo.</p>

      <h2>4. Datos de usuario de Google - declaración de Limited Use</h2>
      <p>
        El uso y la transferencia que Missela hace de la información recibida de las APIs de Google
        se ajustará a la{' '}
        <ExternalLink href="https://developers.google.com/terms/api-services-user-data-policy">
          Política de datos de usuario de los servicios API de Google
        </ExternalLink>
        , incluidos los requisitos de Limited Use (uso limitado).
      </p>
      <p>
        En particular: los datos de usuario de Google se usan únicamente para las funciones visibles
        descritas arriba, nunca se venden, nunca se usan para publicidad y nunca se usan para
        entrenar modelos generalizados de IA o aprendizaje automático. El acceso humano queda
        limitado a los casos de la sección 5.
      </p>

      <h2>5. Quién puede ver tus datos</h2>
      <p>
        Nadie, en la operación normal. Tus mensajes los procesa automáticamente nuestro software. Un
        humano (el operador) solo puede acceder a datos concretos cuando:
      </p>
      <ul>
        <li>
          tú lo pides expresamente y das tu consentimiento, por ejemplo para investigar un problema
          de soporte;
        </li>
        <li>es necesario por motivos de seguridad, como investigar un abuso;</li>
        <li>lo exige la ley aplicable;</li>
        <li>los datos están agregados y anonimizados para estadísticas internas de operación.</li>
      </ul>
      <p>
        No compartimos datos con terceros, con dos excepciones acotadas: nuestro proveedor de
        hosting, Hetzner Online GmbH (servidores en Núremberg, Alemania, UE), que los procesa por
        nuestra cuenta como infraestructura, y tus propios proveedores de correo (Google,
        Microsoft), que reciben las llamadas API necesarias para operar sobre tu buzón. También
        revelaríamos datos si la ley nos obligara.
      </p>

      <h2>6. Seguridad</h2>
      <ul>
        <li>
          Todo el tráfico entre tu navegador, nuestros servidores y tus proveedores va cifrado con
          TLS (HTTPS en todo; el dominio .app lo fuerza).
        </li>
        <li>
          Los tokens OAuth se cifran en reposo con cifrado simétrico (Fernet); la clave nunca sale
          del servidor.
        </li>
        <li>
          El servicio corre en contenedores endurecidos, con cortafuegos de red, limitación de
          peticiones y configuración de mínimo privilegio, alojado en la UE.
        </li>
        <li>
          El contenido cacheado está aislado por cuenta y se elimina automáticamente según las
          reglas de expiración de arriba.
        </li>
      </ul>
      <p>
        Ningún sistema es perfectamente seguro, pero la superficie es deliberadamente pequeña: un
        servidor, ningún tercero procesando datos más allá del hosting, y cachés de vida corta.
      </p>

      <h2>7. Retención y borrado de datos</h2>
      <ul>
        <li>
          Los cuerpos de mensaje cacheados expiran a los 7 días de la última apertura. Los ficheros
          de adjuntos cacheados se conservan hasta 30 días desde el último acceso.
        </li>
        <li>
          Todo lo demás ligado a una cuenta conectada (metadatos, borradores, tokens, firma) se
          conserva mientras la cuenta siga conectada.
        </li>
        <li>
          <strong>Desconectar una cuenta</strong> dentro de Missela borra inmediatamente todos sus
          datos almacenados: tokens, metadatos, contenido cacheado, adjuntos y borradores.
        </li>
        <li>
          <strong>Borrar tu cuenta de Missela</strong> (Ajustes → Cuenta) borra inmediatamente tu
          registro de usuario y todo lo que cuelga de él, para todas las cuentas conectadas.
        </li>
        <li>
          También puedes revocar el acceso de Missela desde Google/Microsoft en cualquier momento
          (enlaces en la sección 1); con el acceso revocado, Missela ya no puede leer nada de tu
          buzón.
        </li>
      </ul>

      <h2>8. Tus derechos (RGPD)</h2>
      <p>
        Si estás en el Espacio Económico Europeo, el responsable del tratamiento es Andrés Muelas
        Tenorio (contacto: <a href="mailto:support@missela.app">support@missela.app</a>). El
        tratamiento se basa en: la ejecución del servicio que solicitas (art. 6.1.b RGPD) y tu
        consentimiento expresado en la autorización OAuth (art. 6.1.a), que puedes retirar en
        cualquier momento como se describe arriba.
      </p>
      <p>
        Tienes derecho a acceder, rectificar, suprimir, limitar u oponerte al tratamiento de tus
        datos personales, y a la portabilidad. La mayoría puedes ejercerlos directamente en la
        aplicación (ver tu correo es acceso; desconectar/borrar es supresión). Para cualquier otra
        cosa, escribe a <a href="mailto:support@missela.app">support@missela.app</a>. También tienes
        derecho a reclamar ante tu autoridad de control; en España, la Agencia Española de
        Protección de Datos (<ExternalLink href="https://www.aepd.es">www.aepd.es</ExternalLink>).
      </p>

      <h2>9. Cookies</h2>
      <p>
        Missela usa una única cookie de sesión propia, estrictamente necesaria para mantener tu
        sesión iniciada (httpOnly, Secure). Sin cookies publicitarias, sin cookies de terceros, sin
        rastreo entre sitios. Al usarse solo cookies esenciales, no se requiere banner de
        consentimiento de cookies.
      </p>

      <h2>10. Menores</h2>
      <p>Missela no está dirigida a menores de 16 años y no tratamos sus datos a sabiendas.</p>

      <h2>11. Cambios en esta política</h2>
      <p>
        Publicaremos cualquier cambio en esta página y actualizaremos la fecha de arriba. Si un
        cambio afecta de forma sustancial al tratamiento de tus datos, lo anunciaremos dentro de la
        aplicación antes de que entre en vigor.
      </p>

      <h2>12. Contacto</h2>
      <p>
        <a href="mailto:support@missela.app">support@missela.app</a>
      </p>
    </>
  );
}
