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
- Escribe el prompt de corrección con **BPMN ARIS XML Repair** y hace clic en el botón de Enviar (selector anterior del prototipo: `[data-testid="send-button"]`, sujeto a verificación).
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
