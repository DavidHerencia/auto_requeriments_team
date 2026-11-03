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

    def _esperar_nodo(self, tree_id: str, name: str, level: int,
                      timeout_seconds: int = 60):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                return self._nodo(tree_id, name, level)
            except ArisError:
                self.page.wait_for_timeout(350)
        raise NoConfirmado(
            f"Árbol {tree_id}: no se mostró {name!r} en nivel {level}"
        )

    def _expandir(self, tree_id: str, name: str, level: int):
        nodo = self._esperar_nodo(tree_id, name, level)
        state = nodo.get_attribute("aria-expanded")
        if state == "true":
            self.log(f"[PROD][ARBOL] Ya expandido: {name}")
            return
        if state != "false":
            raise ArisError(f"No se puede expandir {name!r}; aria-expanded={state!r}")
        toggle = nodo.locator(".cellTreeItemImage").first
        if toggle.count() != 1:
            raise ArisError(f"El nodo {name!r} no tiene control de expansión")
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
        self.log(f"[PROD][ARBOL] Expandido: {name}")

    def _seleccionar(self, tree_id: str, name: str, level: int):
        node = self._esperar_nodo(tree_id, name, level)
        label = node.locator(".cellTreeItemValue > span[title]").first
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
        self.log(f"[PROD][ARBOL] Seleccionado {tree_id}: {name}")

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

    def _next(self):
        self._boton("Siguiente").click()

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
        self._seleccionar("TREE_P0", detalle, 2)
        self._next()
        self.log("[PROD][ASISTENTE] Escenario - Detalle confirmado")

    def _mapa(self, nombre: str):
        self._esperar_paso("TREE_P1")
        self._expandir("TREE_P1", MAPAS, 1)
        self._seleccionar("TREE_P1", nombre, 2)
        self._next()
        self.log("[PROD][ASISTENTE] Mapa y Catálogos confirmado")

    def _exportar(self, detalle: str, base: str):
        d = self._dialogo()
        text = d.inner_text()
        if "Resumen de datos a exportar" not in text:
            raise ArisError("El resumen de exportación no está disponible")
        # ARIS presenta los grupos seleccionados en dos TEXTAREA.
        # No aparecen en inner_text(): hay que leer el VALUE del control.
        scen = d.locator('[data-testid="TEXTO_RESUMEN_P2"]')
        maps = d.locator('[data-testid="TEXTO_PROC_MOD_P2"]')
        if scen.count() != 1 or maps.count() != 1:
            raise ArisError("No se encontraron los resúmenes de escenario y mapa")
        scen_value = scen.input_value()
        maps_value = maps.input_value()
        if detalle not in scen_value or base not in maps_value:
            raise ArisError(
                "El resumen de exportación no coincide con el proceso: "
                f"escenario={scen_value[:200]!r}, mapa={maps_value[:200]!r}"
            )
        self.log(
            f"[PROD][RESUMEN EXPORT] Escenario={scen_value[:160]!r} "
            f"Mapa={maps_value[:160]!r}"
        )
        self.log("[PROD][ASISTENTE] Resumen de exportación inspeccionado")
        self._boton("Finalizar").click()  # una sola exportación
        self.log("[PROD][ESCRITURA] Exportación confirmada una sola vez")
        self._esperar_paso("TREE_P2", timeout=360000)

    def _destino(self, codigo: str):
        for nivel, carpeta in enumerate(DESTINO_WIZARD, start=1):
            self._expandir("TREE_P2", carpeta, nivel)
        self._seleccionar("TREE_P2", codigo, 4)
        self._next()
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

    def _resultado(self, baseline: int) -> bool:
        deadline = time.monotonic() + 600
        next_log = 0
        while time.monotonic() < deadline:
            dialogs = self.page.locator('div[role="dialog"]:visible')
            if dialogs.count() == 1:
                d = dialogs.first
                text = d.inner_text()
                if "El informe está completo." in text:
                    button = d.get_by_role("button", name="Aceptar")
                    if button.count() != 1:
                        raise NoConfirmado("Informe completo sin botón Aceptar único")
                    button.click()
                    self.log("[PROD][RESULTADO] ARIS confirmó informe completo")
                    return True
                if re.search(r"cancelad[oa]|ha fallado|con errores|se ha producido un error", text, re.I):
                    self.log(f"[PROD][RESULTADO] ARIS informó fallo: {text[:300]}")
                    return False
            rows = self.page.locator(
                REPORT_PANEL + ' .finished-report-item-row'
            )
            if rows.count() > baseline:
                # Nunca aceptar un estado histórico: solo entradas posteriores
                # al inicio de ESTE informe; deben identificar publicación.
                for ix in range(baseline, rows.count()):
                    txt = rows.nth(ix).inner_text()
                    if PUBLICACION not in txt:
                        continue
                    if "La generación del informe ha concluido." in txt:
                        self.log("[PROD][RESULTADO] Nuevo informe marcado como concluido")
                        return True
                    if re.search(r"cancelad[oa]|ha fallado|con errores", txt, re.I):
                        self.log(f"[PROD][RESULTADO] Nuevo informe falló: {txt[:260]}")
                        return False
            if time.monotonic() >= next_log:
                self.log("[PROD][ESPERA] Esperando confirmación del informe, sin reintentos")
                next_log = time.monotonic() + 15
            self.page.wait_for_timeout(500)
        raise NoConfirmado(
            "La publicación se lanzó pero ARIS no confirmó el resultado. "
            "Revisar manualmente; NO volver a ejecutar automáticamente."
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
        return self._resultado(baseline)
