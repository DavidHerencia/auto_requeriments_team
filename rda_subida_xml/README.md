# RDA subida XML — base de integración ARIS

Implementación inicial intencionalmente enfocada en **un XML**. El pipeline puede iterarse después. Los adaptadores de Sheets y ChatGPT son contratos pendientes, no funcionalidades fingidas.

## Primer uso en Windows

1. Tener Python y Google Chrome instalados.
2. Ejecutar `py -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements.txt` y `.venv\Scripts\python -m playwright install chrome` en `rda_subida_xml` (o usar `iniciar.bat` para instalar y ver ayuda).
3. Copiar `config.example.json` a `config.local.json` y configurar `aris_url` con la URL corporativa de ARIS. No compartir este archivo.
4. Ejecutar `.venv\Scripts\python app.py --xml examples\ejemplo_sintetico.xml`.
5. Iniciar sesión manualmente en Chrome cuando se abra; navegar hasta la pantalla de importación. En modo `inspeccionar` **no se importa ningún archivo**.
6. Documentar selectores reales y condiciones de confirmación **sin copiar cookies, contraseñas ni datos reales al repositorio**.

## Importación real (después de inspeccionar)

Configurar `modo_aris: importar` y los cuatro selectores. El programa requiere una confirmación explícita antes de clicar Importar. Una ausencia de confirmación genera `requiere_revision` para evitar reintentos ciegos. La confirmación visual es provisional: se debe validar contra el comportamiento real de ARIS.

## Carpeta local

`data/` (ignorada por Git) contiene XML de trabajo, perfil Chrome y reportes. El código nunca exporta cookies. Para un segundo intento, usar un nombre nuevo o limpiar exclusivamente datos sintéticos de prueba; no sobrescribe archivos.

## Advertencias

- La estructura exacta XML compatible con ARIS está **por verificar**: el ejemplo solo es XML bien formado, no un XML importable garantizado.
- `sheets.py` y `chatgpt.py` son interfaces pendientes; no hay conexión real todavía.
- La generación del mapa de roles, organización definitiva y persistencia robusta son etapas siguientes.
- Solo usar con cuentas y datos autorizados por la organización.
