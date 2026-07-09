# Reglas Generales de la Capa Core

Este es el `CLAUDE.md` de la capa de **lógica de negocio central (core)**. Sirve como referencia arquitectónica general de esta capa, describiendo su separación de responsabilidades, su modelo de gestión de errores y escalado, sus reglas estructurales y su comportamiento común. Todo lo que se cubre aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico al proyecto por diseño.** Nada aquí referencia un dominio, entidad o funcionalidad concretos. Cada regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la arquitectura de la capa core desde el primer día. La guía específica del proyecto extiende estas reglas con detalles de dominio, pero nunca debe contradecirlas.

**Precedencia.** En caso de conflicto entre este fichero y una guía específica del proyecto, estas reglas prevalecen.
**Inmutable.** Este fichero nunca debe editarse. Todos los cambios específicos del proyecto van en el fichero `*_guide.md` referenciado al final de este documento.
## 1. Aislamiento de la Capa

El paquete `core/` es una capa agnóstica al framework — **no tiene imports de `api/`**, `database/` ni `auth/`. Los servicios de la capa API traducen las subclases de `CoreError` a subclases de `ApiError` mediante una función de traducción.

## 2. Patrón de Jerarquía de Errores

Todos los errores de core derivan de una única clase base. Cada clase provee:

- `code` — identificador de cadena estable
- `default_message` — valor por defecto a nivel de clase
- `message` — override a nivel de instancia (recae en `default_message`)
- `detail` — dict opcional con contexto estructurado

Deriva los errores por dominio funcional (p. ej. errores de email, errores de pago) manteniendo una jerarquía plana o poco profunda dentro de cada dominio.

## 3. Técnica de Captura

Cada módulo de core sigue estas reglas al capturar excepciones.

### Reglas

1. **Valida pronto, falla con errores de dominio.** Comprueba configuración, tokens y estado de auth al inicio de cada método antes de cualquier llamada externa. Lanza el error de dominio correspondiente de inmediato.

2. **Captura primero las excepciones específicas del provider.** Cada bloque `try` lista los tipos de excepción concretos que puede lanzar el SDK del provider, ordenados de más específico a más general.

3. **Fallback genérico al final.** Un `except Exception as exc` final con el mensaje `"<Provider> unexpected <operation> error ({type}): {exc}"` garantiza que ninguna excepción escape sin tipar. Las capas internas pueden incluir `type(exc).__name__` en los mensajes de error, ya que estos siempre se traducen antes de llegar al cliente.

4. **Preserva la cadena de causas.** Usa siempre `raise ... from exc` para que el traceback original siga disponible para depuración.

5. **Nunca hagas doble envoltura de errores tipados.** Esta regla aplica cuando el código dentro de un bloque `try` puede lanzar una subclase de `CoreError` — ya sea vía un `raise` explícito o a través de un helper que lance una. Añade un `except CoreError: raise` dirigido (o la subclase específica) **antes** del handler genérico `except Exception`. **Si nada dentro del `try` puede producir un `CoreError`, la guarda es innecesaria.** Ejemplo:
   ```python
   try:
       self._internal_helper(...)     # Can raise a CoreError subclass
       result = provider_sdk.call()
   except CoreError:                  # Guard: re-raise before generic catch
       raise
   except ProviderError as exc:
       raise DomainSpecificError(...) from exc
   except Exception as exc:
       raise DomainSpecificError(...) from exc
   ```

6. **Reclasifica cuando cambia el significado funcional.** Un fallo de API externa durante el refresco de token se convierte en un error de refresco, no en un error genérico de API externa, porque la operación que falló es el refresco de autenticación.

7. **Parseo best-effort con fallback suave.** Al procesar datos de respuesta (cabeceras, fechas, cuerpos de error), tolera valores malformados con un fallback en lugar de abortar toda la operación.

8. **Envuelve y relanza — nunca loguees el traceback.** Los bloques `try` de esta capa traducen y relanzan; no deben loguear las excepciones que envuelven. La cadena `raise ... from exc` transporta la causa original hacia arriba hasta los handlers globales de la capa API, el único sitio donde se loguean los fallos del lado servidor — loguear aquí duplicaría ese registro. La única excepción: un error que esta capa captura y descarta deliberadamente (un fallback suave que continúa la operación) nunca llega a esos handlers, así que cuando la causa descartada importa para el diagnóstico, el propio sitio del descarte debe loguearla con `exc_info=exc`.

## 4. Reglas de la Fachada Pública

- Todo el código externo importa desde la raíz del paquete o desde la fachada del sub-paquete de dominio.
- El `__init__.py` re-exporta todos los símbolos públicos.
- Los consumidores externos nunca importan directamente desde submódulos internos.

## 5. Principios de Diseño

- Mantén el comportamiento de integración externa encapsulado dentro de módulos dedicados.
- Mantén las preocupaciones de la capa API fuera del código de core — sin imports de `api/`.
- Mantén los secretos envueltos en las fronteras y desenvueltos solo cuando sea necesario.
- Mantén los mensajes de error explícitos y específicos de la operación.
- Usa helpers compartidos para operaciones comunes y evitar duplicación entre implementaciones.

## 6. Guía Específica del Proyecto

Este fichero cubre las reglas generales y transferibles de la capa de lógica de negocio central. Para los detalles específicos del proyecto — reglas concretas, decisiones arquitectónicas y detalles de implementación que aplican estos principios generales a la aplicación actual — consulta [`core_guide.md`](core_guide.md).

La guía complementa estas reglas pero nunca las contradice. En caso de conflicto, este `CLAUDE.md` tiene precedencia absoluta. El código de esta capa debe respetar ambos niveles: primero estas reglas generales, y luego la guía específica del proyecto core_guide.md
