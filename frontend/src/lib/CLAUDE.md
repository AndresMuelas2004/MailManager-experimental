# Reglas Generales de la Capa de Utilidades

Este es el `CLAUDE.md` de la **capa de utilidades** del frontend. Alberga los bloques de construcción puros y reutilizables que toda capa superior tiene permitido consumir. Todo lo cubierto aquí es transferible a cualquier aplicación que siga esta arquitectura por capas — nada es específico de un único proyecto.

**Agnóstico del proyecto por diseño.** Nada aquí hace referencia a un dominio, entidad o feature concretos. Toda regla aplica a cualquier repositorio que siga esta arquitectura por capas.

**Reutilizable.** Copia este fichero en un proyecto nuevo para establecer la capa de utilidades desde el primer día.

**Precedencia.** En caso de conflicto entre este fichero y cualquier documento más abajo en el repositorio, estas reglas tienen precedencia.

**Inmutable.** Este fichero nunca debe editarse. Todo cambio en las reglas de la capa de utilidades pasa por una nueva versión de este fichero.

## 1. Propósito

La capa de utilidades alberga helpers **puros, transversales a features y ligeros respecto al framework**. Todo lo que dos o más features duplicarían de otro modo vive aquí. Todo lo atado a una única feature permanece dentro de esa feature.

## 2. Estructura

```
lib/
├── types.ts         # Cross-feature domain-agnostic types
├── formatters.ts    # Pure formatting functions (dates, numbers, strings)
├── <registry>.ts    # Optional config registries (enum maps, presets)
└── hooks/           # Generic React hooks with no domain knowledge
```

La lista es abierta, pero cada fichero debe satisfacer las reglas de abajo. Se anima a añadir un nuevo subfichero o subdirectorio dentro de `lib/` cuando emerja un helper genuinamente reutilizable.

## 3. Reglas

### 3.1 Pureza
- Las funciones deben ser deterministas y libres de efectos secundarios siempre que sea posible. Entra una entrada, sale una salida.
- Los helpers impuros (los que leen `Date.now()`, el DOM o estado externo) solo se permiten cuando son necesarios, documentados con un comentario de una línea, y libres de semántica de negocio.

### 3.2 Alcance
- Cada export debe ser significativo en al menos **dos** sitios del codebase. Si solo lo usa una feature, muévelo a esa feature.
- Sin dependencias del dominio de la aplicación. `lib/` no debe conocer usuarios, sesiones, recursos, pages, ni ningún concepto que pertenezca a una feature o a `api/`.

### 3.3 Hooks en `lib/hooks/`
- Deben ser genéricos: sus parámetros de tipo y su comportamiento aplican a muchas formas de datos, no a una entidad concreta.
- No deben llamar a endpoints. El data fetching pertenece a los hooks de feature, no aquí.
- Nomenclatura: `useXxx.ts` exportando `useXxx` como export por defecto (o con nombre — ver las convenciones de nombres en el `CLAUDE.md` raíz del frontend).

## 4. Debe / No Debe

### Debe
- Mantenerse libre de dependencias respecto al resto de `src/`. Solo React, paquetes de terceros fijados (TypeScript-safe), y otros ficheros dentro de `lib/`.
- Proporcionar tipos TypeScript explícitos para cada export público.

### No Debe
- Importar de `api/`, `features/`, `components/`, `app/` o `test/`. Cualquier import de este tipo es una violación arquitectónica.
- Realizar renderizado JSX (más allá de los hooks genéricos que devuelven estado/callbacks).
- Mantener estado mutable a nivel de toda la aplicación. Los hooks genéricos pueden mantener estado *local* acotado al llamante.

## 5. Fronteras de Imports

| Imports permitidos                                                                          | Imports prohibidos                                                 |
|---------------------------------------------------------------------------------------------|--------------------------------------------------------------------|
| React (para hooks), dependencias de terceros fijadas, otros ficheros dentro de `lib/`       | Cualquier otro directorio bajo `src/` (`api`, `features`, `components`, `app`, `test`) |

## 6. Añadir Algo a `lib/` — Checklist

- [ ] Confirma que el helper es genuinamente reutilizable en dos o más sitios. Si no, ponlo en la feature que lo posee.
- [ ] Elimina cualquier dependencia del dominio de la aplicación. Haz la API genérica.
- [ ] Escribe un test unitario co-ubicado junto al fichero (`*.test.ts`). Ver `src/test/CLAUDE.md`.
- [ ] Exporta solo lo que otras capas necesitan — mantén los helpers internos privados del módulo.
- [ ] No introduzcas imports de otros directorios de `src/`.
