# RDA XML — corrección con HERO

La fase download validada exporta un JSON con cuatro campos: `id_solicitud`, `id_n5`, `nombre_proceso`, `xml`. Sin cambios de Apps Script.

## Primer paso: inspección de ChatGPT

Ejecutar `python app.py --fase chatgpt --archivo data/exportaciones/registros_XXX.json` con `modo_chatgpt` igual a `inspeccionar`. Abrirá Chrome **sin enviar archivos**. Seleccionar manualmente el GPT **HERO - BPMN Modeling**, comprobar la habilidad **BPMN ARIS XML Repair** y localizar DOM real de los siguientes controles: URL directa del GPT, `input[type=file]`, editor, botón Enviar, contenedor de **nueva** respuesta del asistente, señal de turno completo dentro de ese contenedor y enlace/botón de descarga del archivo. No enviar datos ni cookies al repositorio.

## Modo ejecutar (requiere inspección real previa)

Configurar `hero_gpt_url`, `modo_chatgpt: ejecutar` y selectores `gpt_*` en `config.local.json`. Se usa siempre el prompt:
«Corrige el xml usando y siguiendo las instrucciones de "BPMN ARIS XML Repair". Dame solamente el xml listo para descargar sin nada mas o en caso de no poder solo ponmelo para copiar y pegar.»

Por cada registro, adjunta XML y espera respuesta nueva finalizada. Prefiere archivo descargable `.xml`, o XML pegado en bloque de texto. Si no hay XML válido, pide **una sola vez** «Dame solamente el xml listo para descargar sin nada mas o en caso de no poder solo ponmelo para copiar y pegar.» Si tampoco sale válido, marca `correccion_fallida`. Si hay timeout u otra incertidumbre marca `requiere_revision`, nunca reenvía a ciegas.

El archivo final se guarda en `data/corregidos/id_n5 nombre_proceso - Detalle.xml`, reemplazando caracteres inválidos Windows por guiones. El `name` de `id="LanePrincipal"` se actualiza exactamente con `id_n5 nombre_proceso` sin sanear el texto del proceso. Se valida estructura XML y presencia única del lane. Si falla, no se marca éxito.

El resultado JSON local es `data/exportaciones/procesados_REGISTROS.json`: contiene el registro inicial más `id_archivo_n5` (nombre sin extensión), `xml_corregido_ruta` y `estado=correccion_finalizada`. Se genera checkpoint después de cada registro y los completados se omiten al reanudar. El XML original del JSON no se sobrescribe.

Logs en `data/logs`; no incluyen contenido XML ni prompts. `data/` queda ignorado por Git. No se ha validado la automatización con el DOM de ChatGPT: necesitamos inspeccionarlo antes de habilitar ejecución. ARIS sigue pendiente.

## Perfil único Chrome
Sheets, ChatGPT y ARIS utilizan el perfil persistente original `data/chrome_profile_sheets/`. No se borra ni migra. Cierra otras ventanas automatizadas antes de iniciar una etapa para evitar bloqueos. En inspección ChatGPT abre `hero_gpt_url` si está configurada. Posteriormente podrá externalizarse para otros RDAs.
