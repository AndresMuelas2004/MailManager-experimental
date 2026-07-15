import { Link } from 'react-router-dom';

// Copy legal aprobado (UtilidadesDelProgramador/despliegue-app/verificacion-oauth/
// BORRADOR-terms-of-service.md, versión en español). Cualquier cambio de contenido
// pasa por un nuevo borrador aprobado — no reescribir sobre la marcha.
export default function TermsContentEs() {
  return (
    <>
      <h1>Términos de servicio</h1>
      <p>
        <strong>Última actualización: 15 de julio de 2026</strong>
      </p>
      <p>
        Estos términos regulan tu uso de Missela, el cliente de correo disponible en missela.app,
        operado por Andrés Muelas Tenorio, desarrollador individual con base en España ("nosotros").
        Al crear una cuenta o usar el servicio, los aceptas.
      </p>

      <h2>1. Qué es Missela</h2>
      <p>
        Missela te permite conectar tus cuentas de Gmail y Outlook y leer, buscar, organizar,
        redactar y enviar correo desde una única interfaz. Tu correo en sí permanece en tu proveedor
        (Google o Microsoft); Missela opera sobre él a través de las APIs oficiales, con los
        permisos que concedes al iniciar sesión.
      </p>
      <p>
        Missela está actualmente en <strong>acceso anticipado (beta)</strong> y es gratuita durante
        la beta. Las funciones pueden cambiar, y en esta etapa cabe esperar errores puntuales o
        interrupciones.
      </p>

      <h2>2. Tu cuenta</h2>
      <p>
        Necesitas iniciar sesión con una cuenta de Google o Microsoft. Eres responsable de la
        seguridad de esa cuenta. Debes tener al menos 16 años para usar Missela. Pueden aplicarse
        límites técnicos al servicio (por ejemplo, un número máximo de buzones conectados por
        usuario).
      </p>

      <h2>3. Uso aceptable</h2>
      <p>
        No uses Missela para enviar spam, phishing o malware, para acosar a nadie, para infringir
        ninguna ley, ni para interferir con el propio servicio (sondearlo, sobrecargarlo, hacer
        scraping automatizado o intentar acceder a datos de otros usuarios). Podemos suspender o
        cerrar las cuentas que lo hagan.
      </p>

      <h2>4. Tus datos</h2>
      <p>
        Tu correo es tuyo. Lo tratamos únicamente para prestar el servicio, como se describe en la{' '}
        <Link to="/privacy">Política de privacidad</Link>, que forma parte de estos términos.
        Desconectar una cuenta o borrar tu cuenta de Missela elimina los datos asociados de nuestros
        servidores, como allí se describe.
      </p>

      <h2>5. Servicios de terceros</h2>
      <p>
        Missela depende de las APIs de Google y Microsoft. Tu uso de Gmail y Outlook sigue regido
        por sus propios términos y políticas. No somos responsables de esos servicios, de los
        cambios en sus APIs ni de las acciones que tomen sobre tus cuentas (como límites de
        peticiones o bloqueos de seguridad).
      </p>

      <h2>6. Disponibilidad y exclusión de garantías</h2>
      <p>
        Missela se ofrece "tal cual" y "según disponibilidad", sin garantías de ningún tipo, en la
        máxima medida que permita la ley. No garantizamos servicio ininterrumpido, funcionamiento
        sin errores ni la conservación de los datos cacheados. Missela no es un archivo de correo:
        la copia autoritativa de tu correo vive siempre en tu proveedor, así que un fallo de Missela
        nunca supone perder tu correo.
      </p>

      <h2>7. Limitación de responsabilidad</h2>
      <p>
        En la máxima medida que permita la ley, no responderemos por daños indirectos, incidentales
        o consecuentes, lucro cesante o pérdida de datos derivados del uso del servicio. Nada en
        estos términos limita la responsabilidad que la ley no permita limitar, incluida la derivada
        de dolo o negligencia grave, ni tus derechos imperativos como consumidor.
      </p>

      <h2>8. Terminación</h2>
      <p>
        Puedes dejar de usar Missela y borrar tu cuenta en cualquier momento desde Ajustes. Podemos
        suspender o cerrar cuentas que incumplan estos términos, y podemos descontinuar la beta con
        un preaviso razonable dentro de la aplicación.
      </p>

      <h2>9. Cambios en estos términos</h2>
      <p>
        Podemos actualizar estos términos conforme evolucione el servicio. Si un cambio es
        sustancial, lo anunciaremos dentro de la aplicación antes de que entre en vigor. Usar el
        servicio después implica aceptar los términos actualizados.
      </p>

      <h2>10. Ley aplicable</h2>
      <p>
        Estos términos se rigen por la ley española. Si usas Missela como consumidor en la UE,
        conservas las protecciones imperativas y reglas de jurisdicción de tu país de residencia.
      </p>

      <h2>11. Contacto</h2>
      <p>
        <a href="mailto:support@missela.app">support@missela.app</a>
      </p>
    </>
  );
}
