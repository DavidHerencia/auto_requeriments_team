# RDA de subida XML: prueba de corrección HERO

## Entrada
El módulo `download_xmls.py` ya exporta el JSON local con `id_solicitud`, `id_n5`, `nombre_proceso` y `xml`. Apps Script y su puente no cambiaron.

## Prueba real en ChatGPT (1 o 2 registros)

1. Cierra ventanas previas de Chrome abiertas por el robot; conserva los perfiles existentes sin borrarlos.
2. Actualiza el repositorio e instala dependencias si faltan: `python -m pip install -r requirements.txt`.
3. En `config.local.json` configura la URL de HERO (`hero_gpt_url`) y cambia `modo_chatgpt` a `ejecutar`. El archivo es local e ignorado por Git.
4. Ejecuta `python app.py --fase chatgpt --archivo data/exportaciones/registros_XXXXX.json --limite 1`.
5. Escribe `PROCESAR` en la consola para autorizar el envío. Para dos registros, usa `--limite 2`.

El script mantiene **un mismo Chrome/Playwright** abierto durante los registros seleccionados y carga el inicio de HERO (no reutiliza un chat previo) al comenzar cada registro. Al terminar, deja Chrome abierto hasta pulsar Enter para inspeccionar el resultado; la sesión persiste en `data/chrome_profile_sheets/`.

## Flujo por registro

- Adjunta el XML a HERO utilizando el input inspeccionado: `input[type="file"][aria-label="Adjuntar archivos"]`.
- Escribe el prompt de corrección con **BPMN ARIS XML Repair** y lo envía mediante `editor.press('Enter')`, validado en el prototipo. Nunca envía automáticamente dos veces ante incertidumbre.
- Espera un turno nuevo de asistente y una señal de finalización con texto estable. No usa un máximo de espera fijo cuando termina antes.
- Busca descarga XML en el turno actual o contenido XML de un bloque de código. Si no hay XML válido, hace una sola petición de recuperación. Si hay timeout o error de carga, marca `requiere_revision` sin reenvío automático.
- Corrige únicamente el atributo `name` del elemento `id="LanePrincipal"` con `id_n5 nombre_proceso` (texto original), y valida el XML.
- Guarda `data/corregidos/<id_n5 nombre_proceso - Detalle>.xml` saneando caracteres inválidos Windows; el JSON derivado añade `id_archivo_n5`, ruta y estado `correccion_finalizada`. La exportación de entrada no se altera.

## Estado y trazabilidad

El checkpoint de lote está en `data/exportaciones/procesados_<nombre del JSON>.json`; los finalizados, fallidos o inciertos NO se reenvían en ejecuciones posteriores. Logs operativos en `data/logs/` sin contenido de los XML ni de las respuestas. Para repetir uno ya marcado habrá que revisar el caso, no borrar todo el progreso.

## Límites actuales

Los selectores de respuesta provienen del prototipo; no se han validado en la versión actual de HERO. Un enlace de descarga generado como botón distinto de `<a>` puede requerir su propio selector `gpt_descarga_selector`; si ocurre, inspeccionaremos el HTML de esa respuesta. La interacción con la habilidad debe confirmarse en una prueba real; escribir su nombre en el prompt no garantiza que se haya activado.

El aviso de Chrome `--no-sandbox` observado durante la inspección motivó activar `chromium_sandbox=True` en la etapa ChatGPT. **No se ha demostrado que dicho aviso causara los errores de respuesta.** Si Chrome corporativo no inicia, informar el error de lanzamiento para decidir ajustes. No modificar políticas de Chrome ni el perfil existente.

ARIS queda pendiente de disponibilidad. Solo procesar archivos corporativos que esté permitido subir a ChatGPT.

## Captura de código XML y diagnóstico de errores de HERO

El prototipo previo validó los selectores de turnos `[data-turn-key$=":assistant"]`,
mensajes `[data-markdown-style="assistant-message"]` y estados `data-turn-state`.
Esta versión aprovecha dichas señales y permite finalizar por estabilidad cuando no
se ve el indicador Detener. El DOM puede cambiar; no constituye una API.

La captura prioriza una descarga en el turno nuevo cuando exista un enlace verificable;
si aparece un bloque como el mostrado en HERO, lee `pre code` dentro de
`[data-markdown-style="assistant-message"]` (incluidos los spans de coloreado).
No usa el portapapeles ni captura otros turnos.

Si el navegador muestra «No se ha podido cargar esta respuesta», el código marca
`requiere_revision` sin reenviar. Además registra solo códigos HTTP de solicitudes
fallidas, tipos de recurso, errores de red y si Chrome se inició con `--no-sandbox`.
No guarda URLs de conversación, mensajes ni XML en logs. Estos diagnósticos **no
reparan por sí mismos** un error del servicio/navegador.

Prueba local: `python -m unittest discover -s tests -v`. Luego ejecutar
`python app.py --fase chatgpt --archivo data/exportaciones/registros_XXX.json --limite 1`
con `modo_chatgpt=ejecutar` y un registro autorizado. Para dos, `--limite 2`.
Un checkpoint existente con estado `requiere_revision` o `envio_iniciado`
no se reenvía automáticamente; revisar ese caso antes de repetirlo.

## Envío ChatGPT: método probado

El flujo de prueba anterior escribió el prompt y utilizó `editor.press("Enter")`.
Se usa ese método nuevamente en lugar de esperar un selector visible de botón Enviar,
que provocaba un timeout de 20 segundos sin enviar. Ahora se observa en el DOM un
**nuevo turno de usuario** como confirmación: nunca se repite Enter si la confirmación
falla. La espera de 30 segundos es solo un límite de seguridad, no una pausa fija.
Después se observa el turno nuevo de asistente y su estabilidad.

Probar con `--limite 1` y verificar logs `Prompt escrito`, `Enter pulsado`,
`Nuevo turno de usuario detectado` y `Respuesta estable`.
Los estados `requiere_revision` previos se conservan para prevenir duplicados;
el siguiente ensayo seleccionará otro registro pendiente. Antes de reenviar cualquiera
de ellos hay que comprobar manualmente la conversación correspondiente.
Los 403 observados son independientes del error del botón: pueden indicar
un rechazo de alguna solicitud del servicio, sin que sepamos cuál sin más evidencia.

## Corrección del detector para XML largo (26 de octubre de 2026)

En HERO se observó **mensaje enviado y XML visible**, pero el detector anterior
exigía `[data-turn-key$=":user"]` y agotaba 30 segundos, aunque el turno del
asistente ya existía. Esa señal no es obligatoria en esta interfaz: ahora la
respuesta **nueva** de asistente confirma el envío.

La finalización depende de **eventos**, no de tiempo transcurrido:
- `data-turn-state="complete"` dentro del turno actual y sin botón Detener; o
- Se observó el botón Detener, desapareció y volvió a estar disponible Enviar.

Tras observar uno de esos eventos, se comprueban dos lecturas consecutivas
idénticas del DOM, como precaución contra actualizaciones finales de renderizado.
Los 480 segundos configurables son **solo un timeout máximo** cuando esos eventos
no llegan. No se capturará XML parcial deliberadamente.

En pantalla, el XML coloreado de HERO se obtiene de `pre code` mediante
`textContent` (sin usar portapapeles). Solo entonces se valida el XML, se
modifica `LanePrincipal` y se guarda el archivo terminado.

**No borrar el checkpoint existente.** Los registros marcados
`requiere_revision` por el antiguo detector pudieron responder correctamente
en ChatGPT. Revisarlos o recuperarlos sin volver a enviar el mismo XML a ciegas.

## Diagnóstico del bloqueo de octubre 26

La inspección real del DOM de HERO reveló que el turno nuevo utiliza
`data-chatgpt-turn-key="...:assistant"` y no solo el antiguo
`data-turn-key="...:assistant"`. Por eso los logs repetían
`respuesta_nueva=False` aunque el XML estuviera visible. Ahora se
prioriza el atributo real y se conserva compatibilidad con el anterior
y `data-conversation-role="assistant"`; el valor antiguo que pueda
seguir en `config.local.json` se reconoce sin exigir modificar secretos.

La finalización sigue basada en la marca `complete` del turno o en
la transición observada de controles de generación. No se captura
una respuesta simplemente por haber transcurrido unos segundos.
Si encuentra el turno pero NO encuentra evidencia de finalización,
deja `requiere_revision` sin reenviar. En ese caso inspeccionaremos
juntos la señal real de finalización de la interfaz.

Los warnings HTTP 403 repetidos provenían de registrar **cada**
respuesta `fetch` rechazada. Ahora se agrupan en un resumen por
código de estado y tipo de solicitud, junto con los `ERR_ABORTED`.
No se guardan endpoints ni URLs sensibles. Estos códigos, por sí solos,
no permiten atribuir una causa; la evidencia mostró que HERO generaba
respuesta pese a ellos.

Con Ctrl+C una sola vez durante un registro, la aplicación marca
`requiere_revision`, guarda el checkpoint y detiene el lote antes
de cerrar Chrome. Evitar un segundo Ctrl+C durante la limpieza.

**Probar únicamente un pendiente** (`--limite 1`) antes de procesar
lotes. Los registros que ya tienen respuesta en HERO pero figuran como
`requiere_revision` no se reenvían automáticamente. Hay que revisar
o recuperar su resultado existente antes de volver a enviarlos.

## Detección al terminar una respuesta larga en HERO

La inspección mostró un botón con `aria-label="Iniciar Voz"` y
`data-state="closed"` después de la respuesta. **El atributo
`data-state=closed` corresponde a un control emergente/tooltip del
botón y no es evidencia fiable de finalización.**

El RDA ahora verifica: (1) si el turno nuevo tiene estado `complete`;
o (2) si **se observó el botón Detener**, luego desapareció y el composer
volvió a estar listo (botón Enviar habilitado o botón `Iniciar Voz`
visible). Nunca cierra solo porque el XML parezca completo o por una
espera temporal. Se mantiene un tiempo máximo configurable por seguridad.

Si el robot no ve el botón Detener ni el atributo `complete`, guardará
`requiere_revision` sin reenviar a ciegas. En esa situación necesitamos
inspeccionar el HTML del botón mientras está generando para confirmar
el selector exacto. Los controles del DOM de ChatGPT no son API estable.

## DOM validado con dos capturas locales (26-10-2026)

Se analizaron dos exportaciones HTML completas de conversaciones de HERO
**sin almacenarlas en Git**. Resultado observado:

- Los mensajes reales usan `[data-chatgpt-search-unit-key$=":assistant"]`.
  `[data-conversation-role="assistant"]` identifica solo el encabezado
  accesible oculto «ChatGPT dijo:», NO el cuerpo.
- El cuerpo usa `[data-markdown-text-style="assistant-message"]`.
  Para XML en texto, `pre code` contiene los fragmentos `span` de
  coloreado: Playwright `all_text_contents()` reconstruye el texto completo.
  Se eliminan espacios externos y se verifica que el resultado sea XML.
- La opción de archivo no es `<a download>`: es
  `button[aria-label="Descargar archivo"]`, dentro del mensaje pero
  **fuera** de la raíz Markdown. Se hace clic y se espera un evento real
  `download`, luego se valida el contenido del archivo.
- El turno tiene un ancestro `[data-talvt-turn-state="complete"]`.
  Esa señal explícita tiene preferencia sobre inferir finalización por tiempo.
- Si hay XML correcto en la primera respuesta, NO se envía el prompt de
  recuperación. No se descargan ni copian XML de turnos anteriores.
- Los XML del ejemplo del bloque de código son bien formados, pero **NO
  contienen `id="LanePrincipal"`**. El RDA seguirá rechazándolos en
  la etapa posterior, en vez de marcar falsamente `correccion_finalizada`.
  La estructura BPMN esperada deberá revisarse antes de escalar a los 100.

Los fixtures de las pruebas son **sintéticos**: ningún XML o HTML de usuario
se incorpora al repositorio. Las pruebas de Playwright contra una respuesta
real requieren la siguiente ejecución en el Chrome autorizado.

## Hallazgo corregido: el XML sí se capturaba

En el log del 26/10 (solicitud S000000000659), Playwright confirmó:
`Respuesta COMPLETA: caracteres=32041 bloques_codigo=1` y
`XML completo recuperado de pre code; caracteres=32037`.
El posterior warning «segundo intento sin XML válido» fue **incorrecto**:
se originó en `corregir_lane()`, no en la captura del GPT.

En los dos HTML de prueba el ID real es `Lane_Principal`, no
`LanePrincipal`. Ahora se admiten esas dos variantes documentadas, solo
si identifican **exactamente una** etiqueta BPMN `lane`.
El cambio sigue siendo textual únicamente sobre el atributo `name`;
no se reserializan los namespaces ni el XML. Si no hay lane inequívoca,
NO se inventa ni reemplaza una arbitraria.

Como precaución, antes de modificar la lane, el XML capturado válido se
guarda en `data/capturados/`, y se refleja inmediatamente en el checkpoint:
`estado=xml_capturado`, `xml_capturado_ruta` e `id_archivo_n5`.
Después:
- `correccion_finalizada`: lane actualizada, XML definitivo validado.
- `postprocesamiento_pendiente`: el XML de HERO **ya existe en disco**,
  pero la lane requiere revisión. No repetir llamada al GPT a ciegas.
- `correccion_fallida`: la respuesta de GPT realmente no produjo XML válido.
  No se confunde con un error posterior.

Los checkpoints antiguos con `correccion_fallida` por este bug no disponen
del XML capturado localmente; deben recuperarse de los chats existentes si
se quiere evitar otra generación. Ningún archivo real ni secreto va a Git.

## Corrección final de nombres y control de XML incompleto

Al terminar la respuesta de HERO, el RDA captura y valida el XML, **antes**
de modificar nombres. Solo cambia dos atributos de nombre:
- `semantic:process@name`
- `semantic:lane@name` de la única lane principal con
  `id="Lane_Principal"` o `id="LanePrincipal"`.

Ambos reciben exactamente `id_n5 + " " + nombre_proceso`, sin sanear
el texto interno del XML. Se validan ambos atributos tras la modificación,
exigiendo un único proceso y una lane principal dentro del mismo. Se
conservan la serialización original, los IDs, namespaces y los atributos
`definitions.name`, que **no** se modifican por no estar incluidos
en el requisito. El nombre del **archivo** sí se sanea para Windows y
termina en ` - Detalle.xml`.

Los dos prompts incluyen la instrucción de completar un XML incompleto
usando solo información disponible y, cuando no sea posible por falta
de información, responder exactamente:
`incompleto - no se puede generar porque falta demasiada info`.

El segundo prompt termina con la alternativa explícita de entregar
XML completo para copiar/pegar. Solo se envía si la primera respuesta
finalizada no contiene XML válido **y no indica incompleto**.
Ambos envíos usan `_enviar`: un único `Enter` y detección de un
turno nuevo y finalizado, no tiempos fijos ni reenvíos a ciegas.

Si HERO responde exactamente la frase de incompleto (admitiendo
comillas/puntuación y variantes de guion), se registra
`estado=incompleto` y `motivo` en el checkpoint, sin segundo
prompt ni reintento. Fallos de XML real conservan
`correccion_fallida`; fallos de posprocesamiento conservan
`postprocesamiento_pendiente` con el XML capturado.

Prueba de edición sobre el XML completo aportado en el chat: proceso y
lane normalizados, namespaces y nombres de otras lanes intactos; el
XML real no se incluye en Git.

## ARIS: importar por informe (etapa 3, prueba controlada)

**Selección de carpeta:** pega la URL completa de la carpeta autorizada del repositorio en \`aris_url\` de \`config.local.json\`. Esa URL no se añade al código ni se publica en Git. El robot no navega por otros grupos ni mueve modelos.

**Controles observados en las capturas:** la pestaña Informes usa \`li[data-testid="REPORT_REPOSITORY_BUTTON"] a[aria-label="Informes"]\`; el selector de informes es un combobox de \`div.reportActionPanel\` y se elige por **clic** el texto exacto \`BBVA - Importar BPMN\`; \`Lanzar\` está en el panel de informes. En el modal de archivo hay un \`input[type="file"][name="fileUpload"][accept*=".xml"]\`. Playwright usa \`set_input_files\` sin abrir el diálogo de Windows. Luego Aceptar → Opciones de importación → Importación automática → Aceptar.

**Seguridad:** por defecto \`modo_aris="inspeccionar"\` no ejecuta el informe. Para una importación real cámbialo a \`"ejecutar"\` y confirma escribiendo \`IMPORTAR\` **antes de lanzar** el informe. Se usa el perfil Chrome persistente del RDA; cerrarlo si ya está abierto por otro proceso. La prueba inicial debe usar **un solo XML**, sin navegación ni acciones de mover/copiar carpetas.

El modal terminal se clasifica así:
- \`informe_completo\` al observar \`El informe está completo.\`. **No prueba por sí solo que el modelo esté presente en la carpeta.**
- \`xml_rechazado\` al observar \`El fichero seleccionado no es válido\`. Queda registrado para una única revisión adicional con GPT tras validar primero el importador. **No reenvía automáticamente el mismo XML.**
- \`requiere_revision\` ante timeout, resultado ambiguo u otra interrupción después de iniciar.
No se infieren éxitos del simple progreso o del texto del histórico de informes.

### Entrada desde JSON corregido

\`\`\`powershell
python app.py --fase aris --archivo data\exportaciones\procesados_registros_XXX.json --limite 1
\`\`\`

El módulo lee únicamente registros \`correccion_finalizada\` y verifica que su \`xml_corregido_ruta\` exista dentro de \`data/corregidos\`. Guarda un checkpoint separado \`data/exportaciones/aris_procesados_registros_XXX.json\`, incluyendo hash SHA-256, intentos, estado de ARIS y código de solicitud; si un XML cambia entre ejecuciones, se detiene ese registro. No se modifica el JSON de ChatGPT.

También se admite un XML aislado para inyección directa:
\`\`\`powershell
python app.py --fase aris --archivo data\corregidos\archivo - Detalle.xml
\`\`\`
(usa comillas cuando el nombre contiene espacios). En modo ejecutar se hace una copia no sobreescrita a \`data/xml\`. El JSON es la entrada recomendada para lotes.

**Qué queda por verificar en tu navegador:** DOM del menú desplegable al seleccionar el informe (el HTML de las opciones no apareció en las capturas), estructura del primer botón Aceptar y DOM del selector de tipo automático. El código se detiene si esos pasos no se identifican de forma inequívoca. Se preserva el modal de resultado para inspeccionarlo manualmente antes de cerrar Chrome. El reintento por GPT tras un error explícito se conectará cuando validemos una importación exitosa y una rechazada, sin automatizar reenvíos a ciegas.


## ARIS: selector del informe corregido (HTML real, 26-10-2026)

Los intentos de prueba S000000000799 y S000000000800 fallaron ANTES
del botón Lanzar. Las trazas de \`seleccionar_informe\` muestran:
- Dos coincidencias visibles con texto \`BBVA - Importar BPMN\`:
  una opción del desplegable y una entrada de ejecución en el historial
  \`.reportPanel-reports-list .finished-report-item-row\`.
- El \`input[role=combobox]\` puede existir pero permanecer **oculto**
  cuando la pestaña Informes aún no está abierta.

La corrección valida visibilidad real del panel
\`.sidebar.reportSidebar\` y del \`.tcReportSelectReportDropDown input\`;
solo hace clic en la pestaña \`REPORT_REPOSITORY_BUTTON\` si no figura
activa, evitando el efecto toggle que la puede cerrar. Para seleccionar
informe: clic en \`.tcReportSelectReportDropDown a[aria-label="dropdown-toggle"]\`,
buscar texto exacto en la lista desplegada **excluyendo completamente
el historial de informes**, clic en la opción visible y verificar que
el combobox contiene exactamente el nombre esperado. Si hay 0 o 2
opciones utilizables se detiene: no hace clic al azar.

\`aris_grupo_informes\` (predeterminado \`03 PROYECTOS\`) valida que el
contexto del panel sea la carpeta autorizada; ajustar localmente solo
si cambia intencionalmente la carpeta. \`aris_timeout_seleccion_ms\` vale
20000; es máximo de seguridad, no una espera fija. Si falla antes de
importar, el navegador puede quedar abierto hasta Enter para inspección.

### Validación recomendada sin efectos

Cambiar solo este campo de \`config.local.json\`:

\`\`\`json
"modo_aris": "preparar"
\`\`\`

Y ejecutar, con el JSON ya procesado:

\`\`\`powershell
python app.py --fase aris --archivo data\exportaciones\procesados_registros_20261026T060440_012519Z.json --limite 1
\`\`\`

\`preparar\` abre la carpeta de ARIS, abre Informes si procede,
selecciona el informe mediante clic y **se detiene antes de Lanzar**.
Conserva Chrome para revisión y registra
\`estado_aris=pendiente\` (sin incrementar intentos de importación).

Los checkpoints anteriores con \`preparacion_fallida\` se vuelven a
elegir para este paso; representan fallos **anteriores al lanzamiento**,
sin importación realizada. Nunca se reintenta automáticamente un registro
\`aris_iniciado\`, \`requiere_revision\`, \`informe_completo\` o
\`xml_rechazado\`. No borrar el checkpoint ni volver a generar XML.

Cuando funcione el paso de selección, cambiar a
\`"modo_aris": "ejecutar"\` para la prueba controlada real y confirmar
\`IMPORTAR\` por consola. Si ARIS cambia el DOM del menú desplegado,
inspeccionar el popup abierto; no inferir el selector de las entradas
históricas.


### Corrección del 26-10: contexto real del panel Informes

El registro S000000000799 llegó a abrir el panel Informes pero se
detuvo antes de abrir el desplegable: la validación exigía por defecto
\`03 PROYECTOS\`, mientras la página devolvía otro número de elementos
o un texto diferente. Esa comprobación no decía cuál era el valor real.

El robot ahora espera un **contexto visible** dentro de
\`.sidebar.reportSidebar .reportContextPanel\`, registra el texto
observado, tolera espacios repetidos y utiliza el grupo observado
en la confirmación de ejecución.

\`aris_grupo_informes\` es **opcional**, sin valor por defecto.
Si se define en \`config.local.json\`, la comparación es estricta en
\`modo_aris="ejecutar"\`, pero **no bloquea \`preparar\`**: en ese
modo solo emite una advertencia y sigue con la selección, sin lanzar.
Después de comprobar la carpeta correcta, configurar el grupo real
en el archivo local si se desea la verificación estricta.

El log esperado para la prueba sin efectos incluye:
\`ARIS: contexto real del panel Informes: [...] \`,
\`ARIS: desplegable Informes abierto\` y
\`ARIS: informe exacto seleccionado por clic: BBVA - Importar BPMN\`.
Si el desplegable cambia de estructura y falla, detenerse y compartir
solo la sección del DOM del menú abierto; no subir secretos al repo.


## ARIS: transición automática tras elegir el XML (26-10-2026)

Los logs confirmaron que \`set_input_files()\` adjuntó el XML; a continuación
la interfaz ya mostraba **Opciones de importación**, pero el robot intentaba
pulsar \`Aceptar\` en el modal anterior, ahora deshabilitado.
No era un problema del explorador nativo de Windows: Playwright no necesita
abrirlo, solo usar el \`input[type="file"]\` HTML.

El RDA ahora espera la primera señal observable tras seleccionar:
- \`Opciones de importación\`: ARIS avanzó automáticamente. No pulsar
  \`Aceptar\` en el selector de fichero otra vez.
- \`El fichero seleccionado no es válido\`: registrar rechazo explícito.
- \`Aceptar\` habilitado en el modal de fichero (si ARIS no avanzó):
  pulsarlo **una vez** y esperar el siguiente modal.

Si entre la comprobación y el clic cambia el modal, se evita repetir
la acción. Luego se valida \`Importación automática\`, se acepta, y se
espera el resultado final del informe. \`aris_timeout_paso_ms\`
(45000) limita esperas sin sustituir los eventos. No se leen ni registran
datos privados de los XML.

**Checkpoint:** S000000000799 quedó \`requiere_revision\` porque ya se
lanzó el informe y no se confirmó su resultado. El RDA no lo repetirá
automáticamente. Comprobar en ARIS si hubo importación antes de
reprocesarlo; para la siguiente prueba se puede utilizar el siguiente
registro \`pendiente\`. No borrar ni editar a ciegas checkpoints.

### Actualización de importación ARIS (26-10-2026)

* **ARIS tarda en cargar**: el texto `Ninguna selección de informe válida disponible`
  no es un nombre de carpeta. El robot espera que aparezca el contexto real;
  si `aris_grupo_informes` está configurado en modo ejecutar, solo continúa
  cuando coincide. El límite es `aris_timeout_carpeta_ms` (120000).
  `aris_timeout_seleccion_ms` es 60000; ninguno introduce sleeps fijos.
* **Popup de opciones**: se usa el modal visible
  `[data-testid="SCRIPT_DIALOG"]`, combobox
  `[data-testid="DROP_OPCIONES"] input[role="combobox"]` y
  `.modal-footer a[aria-label="Aceptar"]`. Antes del clic se comprueba
  mediante el valor DOM que el tipo sea exactamente `Importación automática`.
* **Reintentar el MISMO XML en pruebas, sin borrar checkpoints**: verifica
  primero en ARIS, dentro de `P00 Importación XML`, que ese modelo no se
  haya importado. Ejemplo:
  ```powershell
  python app.py --fase aris --archivo data\exportaciones\procesados_registros_20261026T060440_012519Z.json --limite 1 --reintentar-solicitud S000000000800
  ```
  Se pide escribir `NO_IMPORTADO S000000000800` y después el usual
  `IMPORTAR`. Crea backup del checkpoint, conserva los intentos previos
  y habilita únicamente esa solicitud. Si hay dudas de si importó, **NO**
  habilitar el reintento; podría duplicar modelos.
* Los estados `requiere_revision` e `informe_completo` nunca se
  reintentan implícitamente. La finalización del informe no sustituye
  verificar la existencia del modelo en el repositorio.

### ARIS: selector de importación sin tilde (26-10-2026)

El log del 26/10 mostró `valor observado='Importación automatica'` (sin
segunda tilde). El RDA comparaba el valor literalmente contra
`Importación automática` y agotaba 45s pese a que el modal correcto
estaba listo. Ahora se comparan ambos valores normalizando solamente
acentos, mayúsculas y espacios; `Importación manual` **no** es válido.
Después se valida de nuevo y se hace exactamente un clic en
`[data-testid="SCRIPT_DIALOG"] .modal-footer a[aria-label="Aceptar"]`.
La fase sigue esperando el resultado final de ARIS, no supone éxito.

Los lanzamientos de Chrome en ARIS y descarga de XML usan
`chromium_sandbox=True`, como ChatGPT, para evitar el aviso de Chrome
por `--no-sandbox`. Esa advertencia era independiente del fallo por
acentos. No se registran ni incorporan secretos al repositorio.

### Cierre del resultado de ARIS (26-10-2026)

Tras el informe `BBVA - Importar BPMN`, ARIS muestra el mensaje modal
`El informe está completo.` (o el rechazo conocido
`El fichero seleccionado no es válido`). El RDA conserva el estado
y ahora pulsa exactamente una vez el botón `Aceptar` del modal activo:
`div[role="dialog"][aria-hidden="false"] .modal-footer a[aria-label="Aceptar"]`.
Espera a que desaparezca el modal y falla en modo seguro si no puede
confirmar el cierre. `informe_completo` NO equivale a confirmar por sí
solo que existe el modelo en la carpeta final.

También cierra pestañas adicionales del mismo servidor ARIS que se hayan
abierto después de lanzar el informe; nunca cierra la pestaña principal,
pestañas anteriores o URLs de otros sitios. Si una pestaña no se puede
identificar como propia, la deja abierta y registra el aviso. No se
han implementado aún copiar/pegar/mover/renombrar ni un segundo informe;
esas operaciones irán en otro módulo.
