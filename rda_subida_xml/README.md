# RDA XML: extracción y corrección

## Hoja
Crea una pestaña llamada **EXPORT_RDA** con las cuatro columnas exactas:
`id_solicitud`, `id_n5`, `nombre_proceso`, `xml`. Identificadores como texto.

## Apps Script
En el Sheet: Extensiones > Apps Script. Copia `apps_script/Code.gs` y agrega archivo HTML llamado **Bridge** con `apps_script/Bridge.html`. Despliega aplicación web ejecutada como **usuario que accede**, solo usuarios autorizados. Copia URL `/exec` en `config.local.json` como `apps_script_url`. El puente solo lee.

## Instalar
```powershell
cd rda_subida_xml
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chrome
copy config.example.json config.local.json
.venv\Scripts\python app.py --fase download
```

Al autenticar Chrome y presionar Enter, se genera `data/exportaciones/registros_*.json` con `version` y `registros`, cada registro tiene exactamente cuatro campos.

## Corrección / inspección
```powershell
.venv\Scripts\python app.py --fase chatgpt --archivo data\exportaciones\registros_XXX.json
```
Inicialmente `modo_chatgpt=inspeccionar`: se abre ChatGPT sin enviar nada. **Solo 1 registro** en esta fase. Para automatizar: inspeccionar juntos selector de adjuntar, editor, enviar, nueva respuesta y señal de turno completado. Configurar selectores reales y `modo_chatgpt=ejecutar`. Se adjunta XML y se escribe `@skill_revisor_y_corregidor_xml`, cuya activación efectiva debe comprobarse manualmente. No asumimos que el texto equivale a seleccionar la skill.

Si se confirma respuesta XML única y válida, se guarda en `data/corregidos/<id_n5 nombre_proceso>.xml`, reemplazando caracteres inválidos de Windows por `-`, y se crea JSON derivado con `id_archivo_n5`, `estado=correccion_finalizada` y ruta del XML. No altera JSON original. No reintentar envío si el estado del mensaje es incierto.

**No versionar datos**, perfiles, XML reales, credenciales. Probar un registro autorizado primero; Apps Script tiene cuotas/límites y los 100 se evaluarán después. ARIS y subida de resultados quedan pendientes.
