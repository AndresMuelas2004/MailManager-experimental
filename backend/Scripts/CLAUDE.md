# backend/Scripts/

Scripts de ejecución manual para la verificación práctica del comportamiento de los endpoints contra clientes reales de proveedores de correo.

Estos scripts **no contienen lógica de negocio** — llaman directamente a los endpoints de la capa de servicios ya existentes, actuando como wrappers ligeros de CLI. Su propósito es complementar las suites de tests automatizados (unit, integration, E2E) permitiendo la exploración manual de casos límite y comportamientos específicos de cada proveedor que resultan más fáciles de inspeccionar de forma interactiva.

Subdirectorios:

- `cli_utilities/` — scripts reutilizables para operaciones comunes (registrar usuarios, conectar cuentas, enviar correos, gestionar la papelera, etc.). Todos los parámetros se pasan mediante argumentos de CLI; sin credenciales hardcodeadas.
- `ejecucion_unica/` — scripts de un solo uso para tareas de configuración específicas (no versionados en git).
- `EXECUTION_MDs/` — notas personales con parámetros de ejecución (no versionadas en git).
