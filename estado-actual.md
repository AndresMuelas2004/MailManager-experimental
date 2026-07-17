# Estado Actual del Proyecto y Restricciones Operativas Vigentes

> Fichero autocargado en cada sesión vía `@estado-actual.md` desde el `CLAUDE.md` raíz (§16), igual que `common_mistakes.md`. Recoge el **estado operativo vigente** del proyecto y las **restricciones temporales** que no se derivan del código y que Claude debe respetar como reglas estrictas. Manténlo al día cuando el estado cambie; **elimina cada restricción en cuanto deje de aplicar**.

**Última actualización:** 2026-07-16.

---

## 🔒 RESTRICCIÓN DE DESPLIEGUE VIGENTE — verificación OAuth de Google en curso

**Contexto:** la app de producción `https://missela.app` está en **verificación OAuth de Google** para el scope restringido `gmail.modify`, **enviada a la cola el 2026-07-16** (Verification Center: *"branding and data access are currently under review"*). El detalle del trámite (vídeo demo, justificación, secuencia de envío, investigación de casos reales) vive en la carpeta local ignorada por git `UtilidadesDelProgramador/despliegue-app/verificacion-oauth/`.

### Regla estricta (mientras dure la verificación, hasta recibir la Letter of Validation)

**Claude NUNCA despliega a producción por iniciativa propia** — un despliegue solo ocurre si el usuario lo pide de forma explícita. Y mientras dure la verificación, Claude **NUNCA** debe desplegar — ni proponer desplegar — ningún cambio que altere la **superficie que Google revisa**:

- el **nombre de la app** o el **logo**;
- los **scopes** de OAuth solicitados o declarados;
- el **dominio autorizado**;
- las **URLs ni el contenido** de la **home** (`https://missela.app`), de **`/privacy`** ni de **`/terms`**.

Además, `missela.app` debe **permanecer accesible y con TLS válido** en todo momento (el revisor visita la home y esos enlaces durante la revisión).

### Única excepción permitida

Un **hotfix de seguridad o de un bug crítico** que **no toque** ninguno de los elementos listados arriba.

### Sin restricción en local

El desarrollo **en local y en ramas es totalmente libre**; la congelación aplica **solo** al despliegue a producción. Las features nuevas se desarrollan en rama y se quedan **sin desplegar** hasta la Letter of Validation.

### Por qué / causa

Cambiar cualquier elemento que Google ya está revisando devuelve la app al estado **"needs verification"** y **reinicia o reabre** el trámite desde el principio — semanas perdidas. Está documentado en casos reales recopilados en `UtilidadesDelProgramador/despliegue-app/verificacion-oauth/CASOS-REALES-VERIFICACION-OAUTH.md` (no existe jerarquía de scopes en Google; cambiar marca, dominio, scopes o URLs revisadas resetea la revisión).

### Cuándo se levanta

Al recibir la **Letter of Validation** de Google (fin del proceso, tras el assessment CASA). En ese momento, **elimina esta sección** de este fichero (y, si queda vacío, la referencia `@estado-actual.md` del `CLAUDE.md` raíz §16 vía la skill `/edit-CLAUDEmds`).

---

## 📬 A la espera de la confirmación de Google (desde 2026-07-16)

El trámite está en la cola de Google y **el usuario está a la espera de su confirmación**. El usuario ya vigila él mismo, a diario, su Gmail `amuelas30@gmail.com` (developer contact del proyecto); un filtro ya creado marca los correos del equipo de verificación (`oauth-app-verification-review@google.com`) como destacados y fuera de Spam. **Claude NO debe recordarle que mire el correo** — solo queda aquí como constancia del estado. El detalle de qué hacer cuando llegue el correo (responder rápido, posibles rondas de remediación, el assessment CASA) vive en `UtilidadesDelProgramador/despliegue-app/verificacion-oauth/CASOS-REALES-VERIFICACION-OAUTH.md`.
