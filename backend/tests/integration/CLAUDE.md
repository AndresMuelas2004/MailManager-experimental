# Reglas generales de tests de integración

Este es el `CLAUDE.md` de la capa de **tests de integración**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de manejo de errores y escalado, sus reglas estructurales y su comportamiento común. Todo lo que se cubre aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico al proyecto por diseño.** Nada aquí referencia un dominio, entidad o funcionalidad concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un nuevo proyecto para establecer la arquitectura de la capa de tests de integración desde el primer día. La guía específica del proyecto amplía estas reglas con detalles de dominio, pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas tienen precedencia.
**Inmutable.** Este fichero nunca debe editarse. Todos los cambios específicos del proyecto van en el fichero `*_guide.md` referenciado al final de este documento.
## 1. Alcance

Los tests de integración verifican el flujo interno completo del backend:

```
router → router helpers → service → database → service → core
```

Estos tests ejecutan endpoints reales del framework y operaciones reales de base de datos, mientras sustituyen las fronteras de proveedores externos con fakes.

## 2. Modelo de fronteras de los tests

| Componente | Real o Fake | Notas |
|---|---|---|
| App del framework | Real | Ejercitada vía cliente de test |
| Routers y servicios | Real | Módulos de producción |
| Base de datos | Real | Aislamiento por rollback de transacción por test |
| Orquestación de core | Real | Construida y usada en los tests |
| Llamadas a APIs externas | Fake | Sustituidas con clientes fake |
| Carga de credenciales de la app | Fake | Monkeypatched |
| Carga/guardado de tokens | Fake | Monkeypatched |
| Autenticación de sesión | Fake | Dependencia de auth sobreescrita |

## 3. Aislamiento de base de datos

- Cada test se ejecuta dentro de una transacción de base de datos que se hace **rollback** después de que el test termina.
- Una conexión compartida se inyecta vía monkeypatch en todos los módulos de repositorio.
- El esquema se crea una vez por sesión de test (p. ej. vía herramienta de migración).
- Un usuario de test determinista se siembra por test para las comprobaciones de propiedad.
- Los tests son totalmente independientes — ningún test depende del estado producido por otro. El rollback por test y las fixtures de seed garantizan un estado inicial limpio y determinista para cada test.

## 4. Patrón de override de auth

- La dependencia de sesión/auth se sobreescribe para devolver un ID de usuario de test fijo para todos los endpoints protegidos.
- Los tests que verifican la validación real de sesión eliminan temporalmente el override y lo restauran en un bloque `finally`.

## 5. Cobertura de estrategia de errores

Los tests de integración separan dos superficies de error principales:

1. **Errores directos de la capa API** — el servicio lanza `ApiError` directamente (recursos ausentes, fallos de validación, errores de auth/sesión).
2. **Errores traducidos** — errores de capas inferiores traducidos a errores de API (errores de core, errores de base de datos, errores de auth).

## 6. Patrones de diseño de fixtures

- **Fixture de esquema** (scope de sesión, autouse) — ejecuta las migraciones una vez.
- **Fixture de aislamiento** (por test, autouse) — gestiona el rollback de transacción.
- **Fixture de seed** (por test, autouse) — inserta datos de test deterministas.
- **Fixture de override de auth** (scope de sesión, autouse) — sobreescribe la dependencia de auth.
- **Fixture de cliente** — parchea los helpers de construcción y provee un cliente de test.
- **Helpers de setup** — fixtures invocables que crean recursos prerrequisito vía llamadas a la API.
- **Fixture de cliente fallido** (parametrize indirecto) — inyecta fallos fake para los tests de traducción de errores.

## 7. Reglas de mantenimiento

- Mantén el comportamiento de los fakes determinista.
- Mantén cada test enfocado en un contrato de API o una ruta de traducción.
- Evita asunciones específicas de proveedor en los tests de integración.
- Añade cobertura E2E cuando un cambio dependa del comportamiento real del proveedor.

## 8. Lo que los tests de integración NO cubren

- Flujos OAuth reales en navegador
- Tráfico HTTP real de proveedor
- Refresco real de tokens contra endpoints en vivo
- Comportamiento del frontend

## 9. Guía específica del proyecto

Este fichero cubre las reglas generales y transferibles de la capa de tests de integración. Para detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`integration_guide.md`](integration_guide.md).

La guía complementa estas reglas pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, luego la guía específica del proyecto.
