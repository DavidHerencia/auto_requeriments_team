"""Asistente de publicación de ARIS, observado en capturas del 03/11/2026.

No usa APIs privadas. Los pasos se reconocen por data-testid y contenido visible.
Ningún clic de Finalizar se repite cuando el resultado es incierto.
"""
from __future__ import annotations

import re
import time

from playwright.sync_api import TimeoutError as PWTimeout

from aris import ArisError, NoConfirmado, PROCESOS, MAPAS, REPORT_PANEL

PUBLICACION = "BBVA - Publicación de modelado de proceso"
FORMATO_XML = "Archivo XML (*.xml)"
DESTINO_WIZARD = ("04 PERU", "90 OTROS MODELOS", "atencion_r")
SCRIPT_DIALOG = '[data-testid="SCRIPT_DIALOG"]:visible'


class Publicador:
    def __init__(self, aris, log):
        self.aris = aris
        self.page = aris.page
        self.log = log
        self.timeout = aris.timeout

    def _dialogo(self):
        d = self.page.locator(SCRIPT_DIALOG)
        if d.count() != 1:
            raise NoConfirmado(
                f"Asistente de publicación no es único ({d.count()}). "
                "No se repetirá ninguna acción."
            )
        return d

    def _esperar_paso(self, tree_testid: str, timeout: int = 180000):
        try:
            self.page.locator(
                SCRIPT_DIALOG + f' [data-testid="{tree_testid}"]'
            ).wait_for(state="visible", timeout=timeout)
        except PWTimeout as exc:
            raise NoConfirmado(
                f"No apareció la etapa {tree_testid} del asistente. "
                "Comprobar el estado en ARIS; no se vuelve a lanzar el informe."
            ) from exc
        self.log(f"[PROD][ASISTENTE] Paso confirmado: {tree_testid}")

    def _nodo(self, tree_id: str, name: str, level: int | None = None):
        tree = self._dialogo().locator(f'[data-testid="{tree_id}"]')
        if tree.count() != 1:
            raise ArisError(f"Árbol {tree_id} no disponible de forma única")
        encontrados = []
        for node in tree.locator('[role="treeitem"]').all():
            if level is not None and node.get_attribute("aria-level") != str(level):
                continue
            label = node.locator(".cellTreeItemValue > span[title]").first
            if label.count() == 1 and label.get_attribute("title") == name:
                encontrados.append(node)
        if len(encontrados) != 1:
            raise ArisError(
                f"{tree_id}: esperado un nodo {name!r} en nivel {level}; "
                f"encontrados={len(encontrados)}"
            )
        return encontrados[0]

    def _vista_arbol(self, tree_id: str) -> list[dict]:
        """Diagnóstico de SOLO LECTURA de los nodos GWT del asistente."""
        try:
            tree = self._dialogo().locator(f'[data-testid="{tree_id}"]')
            if tree.count() != 1:
                return [{"error": f"Árbol visible: {tree.count()} coincidencias"}]
            result = []
            for node in tree.locator('[role="treeitem"]').all():
                label = node.locator(".cellTreeItemValue > span[title]").first
                result.append({
                    "nivel": node.get_attribute("aria-level"),
                    "nombre": label.get_attribute("title") if label.count() else None,
                    "expandido": node.get_attribute("aria-expanded"),
                    "seleccionado": node.get_attribute("aria-selected"),
                })
            return result[:40]
        except Exception as exc:
            return [{"error": f"{type(exc).__name__}: {exc}"}]

    def _esperar_nodo(self, tree_id: str, name: str, level: int,
                      timeout_seconds: int = 60):
        deadline = time.monotonic() + timeout_seconds
        next_log = time.monotonic() + 8
        last_error = ""
        while time.monotonic() < deadline:
            try:
                return self._nodo(tree_id, name, level)
            except ArisError as exc:
                last_error = str(exc)
                if time.monotonic() >= next_log:
                    self.log(
                        f"[PROD][ARBOL][ESPERA] {tree_id}: buscando {name!r} "
                        f"nivel={level}; presentes={self._vista_arbol(tree_id)!r}"
                    )
                    next_log = time.monotonic() + 8
                self.page.wait_for_timeout(350)
        snapshot = self._vista_arbol(tree_id)
        self.log(
            f"[PROD][ARBOL][ERROR] {tree_id}: grupo {name!r} no visible; "
            f"ultima_error={last_error}; nodos={snapshot!r}"
        )
        raise NoConfirmado(
            f"Árbol {tree_id}: no se mostró {name!r} en nivel {level}; "
            f"nodos visibles={snapshot!r}. No se repetirá el informe."
        )

    def _expandir(self, tree_id: str, name: str, level: int):
        nodo = self._esperar_nodo(tree_id, name, level)
        state = nodo.get_attribute("aria-expanded")
        if state == "true":
            self.log(
                f"[PROD][ARBOL] {tree_id}: {name!r} ya desplegado "
                "(no se vuelve a pulsar el triángulo)"
            )
            return
        if state != "false":
            raise ArisError(
                f"{tree_id}: no se puede desplegar {name!r}; "
                f"aria-expanded={state!r}"
            )
        # En GWT el triángulo es un IMG bajo .cellTreeItemImage.
        # El nombre (.cellTreeItemValue) SOLO selecciona: no abre el árbol.
        toggle = nodo.locator(".cellTreeItemImage img").first
        if toggle.count() != 1:
            raise ArisError(
                f"{tree_id}: no se encontró el triángulo para {name!r}; "
                "no se hará clic sobre el nombre"
            )
        self.log(
            f"[PROD][ARBOL] {tree_id}: pulsando triángulo de {name!r} "
            f"(nivel {level}) una vez"
        )
        toggle.click()
        try:
            self.page.wait_for_function(
                """args => {
                    const tree = document.querySelector(
                        '[data-testid="' + args.tree + '"]');
                    if (!tree) return false;
                    return Array.from(tree.querySelectorAll('[role="treeitem"]'))
                        .some(n => n.getAttribute('aria-level') === String(args.level)
                            && n.getAttribute('aria-expanded') === 'true'
                            && n.querySelector('.cellTreeItemValue > span[title]')
                                ?.getAttribute('title') === args.name);
                }""",
                arg={"tree": tree_id, "name": name, "level": level},
                timeout=60000
            )
        except PWTimeout as exc:
            raise ArisError(f"No se expandió {name!r} en {tree_id}") from exc
        self.log(
            f"[PROD][ARBOL] {tree_id}: {name!r} desplegado y confirmado "
            "(aria-expanded=true)"
        )

    def _seleccionar(self, tree_id: str, name: str, level: int):
        self.log(
            f"[PROD][ARBOL] {tree_id}: esperando grupo {name!r} "
            f"(nivel {level})"
        )
        node = self._esperar_nodo(tree_id, name, level)
        label = node.locator(".cellTreeItemValue > span[title]").first
        if label.count() != 1 or label.get_attribute("title") != name:
            raise ArisError(
                f"{tree_id}: etiqueta del grupo {name!r} no coincide"
            )
        # Clic en el texto para seleccionar, NUNCA en el triángulo.
        label.click()
        try:
            self.page.wait_for_function(
                """args => {
                    const tree = document.querySelector(
                        '[data-testid="' + args.tree + '"]');
                    if (!tree) return false;
                    return Array.from(tree.querySelectorAll('[role="treeitem"]'))
                        .some(n => n.getAttribute('aria-level') === String(args.level)
                            && n.getAttribute('aria-selected') === 'true'
                            && n.querySelector('.cellTreeItemValue > span[title]')
                                ?.getAttribute('title') === args.name);
                }""",
                arg={"tree": tree_id, "name": name, "level": level},
                timeout=30000
            )
        except PWTimeout as exc:
            raise ArisError(f"No quedó seleccionado {name!r} en {tree_id}") from exc
        self.log(
            f"[PROD][ARBOL] {tree_id}: grupo seleccionado "
            f"y confirmado (aria-selected=true): {name!r}"
        )

    def _boton(self, etiqueta: str):
        dialog = self._dialogo()
        if etiqueta == "Siguiente":
            boton = dialog.locator('[data-testid="BTN_NEXT"]:visible')
        else:
            boton = dialog.get_by_role("button", name=etiqueta, exact=True)
        if boton.count() != 1:
            raise ArisError(f"Botón {etiqueta!r} no es único ({boton.count()})")
        if (boton.get_attribute("aria-disabled") == "true" or
                "disabled" in (boton.get_attribute("class") or "").split()):
            raise ArisError(f"El botón {etiqueta!r} está deshabilitado")
        return boton

    def _next(self, etapa: str):
        """Esperar que ARIS habilite Siguiente DESPUÉS de seleccionar el grupo.

        No se hace clic en Siguiente mientras exista error de selección.
        Se consulta el estado del botón de forma reactiva, sin repetir
        ninguno de los clics previos sobre el árbol.
        """
        self.log(
            f"[PROD][ASISTENTE] {etapa}: esperando habilitación de Siguiente"
        )
        try:
            self.page.wait_for_function(
                """() => {
                    const d = Array.from(
                        document.querySelectorAll('[data-testid="SCRIPT_DIALOG"]')
                    ).find(el => el.getClientRects().length > 0);
                    const a = d?.querySelector('[data-testid="BTN_NEXT"]');
                    return !!a && a.getClientRects().length > 0
                        && a.getAttribute('aria-disabled') !== 'true'
                        && !a.hasAttribute('disabled')
                        && !a.classList.contains('disabled');
                }""",
                timeout=60000,
            )
        except PWTimeout as exc:
            raise NoConfirmado(
                f"{etapa}: ARIS no habilitó Siguiente tras seleccionar "
                "el grupo. Comprobar el mensaje del asistente; "
                "no se volverá a seleccionar ni a lanzar el informe."
            ) from exc
        self.log(f"[PROD][ASISTENTE] {etapa}: Siguiente habilitado")
        self._boton("Siguiente").click()
        self.log(f"[PROD][ASISTENTE] {etapa}: Siguiente pulsado una vez")

    def _seleccionar_informe(self):
        panel = self.page.locator(REPORT_PANEL)
        if not panel.is_visible():
            self.page.locator(
                'li[data-testid="REPORT_REPOSITORY_BUTTON"] a[aria-label="Informes"]'
            ).click()
        panel.wait_for(state="visible", timeout=self.timeout)
        try:
            self.page.wait_for_function(
                """() => document.querySelector(
                    '.sidebar.reportSidebar .reportContextPanel .label')
                    ?.textContent.trim() === '00 Procesos'""",
                timeout=60000
            )
        except PWTimeout as exc:
            raise ArisError("El informe no apunta a la carpeta 00 Procesos") from exc
        combo = panel.locator('.tcReportSelectReportDropDown input[role="combobox"]')
        panel.locator(
            '.tcReportSelectReportDropDown a[aria-label="dropdown-toggle"]'
        ).click()
        options = []
        for el in self.page.get_by_text(PUBLICACION, exact=True).all():
            if el.is_visible() and el.evaluate(
                """el => !el.closest(
                    '.reportPanel-reports-list, .scrollable-report-panel, '
                    + '.finished-report-item-row') && el.children.length === 0"""
            ):
                options.append(el)
        if len(options) != 1:
            raise ArisError(
                f"Informe {PUBLICACION!r} ambiguo o inexistente: {len(options)}"
            )
        options[0].click()
        if combo.input_value().strip() != PUBLICACION:
            raise ArisError("ARIS no seleccionó el informe de publicación")
        fmt = panel.locator('.tcReportSelectFormatDropDown input')
        if "Archivo XML" not in fmt.input_value():
            panel.locator(
                '.tcReportSelectFormatDropDown a[aria-label="dropdown-toggle"]'
            ).click()
            formats = [
                el for el in self.page.get_by_text(FORMATO_XML, exact=True).all()
                if el.is_visible()
            ]
            if len(formats) != 1:
                raise ArisError("No hay una opción XML única para publicación")
            formats[0].click()
        if "Archivo XML" not in fmt.input_value():
            raise ArisError("El formato XML no quedó seleccionado")
        self.log("[PROD][INFORME] Informe de publicación y formato XML confirmados")

    def _escenario(self, detalle: str):
        self._esperar_paso("TREE_P0")
        # El grupo - Detalle no aparece hasta pulsar el TRIÁNGULO de
        # "00 Procesos"; el clic en su nombre no despliega el árbol GWT.
        self._expandir("TREE_P0", PROCESOS, 1)
        self._seleccionar("TREE_P0", detalle, 2)
        self._next("Escenario / 00 Procesos")
        self.log(f"[PROD][ASISTENTE] Escenario confirmado: {detalle!r}")

    def _mapa(self, nombre: str):
        self._esperar_paso("TREE_P1")
        self._expandir("TREE_P1", MAPAS, 1)
        self._seleccionar("TREE_P1", nombre, 2)
        self._next("Mapa / 01 Mapas y Catálogos")
        self.log(f"[PROD][ASISTENTE] Mapa confirmado: {nombre!r}")

    def _exportar(self, detalle: str, base: str):
        d = self._dialogo()
        text = d.inner_text()
        if "Resumen de datos a exportar" not in text:
            raise ArisError("El resumen de exportación no está disponible")
        # El TEXTAREA TEXTO_RESUMEN_P2 de ARIS contiene AMBAS secciones:
        # "Grupos de escenario" y "Grupos y Modelos de Catálogo".
        # TEXTO_PROC_MOD_P2 puede existir pero estar vacío: NO es error.
        # Se cotejan nombres completos por sección, no subcadenas
        # (base también aparece dentro del nombre "<base> - Detalle").
        resumen = d.locator('[data-testid="TEXTO_RESUMEN_P2"]')
        if resumen.count() != 1:
            raise ArisError(
                "No hay un resumen único de exportación (TEXTO_RESUMEN_P2)"
            )
        resumen_valor = resumen.input_value()
        escenario_match = re.search(
            r"Grupos de escenario:\s*\[G\]\s*([^\r\n]+)",
            resumen_valor, flags=re.I
        )
        catalogo_match = re.search(
            r"Grupos y Modelos de Cat[aá]logo:\s*\[G\]\s*([^\r\n]+)",
            resumen_valor, flags=re.I
        )
        seleccionado_detalle = (
            escenario_match.group(1).strip() if escenario_match else None
        )
        seleccionado_mapa = (
            catalogo_match.group(1).strip() if catalogo_match else None
        )
        self.log(
            f"[PROD][RESUMEN EXPORT] escenario={seleccionado_detalle!r}; "
            f"catalogo={seleccionado_mapa!r}"
        )
        if seleccionado_detalle != detalle or seleccionado_mapa != base:
            raise ArisError(
                "El resumen de exportación no coincide con el proceso. "
                f"Esperado: escenario={detalle!r}, catálogo={base!r}. "
                f"Mostrado: escenario={seleccionado_detalle!r}, "
                f"catálogo={seleccionado_mapa!r}. "
                f"Texto={resumen_valor[:450]!r}. "
                "No se pulsó Finalizar."
            )
        self.log("[PROD][ASISTENTE] Resumen de exportación inspeccionado")
        self.log(
            "[PROD][EXPORTACION] Resumen válido; pulsando Finalizar "
            "de exportación (NO es aún la publicación definitiva)"
        )
        self._boton("Finalizar").click()  # un solo clic
        self.log(
            "[PROD][EXPORTACION] Finalizar de exportación pulsado una vez; "
            "esperando asistente para seleccionar destino de producción"
        )
        self._esperar_paso("TREE_P2", timeout=360000)
        self.log(
            "[PROD][EXPORTACION OK] Asistente de destino disponible; "
            "todavía NO se ha pulsado el Finalizar definitivo"
        )

    def _destino(self, codigo: str):
        for nivel, carpeta in enumerate(DESTINO_WIZARD, start=1):
            self._expandir("TREE_P2", carpeta, nivel)
        self._seleccionar("TREE_P2", codigo, 4)
        self._next("Destino de importación")
        summary = self._dialogo()
        path = summary.locator('[data-testid="TEXT_PATH_CONFIRM"]')
        if path.count() != 1:
            raise ArisError("Resumen de importación sin ruta verificable")
        got = path.inner_text().strip()
        wanted = "\\".join((*DESTINO_WIZARD, codigo))
        if got != wanted:
            raise ArisError(
                f"Ruta de producción distinta: {got!r} != {wanted!r}"
            )
        conflict = summary.locator('[data-testid="TEXT_CONFLICT_LIST"]')
        opts = summary.locator('[data-testid="TEXT_MERGE_OPTIONS_LIST"]')
        if conflict.count() != 1 or opts.count() != 1:
            raise ArisError("No se pudo revisar la resolución de conflictos")
        # El comportamiento documentado por las capturas tiene overwrite.
        text = conflict.inner_text().lower()
        if not all(word in text for word in
                   ("modelo:", "objeto:", "conexión:", "sobreescribe destino")):
            raise ArisError("Cambió la política de conflictos del informe")
        self.log(f"[PROD][DESTINO] Ruta exacta y conflictos verificados: {got}")
        self.log(f"[PROD][DESTINO] Opciones: {opts.inner_text().strip()}")
        check = summary.locator('label[data-testid="CHECK_CONFIRM"]')
        if check.count() != 1:
            raise ArisError("Casilla de confirmación de importación no es única")
        checked = summary.locator('#gwt-debug-CHECK_CONFIRM-input')
        if (checked.get_attribute("is-checked") != "checked" and
                "checked" not in (check.get_attribute("class") or "").split()):
            check.click()
        if (checked.get_attribute("is-checked") != "checked" and
                "checked" not in (check.get_attribute("class") or "").split()):
            raise ArisError("No se confirmó la casilla de importación")
        self._boton("Finalizar").click()  # publicación definitiva, SIN retry
        self.log(f"[PROD][PUBLICACION] Enviada una vez al destino {got}")

    def _resultado(self, baseline: int, codigo: str | None = None) -> bool:
        """Confirmar la importación intermedia y el aviso terminal de ARIS.

        El historial en la barra lateral puede incluir ejecuciones anteriores
        o actualizarse antes que el diálogo: nunca se toma como confirmación
        suficiente para abandonar esta etapa ni para publicar otra vez.
        """
        deadline = time.monotonic() + 600
        next_log = 0
        aviso_historial = None
        importacion_aceptada = False
        exitos = re.compile(
            r"el informe est[aá] completo|el informe ha concluido|"
            r"el informe se ha completado|la generaci[oó]n del informe ha concluido",
            re.I
        )
        fallos = re.compile(
            r"cancelad[oa]s?|ha fallado|ha sido fallid[oa]|"
            r"fall[oó]|con errores|se ha producido un error|"
            r"ha ocurrido un error|no se pudo|no se ha podido",
            re.I
        )
        while time.monotonic() < deadline:
            dialogs = self.page.locator('div[role="dialog"]:visible')
            if dialogs.count() > 1:
                raise NoConfirmado(
                    "Aparecieron varios diálogos de ARIS después de publicar. "
                    "No se pulsará Aceptar a ciegas."
                )
            if dialogs.count() == 1:
                d = dialogs.first
                content = d.inner_text().strip()
                # No aceptar por accidente el asistente de selección ni la
                # ventana de progreso (que puede tener botón Cancelar).
                # Un aviso de "completado con errores" es fallo, aunque
                # también contenga la palabra "completado".
                status = (
                    "fallo_aris" if fallos.search(content)
                    else "ok" if exitos.search(content)
                    else None
                )
                # ARIS muestra primero "Importación de escenario" con
                # "La información se ha incorporado en la carpeta ...".
                # Ese Aceptar es INTERMEDIO: no equivale al informe completo.
                # Verificar la ruta N5 antes de cerrar el aviso, sin repetir
                # publicaciones ni hacer clic sobre diálogos desconocidos.
                es_importacion = (
                    "Importación de escenario" in content
                    and "La información se ha incorporado en la carpeta" in content
                )
                if es_importacion:
                    destino = "\\".join((*DESTINO_WIZARD, codigo)) if codigo else None
                    if importacion_aceptada:
                        raise NoConfirmado(
                            "ARIS volvió a mostrar el mismo aviso de importación "
                            "después de Aceptar; no se repetirá el clic."
                        )
                    if not destino or destino not in content.replace("\\n", ""):
                        raise NoConfirmado(
                            "El aviso de importación no confirma la ruta N5 "
                            f"esperada={destino!r}; recibido={content[:500]!r}. "
                            "No se pulsó Aceptar."
                        )
                    aceptar_import = d.get_by_role(
                        "button", name="Aceptar", exact=True
                    )
                    if aceptar_import.count() != 1 or (
                        aceptar_import.get_attribute("aria-disabled") == "true"
                        or aceptar_import.get_attribute("disabled") is not None
                    ):
                        raise NoConfirmado(
                            "El aviso de importación no tiene un Aceptar habilitado "
                            "y único. No se repetirá el informe."
                        )
                    self.log(
                        f"[PROD][IMPORTACION OK] ARIS confirmó incorporación "
                        f"en {destino!r}; pulsando Aceptar intermedio una vez"
                    )
                    aceptar_import.click()
                    importacion_aceptada = True
                    try:
                        self.page.wait_for_function(
                            """() => !Array.from(
                                document.querySelectorAll('div[role="dialog"]')
                            ).some(el => el.getClientRects().length > 0
                                && el.textContent.includes('Importación de escenario')
                                && el.textContent.includes(
                                    'La información se ha incorporado en la carpeta'))""",
                            timeout=45000
                        )
                    except PWTimeout as exc:
                        raise NoConfirmado(
                            "Se pulsó Aceptar de importación pero el aviso no "
                            "desapareció. No se repetirá el clic."
                        ) from exc
                    self.log(
                        "[PROD][IMPORTACION] Aviso intermedio cerrado; "
                        "esperando confirmación final del informe"
                    )
                    continue
                if status:
                    aceptar = d.get_by_role("button", name="Aceptar", exact=True)
                    if aceptar.count() != 1:
                        raise NoConfirmado(
                            f"ARIS mostró resultado {status} pero no un Aceptar "
                            "único. No se repetirá la publicación."
                        )
                    if (aceptar.get_attribute("aria-disabled") == "true" or
                            aceptar.get_attribute("disabled") is not None):
                        raise NoConfirmado(
                            "El botón Aceptar del resultado está deshabilitado."
                        )
                    self.log(
                        f"[PROD][RESULTADO] Resultado explícito de ARIS: "
                        f"{status}; mensaje={content[:500]!r}"
                    )
                    aceptar.click()  # único Aceptar: cerrar el resultado final
                    self.log("[PROD][RESULTADO] Aceptar final pulsado una sola vez")
                    try:
                        d.wait_for(state="hidden", timeout=30000)
                    except PWTimeout as exc:
                        raise NoConfirmado(
                            "Se pulsó Aceptar, pero no se confirmó el cierre "
                            "de la ventana final. No se repetirá el clic."
                        ) from exc
                    self.log(
                        f"[PROD][RESULTADO FINAL] Diálogo cerrado. "
                        f"publicación={status}. Sin reintentos."
                    )
                    return status == "ok"
                # Si aparece Aceptar con un texto no documentado, detenerse
                # inmediatamente para que el usuario pueda inspeccionarlo.
                # No esperar 10 minutos ni aceptar un aviso desconocido.
                otro_aceptar = d.get_by_role(
                    "button", name="Aceptar", exact=True
                )
                if otro_aceptar.count() > 0:
                    raise NoConfirmado(
                        "ARIS mostró un diálogo con Aceptar, pero el "
                        f"resultado no se reconoció: {content[:450]!r}. "
                        "No se cerró el aviso ni se repitió la publicación."
                    )

            # Información complementaria, nunca criterio para dar éxito:
            # el panel conserva informes de ejecuciones anteriores.
            rows = self.page.locator(
                REPORT_PANEL + ' .finished-report-item-row'
            )
            if rows.count() > baseline and aviso_historial is None:
                for ix in range(rows.count()):
                    txt = rows.nth(ix).inner_text()
                    if PUBLICACION in txt:
                        aviso_historial = txt[:350]
                        self.log(
                            f"[PROD][HISTORIAL] Entrada visible (aún no "
                            f"confirmación final): {aviso_historial!r}"
                        )
                        break
            if time.monotonic() >= next_log:
                self.log(
                    "[PROD][ESPERA FINAL] Esperando aviso explícito de éxito "
                    "o fallo de ARIS y su botón Aceptar. Sin reintentos."
                )
                next_log = time.monotonic() + 15
            self.page.wait_for_timeout(500)
        raise NoConfirmado(
            "ARIS no mostró un resultado final reconocible con Aceptar. "
            "El informe ya fue enviado; revisar el historial y el diálogo "
            "manualmente. No se volverá a ejecutar automáticamente."
        )

    def publicar(self, source_url: str, source_name: str, detalle: str,
                 codigo: str, base: str) -> bool:
        self.aris.abrir(source_url, expected=source_name)
        self.aris.seleccionar(PROCESOS, "grupo")
        self._seleccionar_informe()
        baseline = self.page.locator(
            REPORT_PANEL + ' .finished-report-item-row'
        ).count()
        run = self.page.locator('#gwt-debug-tcReportStartBtn:visible')
        if run.count() != 1:
            raise ArisError("Botón Lanzar del informe no es único")
        run.click()  # exactamente una vez por proceso
        self.log(f"[PROD][ESCRITURA] Lanzado informe: {codigo} / {base}")
        self._escenario(detalle)
        self._mapa(base)
        self._exportar(detalle, base)
        self._destino(codigo)
        return self._resultado(baseline, codigo=codigo)
