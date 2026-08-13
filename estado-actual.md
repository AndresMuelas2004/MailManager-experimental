# Estado Actual del Proyecto y Restricciones Operativas Vigentes

> Fichero autocargado en cada sesión vía `@estado-actual.md` desde el `CLAUDE.md` raíz (§16), igual que `common_mistakes.md`. Recoge el **estado operativo vigente** del proyecto y las **restricciones temporales** que no se derivan del código y que Claude debe respetar como reglas estrictas. Manténlo al día cuando el estado cambie; **elimina cada restricción en cuanto deje de aplicar**.

**Última actualización:** 2026-08-13.

---

## ⏸️ VERIFICACIÓN OAUTH DE GOOGLE — PARADA POR DECISIÓN DEL USUARIO (2026-07-22)

**Decisión del 2026-07-22: el trámite de verificación queda PARADO, sin fecha de reanudación.** El motivo es el único paso que queda y el único con coste: el **assessment CASA AL1** (~$675 con TAC Security, pago único por adelantado, requisito **anual**). El usuario decidió no pagarlo por ahora.

**Parar = no pagar. No se ha cancelado nada en la consola de Google** (decisión deliberada, ver "Por qué no se canceló" abajo). La solicitud sigue viva y **decae sola el 16-oct-2026** cuando venza el plazo del CASA, sin ninguna acción por nuestra parte.

### Qué NO pasa por parar (verificado contra la documentación oficial, 2026-07-22)

- **No hay sanción, multa, bloqueo ni veto.** En la documentación de Google no existe ninguna cláusula que revoque el acceso por no completar el assessment. Lo único documentado es la pantalla de "app no verificada" + el cap de usuarios, que la app **ya tiene desde siempre**.
- El correo de Google del 2026-07-18 sitúa el riesgo del plazo en *"el estado de verificación de **su solicitud**"* — es decir, sobre el trámite, no sobre el acceso a la API.
- **La app en producción sigue funcionando igual**: `https://missela.app` operativa, aviso de "app no verificada" en el consentimiento y **cap de 100 buzones Gmail de por vida del proyecto** (iba por 4/100). Ese cap es el estado en el que la app lleva desde el principio.
- **No hay nada contratado ni pagado con ningún laboratorio.** El contacto con TAC Security fue solo una consulta de precio; sus correos de seguimiento son comerciales y se ignoran deliberadamente.
- Reanudar es posible en cualquier momento: el trámite se reenvía desde el Verification Center cuando se quiera.

### Estado real del trámite en el momento de parar (dato valioso si se retoma)

El Verification Center (proyecto `mailmanager-486415`) muestra **6 de 7 requisitos APROBADOS** por Google:

| Requisito | Estado | Última revisión de Google |
|---|---|---|
| Homepage requirements | ✅ complete | 2026-07-16 |
| Privacy policy requirements | ✅ complete | 2026-07-18 |
| App functionality | ✅ complete | 2026-07-18 |
| Branding guidelines | ✅ complete | 2026-07-16 |
| Appropriate data access | ✅ complete | 2026-07-18 |
| Request minimum scopes | ✅ complete | 2026-07-18 |
| **Additional requirements** | ❌ error | 2026-07-18 — *"You are required to complete a CASA security assessment for your app."* |

Es decir: **landing, política de privacidad, funcionalidad, marca con logo, uso apropiado de datos y justificación del scope mínimo ya pasaron la revisión humana de Google, sin una sola ronda de observaciones.** El único bloqueo es el cheque del CASA.

### Por qué NO se canceló en la consola

Borrar `gmail.modify` de Data Access llevaría al mismo destino final (app sin verificar, cap 100), pero **destruiría hoy los 6/7 aprobados** y cerraría una opción que sigue abierta hasta el 16-oct: si el usuario cambiara de idea antes de esa fecha, pagar el CASA lo mete **directo al assessment con la revisión de Google ya aprobada**, sin volver a la cola ni arriesgar rondas de observaciones. Esa puerta se cierra sola el 16-oct; no hay motivo para cerrarla antes a mano.

---

## 🔓 Congelación de despliegue — RELAJADA (2026-07-22)

La congelación total anterior (que dejaba **cualquier** feature nueva sin desplegar hasta la Letter of Validation) **ya no aplica**: esa Letter no va a llegar mientras el trámite esté parado, así que mantenerla bloquearía el proyecto indefinidamente.

### Lo que SIGUE vigente

**Claude NUNCA despliega a producción por iniciativa propia** — un despliegue solo ocurre si el usuario lo pide de forma explícita. (Esta regla es permanente y no depende del trámite.)

**Hasta el 16-oct-2026**, mientras la solicitud siga técnicamente viva, conviene no desplegar a producción cambios que alteren la **superficie que Google ya aprobó**:

- el **nombre de la app** o el **logo**;
- los **scopes** de OAuth solicitados o declarados;
- el **dominio autorizado**;
- las **URLs ni el contenido** de la **home** (`https://missela.app`), de **`/privacy`** ni de **`/terms`**.

Preserva la opción de retomar el trámite antes de octubre sin haber invalidado la revisión ya aprobada. Si el usuario decide tocar alguna de esas piezas antes del 16-oct, **avísale de que eso invalida la revisión aprobada** y déjale decidir — no es un veto.

> **Excepción ya ejercida (2026-08-13): `/privacy` está modificada a conciencia.** Al instalar la analítica web (Google Analytics en las páginas públicas, ver `docs/features/analitica-web.md`) hubo que reescribir las secciones 3 y 9 de la política de privacidad — decían literalmente que no había analítica de terceros y que no hacía falta banner de consentimiento, y ambas cosas dejaron de ser ciertas. El usuario fue avisado de que esto invalida el "Privacy policy requirements ✅" que Google había aprobado el 2026-07-18, y decidió seguir adelante: el trámite está parado y va a seguir tirando de los 100 buzones de prueba. **Consecuencia práctica: si algún día se retoma la verificación, la política de privacidad tendrá que volver a pasar la revisión de Google** (el resto de requisitos aprobados no se han tocado). No hay que "arreglar" nada por esto.

### Lo que YA NO está restringido

**Desarrollar y desplegar features de la aplicación es libre.** El desarrollo en local y en ramas nunca tuvo restricción, y ahora tampoco la tiene el despliegue de funcionalidad interna que no toque la superficie listada arriba.

### Cuándo desaparece del todo

**El 16-oct-2026**, automáticamente y sin hacer nada: al vencer el plazo del CASA la solicitud decae y ya no hay revisión que invalidar. A partir de esa fecha **elimina las dos secciones de este fichero** (y, si queda vacío, la referencia `@estado-actual.md` del `CLAUDE.md` raíz §16 vía la skill `/edit-CLAUDEmds`). Si el usuario retoma el trámite antes, reescribe este fichero con la restricción que corresponda.

---

## 📭 Vigilancia del correo — YA NO APLICA

La rutina diaria de vigilar `amuelas30@gmail.com` esperando a Google **queda sin efecto**: el correo que se esperaba ya llegó (2026-07-18, exigiendo el CASA) y el trámite está parado. **Claude NO debe recordarle mirar el correo** ni tratar los correos de TAC Security como pendientes.

El registro completo de la fase CASA (qué es AL1 vs AL2, laboratorios, precios verificados, la decisión de parar y su justificación) vive en `UtilidadesDelProgramador/despliegue-app/verificacion-oauth/FASE-4-CASA-AL1.md`, y el histórico del trámite entero en la carpeta `UtilidadesDelProgramador/despliegue-app/`.
