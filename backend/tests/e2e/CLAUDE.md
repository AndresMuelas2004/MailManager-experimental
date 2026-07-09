# Reglas Generales de Tests E2E

Este es el `CLAUDE.md` de la capa de **tests end-to-end**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de manejo de errores y escalado, sus reglas estructurales y su comportamiento común. Todo lo que se cubre aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico al proyecto por diseño.** Nada aquí referencia un dominio, entidad o funcionalidad concretos. Cada regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un nuevo proyecto para establecer la arquitectura de la capa de tests E2E desde el primer día. La guía específica del proyecto extiende estas reglas con detalles de dominio, pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas tienen precedencia.
**Inmutable.** Este fichero nunca debe editarse. Todos los cambios específicos del proyecto van en el fichero `*_guide.md` referenciado al final de este documento.
## 1. Alcance

Los tests E2E validan el flujo completo del backend contra APIs externas reales y autenticación real. **Nada se mockea ni se falsea** — cada componente se ejecuta exactamente como lo haría en producción.
Los endpoints interactivos (login OAuth, connect de provider) quedan excluidos de la suite automatizada. En E2E no debe haber tests que necesiten flujos interactivos por parte del usuario como elegir un email o algo similar.

## 2. Modelo de Frontera de Tests

| Componente | Comportamiento en E2E |
|---|---|
| App del framework | App real creada mediante la application factory |
| Autenticación | Sesión inyectada mediante inserción directa en BD (sin login interactivo) |
| Validación de sesión | Dependencia real — cookie verificada contra la BD |
| APIs externas | Llamadas reales a las APIs |
| Base de datos | Persistencia real |

## 3. Prerrequisitos

La suite se omite automáticamente cuando faltan los prerrequisitos.

Requeridos:
- Variables de entorno para la base de datos y las credenciales de los providers
- Los ficheros de credenciales deben existir en las rutas configuradas
- Acceso a internet

## 4. Diseño del Flujo

- El flujo se divide en tests individuales a nivel de endpoint para localizar los fallos rápidamente.
- Si un paso falla, los pasos dependientes posteriores se omiten para evitar el ruido en cascada.
- Cada test comprueba sus propios prerrequisitos y se omite si una dependencia falló, mientras que los
  tests independientes siempre se ejecutan.


## 5. Guía Específica del Proyecto

Este fichero cubre las reglas generales y transferibles de la capa de tests end-to-end. Para los detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`e2e_guide.md`](e2e_guide.md).

La guía complementa estas reglas pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, luego la guía específica del proyecto e2e_guide.md.
