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
- Mover la plantilla dentro del lote; para copiarla **N−1** veces se selecciona explícitamente la carpeta `Procesados` desde su carpeta padre como destino (no pegar con `00 Ejemplo` seleccionado). Después, renombrar N ejemplares con los nombres de procesos.
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

## Correcciones posteriores (03/11/2026)

- Chrome se abre con `chromium_sandbox=True` para no desactivar el sandbox ni utilizar `--no-sandbox`. Si las políticas del equipo impiden iniciar Chrome, **detenerse** y revisar la configuración autorizada; no volver a desactivar el sandbox.
- Los diálogos GWT se completan con `press_sequentially()`, que genera eventos de teclado. Se verifica el nombre escrito, la habilitación de `Aceptar` y se hace **un único clic**.
- Después de crear un grupo, ARIS entra en él. El robot guarda su URL, usa la flecha **Subir un nivel** de ARIS para volver a la carpeta padre y comprueba que la nueva carpeta exista.
- Se soporta también la plantilla llamada `Ejemplo`, además de `00 Ejemplo`.

**IMPORTANTE TRAS UNA PRUEBA INTERRUMPIDA:** si ya se creó una carpeta `Procesados ...`, revisa manualmente dónde quedaron `Ejemplo` y las carpetas `- Detalle`. No vuelvas a ejecutar el lote sobre una estructura parcialmente modificada sin comprobarla antes. El robot no reanuda ni deshace operaciones.

Para probar las correcciones de forma controlada:

```powershell
cd rda_subir_a_prod
python -m unittest discover -s tests -v
python app.py
```

Estas pruebas unitarias no sustituyen la comprobación de Chrome ni de los diálogos en la instancia corporativa de ARIS.


## Corrección: pegado de plantillas y selección BPMN (03/11/2026)

- El aviso *"The target group is a subgroup of one the exclusion groups"* aparece si se intenta pegar un grupo dentro de sí mismo o de un descendiente. Cuando queda seleccionada la fila de `00 Ejemplo`, ARIS puede interpretarla como el destino.
- La copia se hace ahora mediante dos ubicaciones: dentro del lote se selecciona la plantilla y se pulsa **Copiar**; después se navega a la carpeta raíz y se selecciona explícitamente **Procesados** (el grupo de destino) antes de pulsar **Pegar** una sola vez. Se regresa al lote y se comprueba que apareció exactamente una carpeta nueva.
- Al mover grupos se limpia cualquier selección anterior del destino antes de pegar. Nunca se usa la casilla de selección global para borrar la selección.
- Si ARIS muestra el aviso de destino inválido, la operación se detiene **sin repetir el pegado**.
- El HTML de modelos BPMN muestra `.itemTitle .titledesc[title]`, igual que las carpetas. Se diferencia por `.itemIcon i[aria-label="model"]`; las carpetas llevan un valor `aria-label` que comienza por `folder`. Se corrigieron `listar()`, `_row()` y el renombrado posterior por tipo `modelo`.
- Pruebas unitarias añadidas para verificar selección explícita del destino, detección de grupo/modelo y ausencia de reintentos tras un rechazo. Estas pruebas **no han sido ejecutadas contra ARIS corporativo**.

Antes de una nueva ejecución real, inspecciona el árbol del lote interrumpido y elimina o recupera manualmente cualquier duplicado de prueba que no necesites. Ejecuta primero la simulación en una estructura **nueva** y luego una ejecución controlada con una sola carpeta `- Detalle` y su plantilla. El robot no reanuda automáticamente operaciones parcialmente completadas.

## Ajuste de navegación tras la copia (03/11/2026)

Se observó que, tras el primer pegado, ARIS quedaba en la carpeta raíz con **Procesados seleccionado**, mientras un cambio directo del hash no terminaba de abrir su contenido. Al hacer doble clic manual en el grupo, el flujo continuaba.

- Se **mantiene** la selección explícita de **Procesados** desde la raíz como destino del pegado, porque evita copiar la plantilla dentro de sí misma.
- Después del único pegado, el robot identifica el grupo que **realmente muestra el encabezado de ARIS**, sin confiar únicamente en la URL.
- Si ARIS está en la raíz, abre **Procesados** mediante el mismo doble clic que ya usa `entrar()`. No intenta navegar cambiando `location.hash` en este punto.
- Si ARIS ya está dentro de Procesados, no hace un segundo clic. Si el nombre o URL no corresponden al lote, se detiene; nunca repite el pegado.
- Se comprueba que apareció **exactamente una** carpeta nueva en el destino, con nombre distinto al original. Los logs distinguen selección de origen, elección del destino, solicitud de pegado, regreso y confirmación de copia.
- Nuevas pruebas de lógica cubren navegación manual equivalente, destino ya abierto, nombre inesperado y URL de otro lote. Estas pruebas no sustituyen una ejecución controlada en la sesión corporativa.

**Siguiente prueba recomendada:** usar dos procesos `- Detalle` y una plantilla, en una carpeta limpia. Revisar en los logs la secuencia `Destino para pegar confirmado` → `Pegado solicitado` → `Volviendo a ... mediante clic` → `Destino confirmado` → `Plantilla copiada`. No reanudar automáticamente un lote interrumpido.

## Contadores, esperas y diagnóstico de copias (03/11/2026)

El **número de carpetas finales** coincide con el número de diagramas. Si hay 3 diagramas, se mueven **1 plantilla original + 2 copias nuevas = 3 estructuras**. No son 3 copias adicionales. Cada nueva copia se comprueba dentro de su propia carpeta hasta ver `00 Procesos` y `01 Mapas y Catálogos`, sin emitir un segundo pegado.

- La validación de URL después de copiar ya no falla solo porque ARIS regenere la ruta del grupo: utiliza el contexto del repositorio, el grupo visible y comprueba el contenido.
- El robot observa la aparición de carpetas y espera a que terminen de aparecer sus subcarpetas. Solo **cuando no hay confirmación dentro del plazo límite** se registra un error; las escrituras no se reintentan.
- El JSON registra `copias.total_esperado`, `copias.originales`, `copias.copias_nuevas_esperadas`, `copias.copias_nuevas_observadas`, `copias.copias_nuevas_validadas`, `copias.estructura_total_validada`, `copias.nombres` e `incidencias`.
- Se añadieron logs `[MAPEO]`, `[PLAN DE COPIAS]`, `[COPIAS]`, `[COPIA][ESPERA]`, `[COPIA][ESTRUCTURA]` y `[RENOMBRADO]`. Cuando la copia falla, también aparece `[COPIAS][DIAGNOSTICO]`: grupo visible, URL, estado de carga, selección visible y alerta.
- La miniinterfaz tiene **Abrir logs** y **Abrir reportes JSON**. Los archivos anteriores permanecen guardados en `%LOCALAPPDATA%\\rda_subir_a_prod\\logs` y `%LOCALAPPDATA%\\rda_subir_a_prod\\resultados`. Los nuevos logs incluyen un nombre único por ejecución.
- Se permite **volver a procesar el mismo N5** en una solicitud posterior siempre que haya una nueva estructura de entrada. Las carpetas `Procesados ...` de ejecuciones anteriores no se consideran diagramas nuevos ni bloquean el mapeo. Un intento de reanudar una ejecución **parcialmente movida** sigue sin ser automático: hay que inspeccionar el árbol antes de repetirla.

**No se ha ejecutado esta versión contra tu sesión corporativa**. Los cambios de esta etapa afectan principalmente el control de copias, diagnósticos y la pantalla de logs; los informes y el renombrado final no se modificaron.

## Flujo 2 · Publicación en producción (03/11/2026)

**Entradas de la misma miniinterfaz**:
- **Link 1**: destino vacío de producción. Debe apuntar exactamente a `04 PERU / 90 OTROS MODELOS / atencion_r`; no se admite otro `atencion_r` con la misma etiqueta pero diferente jerarquía. Se confirma subiendo de nivel por la interfaz.
- **Link 2**: carpeta de origen con los procesos revisados. Ejemplo: `3.3.1.1_P03 Diseño y mantenimiento de segmentos de clientes PN` con `00 Procesos / <nombre> - Detalle / <modelo>` y `01 Mapas y Catálogos / <nombre> / <modelo>`.
- **Link 3**: reservado, no participa en estos flujos.

**Validar flujo 2 (sin cambios)** comprueba que cada grupo tenga un código N5 único, los dos subgrupos obligatorios, un grupo `- Detalle` y sus modelos esperados; comprueba que el destino esté vacío y esté bajo los padres correctos. Todo ello sucede antes de cualquier escritura.

**Ejecutar flujo 2** con `Simulación` desmarcada y confirmación explícita:
1. Crear bajo `atencion_r` un grupo **solo con el código N5** por proceso, verificar cada creación, y validar todos los destinos.
2. Por proceso, seleccionar la carpeta `00 Procesos` del origen; elegir el informe `BBVA - Publicación de modelado de proceso` en formato XML.
3. En la primera ventana, seleccionar el grupo `<nombre> - Detalle` y luego el mapa/catálogo `<nombre>`. Comprobar el resumen antes de `Finalizar`.
4. En la segunda ventana elegir `04 PERU / 90 OTROS MODELOS / atencion_r / <código N5>`. Comprobar la **ruta exacta** y las políticas de conflicto informadas por ARIS, marcar la casilla y pulsar `Finalizar` **una sola vez**.
5. Esperar el **aviso final de ARIS**: si informa que el informe está completo, registrar `ok`; si indica cancelación o error, registrar `fallo_aris`. En ambos casos pulsar **Aceptar una sola vez**, comprobar que se cerró la ventana y continuar al siguiente proceso. Nunca usar solo una fila histórica del panel como confirmación. Si no puede confirmarse la ejecución, registrar `no_confirmado` y detener la publicación para evitar duplicados.

**Advertencia**: en el resumen del asistente proporcionado, ARIS muestra que el **origen sobrescribe el destino** para modelos, objetos y conexiones. Esta política requiere una confirmación explícita antes de la ejecución real; el bot no la modifica.

En `resultados/flujo2_*.json` se guarda cada código N5 con estado de carpeta destino, publicación, errores y resumen. Los logs señalan `[PROD][MAPEO]`, `[PROD][PREFLIGHT]`, `[PROD][CREACION]`, `[PROD][ARBOL]`, `[PROD][ESPERA FINAL]` y `[PROD][RESULTADO FINAL]`.

### Ejecución local

```powershell
git pull origin main
cd rda_subir_a_prod
python -m unittest discover -s tests -v
python app.py
```

Primero probar **Validar flujo 2 (sin cambios)**. El código y los tests offline no equivalen a una validación en ARIS real; no se ha ejecutado el flujo de publicación dentro de la sesión corporativa desde aquí. No reiniciar el lote automáticamente si quedó una publicación indeterminada.

## Corrección de validación del destino en producción (03/11/2026)

**Síntoma observado:** el preflight de los dos procesos terminaba correctamente, el bot registraba `Subido al nivel: 90 OTROS MODELOS` y luego quedaba esperando una cabecera literal `Buscar en: 04 PERU`. Sin embargo, ARIS mostraba `Buscar en: PERU_PRODUCCION` con `04 PERU` seleccionado y la tabla central de hijos (`90 OTROS MODELOS` entre ellos). Era un **falso error de contexto**, no prueba de que fallara SSO.

**Cambio:** la comprobación ahora verifica la cadena por el contenido real después de cada subida: dentro de `90 OTROS MODELOS` debe existir `atencion_r`, y tras subir otro nivel deben aparecer los hijos de `04 PERU`, incluido `90 OTROS MODELOS`. El encabezado desfasado se registra, pero por sí solo no bloquea la prueba. Se observa la tabla reactivamente (hasta 90 segundos si no aparece), sin repetir clics. La validación termina abriendo el Link 1 con una navegación completa, puesto que no hay portapapeles pendiente.

Nuevos logs: `[PROD][DESTINO]`, `[PROD][DESTINO OK]` y `[PROD][DESTINO][ESPERA]`. La interfaz ahora distingue **validación interrumpida sin cambios** de **ejecución real con posibles cambios parciales**. Se añadieron pruebas sin ARIS para el encabezado `PERU_PRODUCCION` y el rechazo de una jerarquía incorrecta.

Esto no modifica el informe de publicación ni los pasos de creación. **Probar primero `Validar flujo 2 (sin cambios)`** y revisar que aparezca `[PROD][DESTINO OK]` seguido de `[PROD][SIMULACION]`, antes de permitir publicación real.

## Corrección del árbol de publicación y continuación controlada (03/11/2026)

**Incidencia real:** tras crear correctamente `7.3.3.12_P01` y `7.3.3.13_P01` en `atencion_r`, el informe se lanzó, pero el asistente `TREE_P0` solo mostraba `00 Procesos` contraído (`aria-expanded="false"`). El robot buscaba de inmediato el grupo `<proceso> - Detalle` en nivel 2 y agotaba el plazo; nunca lo desplegaba.

**Corrección del asistente GWT:**
1. `TREE_P0`: pulsar el **triángulo (IMG)** de `00 Procesos` (nivel 1) solo si está contraído; esperar `aria-expanded="true"`. Hacer clic **en el texto** del grupo exacto `<proceso> - Detalle` (nivel 2) y confirmar `aria-selected="true"`.
2. Esperar reactivamente a que `Siguiente` deje de estar deshabilitado (`aria-disabled` / `disabled`). Clic **una vez**.
3. `TREE_P1`: repetir la operación con el triángulo de `01 Mapas y Catálogos`, seleccionar el grupo `<proceso>` (sin sufijo) y confirmar `Siguiente`.
4. El paso siguiente (resumen de exportación) sigue comprobando las dos selecciones antes de pulsar `Finalizar`. No se cambió la publicación definitiva ni la lógica del aviso final.

Si no se presenta un nodo, los logs `[PROD][ARBOL][ESPERA]` y `[PROD][ARBOL][ERROR]` listan las etiquetas, niveles y atributos GWT visibles. Nunca se repite `Lanzar informe`, `Finalizar` ni un clic de selección ambiguo.

**Continuar después de un lote interrumpido:** las carpetas N5 de producción creadas en la prueba previa NO se deben recrear por defecto. La interfaz muestra una casilla **Flujo 2: usar N5 existentes SOLO si están vacíos**, desmarcada al iniciar. Al marcarla:
- El preflight acepta solamente grupos preexistentes cuyo nombre coincida con un código N5 mapeado en el Link 2; cualquier otro elemento detiene el lote.
- Entra a cada grupo existente y verifica que NO contenga carpetas, modelos ni objetos. Si tiene contenido, se detiene para no sobrescribir producción.
- Solo los N5 existentes y vacíos se reutilizan; se crean únicamente los grupos faltantes. El JSON marca `destino: reutilizado_vacio` y el resumen distingue `destinos_creados` de `destinos_reutilizados`.
- La casilla **no se recuerda al reiniciar**, de modo que un nuevo lote requiere autorizar nuevamente la continuación.

**Prueba recomendada:** comprobar visualmente que los dos N5 anteriores están vacíos; marcar la opción de continuación; ejecutar **Validar flujo 2 (sin cambios)**. Si el preflight confirma ambos, desmarcar `Simulación` y ejecutar con confirmación explícita para completar los asistentes. El informe anterior fue lanzado, pero no completó su selección; revisar el historial antes de repetir cualquier publicación. No se ha probado aún esta versión en ARIS corporativo.

## Ajuste del resumen de exportación y destino con otros lotes (03/11/2026)

Hallazgo confirmado por el log `05:19:22`: el asistente seleccionó correctamente `TREE_P0` y `TREE_P1`, pero `TEXTO_RESUMEN_P2` contenía **AMBAS** secciones (`Grupos de escenario` y `Grupos y Modelos de Catálogo`). `TEXTO_PROC_MOD_P2` estaba vacío. La validación antigua exigía un mapa en ese segundo campo, lanzó `ArisError` antes de `Finalizar`, y el contexto `with Aris(...)` cerró Chrome al terminar. **No se había pulsado el primer Finalizar.**

Corrección: leer el único `TEXTO_RESUMEN_P2`, extraer por separado los nombres de grupo `[G]` bajo cada encabezado y comparar **nombres completos exactos** con el grupo `- Detalle` y el grupo de mapas sin sufijo. Solo después de validar pulsar `Finalizar` de exportación una vez y esperar `TREE_P2`, el asistente del destino. El segundo `Finalizar` sigue siendo el de publicación definitiva. Los logs separan `[PROD][RESUMEN EXPORT]`, `[PROD][EXPORTACION]`, `[PROD][DESTINO]` y `[PROD][PUBLICACION]`.

Se revisaron además las capturas del cierre del informe: después del segundo `Finalizar` puede aparecer **Importación de escenario** con el mensaje de incorporación en `04 PERU\\90 OTROS MODELOS\\atencion_r\\<N5>`; comprobar ruta y pulsar **Aceptar intermedio** una vez, y solo después esperar el aviso final de **BBVA - Publicación de modelado de proceso** y su **Aceptar final**. Si la ruta no coincide, no se acepta. No reintentar el informe.

**Nuevo comportamiento de Link 1**: `atencion_r` puede contener cualquier cantidad de carpetas de otras solicitudes. El robot las deja intactas. Mapea únicamente los códigos que aparecen en Link 2:
- Si falta un código N5, crea su carpeta.
- Si existe un grupo con el mismo código, exige la casilla de continuación y verifica que el grupo esté completamente vacío antes de reutilizarlo.
- Si existe un objeto o modelo con el mismo código, o una carpeta N5 del lote con contenido, se detiene para no provocar colisiones o sobrescrituras.

La casilla de continuación **no está limitada a los dos códigos de la prueba** y no impide crear nuevos códigos. Los logs muestran `[PROD][DESTINO][INVENTARIO]`, N5 existentes, N5 por crear y otros elementos conservados.

Se añadieron pruebas unitarias del resumen, carpetas ajenas, mezcla de grupos existentes y faltantes, y ambas confirmaciones de la publicación. **No se han ejecutado las pruebas ni el informe real desde aquí**: probar localmente con `python -m unittest discover -s tests -v`, luego validar el flujo y observar los pasos.

## Corrección: normalización de carpetas importadas desde el modelo (04/11/2026)

**Alcance: solo flujo 1; el flujo 2 sigue validando coincidencia exacta.**

Los archivos XML de origen pueden tener nombres adaptados a Windows porque `:`, `/` o `\\` no son válidos en nombres de archivo. ARIS importa el modelo con su título real, pero el grupo creado con el nombre del archivo puede terminar distinto. Para que el flujo 2 reciba nombres alineados, el flujo 1 ahora realiza una revisión inicial:

1. Tras mapear las carpetas con sufijo `- Detalle` y validar `00 Ejemplo`, entra a **cada grupo importado** y lee el nombre del **único modelo** que debe contener.
2. Calcula el nombre canónico del grupo como `<nombre exacto del modelo> - Detalle`. No altera el modelo ni sustituye signos por guiones. Ejemplo: `7.3.3.1_P02 Protección de datos- Gestión de bloqueos DLP - Detalle` → `7.3.3.1_P02 Protección de datos: Gestión de bloqueos DLP - Detalle`.
3. Comprueba **todos** los modelos antes de escribir nada. Si falta el modelo, hay más de uno, se repite un nombre de salida o ya existe un grupo con el destino previsto, interrumpe sin renombrar por suposición.
4. En **Simulación** reporta los cambios planeados y no renombra. En ejecución real, renombra **una sola vez** cada grupo que difiere. Si no confirma el renombrado, detiene el flujo sin reintentar.
5. Utiliza el nombre corregido del grupo y el título real del modelo para los pasos **ya existentes**: copias, movimiento, generación de mapas y renombrado de resultados.

Logs: `[NORMALIZACION][INSPECCION]`, `[NORMALIZACION][MODELO]`, `[NORMALIZACION][PLAN]`, `[NORMALIZACION][SIMULACION]`, `[NORMALIZACION][RENOMBRAR]`, `[NORMALIZACION][OK]`, `[NORMALIZACION][DETENIDO]`. El JSON conserva `carpeta_inicial`, `modelo_origen` y `normalizacion`. Los tests offline están en `tests/test_normalizacion.py`.

**Limitación:** si ARIS no permite crear una carpeta con algún carácter del nombre real del modelo, el bot debe detenerse y reportarlo. No modifica ni simplifica el título del modelo para forzar el cambio. No se ha probado este caso de caracteres especiales en ARIS real.

