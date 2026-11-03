# RDA subir a prod — ARIS

Proyecto independiente dentro de `auto_requeriments_team`. Automatiza **solo ARIS** en Chrome por Playwright; no procesa XML ni utiliza ChatGPT, Sheets o Apps Script.

## Estado

Primera implementación del **flujo 1** basada en capturas y DOM del 03/11/2026. **No se ha validado end-to-end contra ARIS corporativo.** Probar primero con carpetas temporales y un solo proceso. No ejecutar contra carpetas de producción sin verificar cada transición.

## Instalar y abrir

Windows corporativo (sin admin):

1. Instalar Python y Chrome (ya existentes si se reutiliza el entorno).
2. Doble clic en `instalar.bat` (crea `.venv` e instala Playwright).
3. Doble clic en `iniciar.bat`.
4. Introducir **link 1** directo a la carpeta de importados en ARIS y pulsar **Validar sesión y mapear**. Si se solicita SSO, completar el inicio de sesión en Chrome visible.
5. Revisar la lista y el reporte JSON en `%LOCALAPPDATA%\rda_subir_a_prod\resultados`.
6. Desmarcar **Simulación** y ejecutar **Flujo 1** para cambios reales, solo después de validar en un repositorio de prueba.

**No abrir el mismo perfil Chrome** en dos instancias del bot. Perfil exclusivo: `%LOCALAPPDATA%\rda_subir_a_prod\chrome_profile`. No se capturan contraseñas.

## Flujo 1

Entrada: URL de la carpeta que contiene **una plantilla** `00 Ejemplo` (o `Ejemplo`) y las carpetas de diagramas cuyos nombres acaban en `- Detalle` (admite `-Detalle`).

- Mapear todos los grupos de procesos; eliminar solo el sufijo final para derivar el nombre.
- Verificar plantilla con `00 Procesos` y `01 Mapas y Catálogos`.
- Crear en la raíz `Procesados DD-MM-AAAA HH-MM-SS`.
- Mover plantilla dentro del lote, copiarla **N−1** veces; renombrar N ejemplares con los nombres de procesos.
- Mover cada carpeta original con sufijo `- Detalle` hacia `Procesados ... / <proceso> / 00 Procesos`.
- Seleccionar esa carpeta movida y ejecutar `BBVA - Generación automática de mapas` en TXT. Un solo lanzamiento por proceso, sin reintentos.
- Tras completar los informes, reabrir link 1 en página nueva; para cada informe exitoso, renombrar grupo `25 Mapa de Roles` y su modelo interno a `<proceso>` en `01 Mapas y Catálogos`.
- Emitir JSON con estado, etapas y errores por proceso.

Estructura esperada:

```text
<raíz importados>/
  Procesados DD-MM-AAAA HH-MM-SS/
    <proceso A>/
      00 Procesos/
        <proceso A> - Detalle/
      01 Mapas y Catálogos/
        <proceso A>/
          <modelo: proceso A>
    <proceso B>/
      ...
```

Si ARIS responde **fallo**, registrar y **no reintentar** el informe ni renombrar el mapa de ese proceso. Si una operación de mover/copiar/renombrar no se confirma, **interrumpir la fase afectada sin repetir la escritura**; nunca adivinar la situación de carpetas. El reporte puede indicar `interrumpido` con operaciones ya aplicadas: **no relanzar el mismo lote** sin inspección manual.

## Límites del primer prototipo

- El DOM aportado describe elementos y barras de herramientas, pero **no incluye diálogos abiertos de crear/renombrar ni la semántica verificada de portapapeles entre grupos**. Se requerirán pruebas reales para afinar selectores.
- Las listas ARIS se virtualizan y pueden cambiar; el lector implementa desplazamiento, pero no asume cobertura ilimitada.
- La reanudación automática está deshabilitada por seguridad: tampoco hay reintentos implícitos.
- El enlace se valida a un host `*.igrupobbva` y ruta `#default/repository/...`.
- **Link 2 y link 3** aparecen en la interfaz como reserva; **flujo 2 no está implementado** y su botón está deshabilitado.
- Los logs y reportes se guardan localmente; evita distribuirlos si contienen nombres corporativos.

## Archivos

`app.py` (interfaz), `aris.py` (interacciones Playwright), `workflow.py` (orquestador), `tests/test_logic.py` (pruebas sin ARIS).

Pruebas: `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
