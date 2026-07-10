# Reglas Generales de Tests Unitarios

Este es el `CLAUDE.md` de la capa de **tests unitarios**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de gestión de errores y escalado, sus reglas estructurales y su comportamiento común. Todo aspecto cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Independiente del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o funcionalidad concretos. Cada regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la arquitectura de la capa de tests unitarios desde el primer día. La guía específica del proyecto extiende estas reglas con detalles del dominio, pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas tienen precedencia.

## 1. Alcance

Los tests unitarios validan módulos individuales de forma aislada. **No** requieren:

- Servicios externos (bases de datos, APIs, colas de mensajes)
- Acceso a red
- Interacción con navegador
- Efectos secundarios sobre el sistema de ficheros

## 2. Principios

- **Testea un comportamiento por caso.** Cada test valida una única aserción lógica o contrato de comportamiento.
- **Mantén los tests deterministas y rápidos.** Sin aleatoriedad, sin dependencias de temporización, sin llamadas externas inestables (flaky).
- **Mockea solo en las fronteras externas.** Reemplaza las dependencias externas (red, base de datos, E/S de ficheros) con fakes o mocks. La lógica interna se testea directamente.
- **Prefiere el testeo de funciones puras cuando sea posible.** Las funciones sin efectos secundarios son las más fáciles de testear y no requieren mocking.

## 3. Estrategia de Mocking

Mockea o falsea (fake) estas fronteras:

- Peticiones de red (clientes HTTP, SDKs de API)
- Stores y conexiones de base de datos
- Operaciones del sistema de ficheros
- Llamadas externas de autenticación/verificación
- Operaciones dependientes del tiempo (usa timestamps deterministas)

**No** mockees:

- Funciones auxiliares internas (testéalas directamente)
- Transformaciones de datos y lógica de parseo
- Instanciación y jerarquía de clases de error

## 4. Utilidades de Test Compartidas

Mantén fakes y builders reutilizables en un módulo de utilidades de test compartido:

- **Clientes fake** — implementaciones deterministas de interfaces abstractas para testear la lógica de orquestación.
- **Funciones builder** — crean objetos de datos de test con valores por defecto sensatos y campos sobreescribibles.
- **Primitivas fake de base de datos** — registran ejecuciones SQL, devuelven resultados preconfigurados.
- **Helpers de patch** — funciones de conveniencia para reemplazar dependencias a nivel de módulo.

Las utilidades compartidas se usan tanto por los tests unitarios como por los de integración.

## 5. Convenciones de Nomenclatura

- Nombrado de ficheros: `test_<module>.py`
- Nombrado de funciones de test: `test_<behavior>_<scenario>`
- Agrupa casos relacionados en clases cuando mejore la legibilidad.
- Mantén las fixtures en `conftest.py` enfocadas y componibles.

## 6. Qué Cubren los Tests Unitarios

- Lógica de servicio y orquestación
- Traducción y mapeo de errores
- Validación de settings/configuración
- Funciones auxiliares y de utilidad
- Guard clauses y lógica interna de los clientes de provider
- Contratos de la jerarquía de errores (códigos, mensajes por defecto)

## 7. Qué NO Cubren los Tests Unitarios

- Flujos reales de OAuth/navegador
- Llamadas reales a las APIs de los providers
- Persistencia real en base de datos
- Cableado de router/servicio de la API (cubierto por los tests de integración)
- Flujos de extremo a extremo (cubiertos por los tests E2E)

## 8. Guía Específica del Proyecto

Este fichero cubre las reglas generales y transferibles de la capa de tests unitarios. Para los detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`unit_guide.md`](unit_guide.md).

La guía complementa estas reglas, pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, y luego la guía específica del proyecto.
