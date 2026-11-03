"""Operaciones de ARIS Repository. Solo interfaz web, sin API privada.

No se repite ninguna acción de escritura cuando ARIS no confirma su resultado.
Selectores contrastados con el HTML aportado el 03/11/2026; probar en ARIS real.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

REPORT = "BBVA - Generación automática de mapas"
TEMPLATE = "00 Ejemplo"
PROCESOS = "00 Procesos"
MAPAS = "01 Mapas y Catálogos"
MAPA_GENERADO = "25 Mapa de Roles"
GRID = ".dataGridWidget.largeTable:visible"
DIALOG = 'div[role="dialog"]:visible'
REPORT_PANEL = ".sidebar.reportSidebar"
DETAIL_SUFFIX = re.compile(r"\s*-\s*Detalle\s*$", re.I)


class ArisError(RuntimeError):
    pass


class NoConfirmado(ArisError):
    """Se hizo una acción, pero no se confirmó: NUNCA ejecutarla otra vez."""
    pass


@dataclass(frozen=True)
class Item:
    name: str
    kind: str


def nombre_proceso(carpeta: str) -> str:
    if not DETAIL_SUFFIX.search(carpeta):
        raise ValueError(f"No termina en - Detalle: {carpeta!r}")
    resultado = DETAIL_SUFFIX.sub("", carpeta).strip()
    if not resultado:
        raise ValueError("El nombre del proceso quedaría vacío")
    return resultado


def validar_url(url: str) -> str:
    u = urlsplit(url.strip())
    if (u.scheme != "https" or not u.hostname or
        not u.hostname.endswith(".igrupobbva") or
        not u.fragment.startswith("default/repository/")):
        raise ValueError("Usa un enlace HTTPS directo a una carpeta de ARIS Repository")
    if u.username or u.password:
        raise ValueError("No se admiten credenciales dentro de la URL")
    return url.strip()


class Aris:
    def __init__(self, profile: Path, log, timeout_ms: int = 45000):
        self.profile = profile
        self.log = log
        self.timeout = timeout_ms
        self.pw = self.context = self.page = None

    def __enter__(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        self.pw = sync_playwright().start()
        try:
            self.context = self.pw.chromium.launch_persistent_context(
                str(self.profile), channel="chrome", headless=False,
                accept_downloads=False
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(self.timeout)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.pw:
                self.pw.stop()

    def _ready(self, expected: str | None = None, timeout: int = 180000):
        # Login/SSO manual: navegador visible; solo se espera señal de repositorio.
        try:
            self.page.wait_for_function(
                """expected => {
                    const box = document.querySelector('#headerSearchBox');
                    const button = document.querySelector(
                        '[data-testid="RepositoryCreateModelBtn"]');
                    const placeholder = (box && box.getAttribute('placeholder') || '').trim();
                    return !!button && !!box &&
                        (!expected || placeholder === 'Buscar en: ' + expected);
                }""", arg=expected, timeout=timeout
            )
        except PWTimeout as exc:
            raise ArisError(
                "ARIS no quedó listo o no se confirmó la carpeta. "
                "Completa el SSO manualmente y vuelve a validar; no se repitió ninguna acción."
            ) from exc
        self.page.locator(GRID).first.wait_for(state="visible", timeout=self.timeout)

    def abrir(self, url: str, expected: str | None = None, reload: bool = False):
        url = validar_url(url)
        if self.page.url != url:
            actual = urlsplit(self.page.url)
            destino = urlsplit(url)
            # La navegación por hash preserva el portapapeles interno GWT entre
            # Copiar/Cortar y Pegar. Un reload lo perdería.
            if (not reload and actual.scheme == destino.scheme and
                    actual.netloc == destino.netloc and actual.path == destino.path):
                self.page.evaluate("(url) => { location.hash = new URL(url).hash; }", url)
            else:
                self.page.goto(url, wait_until="domcontentloaded", timeout=120000)
        elif reload:
            self.page.reload(wait_until="domcontentloaded", timeout=120000)
        self._ready(expected)

    def recargar_raiz(self, root_url: str, name: str):
        # Fuerza instancia nueva de página para observar los cambios del informe.
        self.page.close()
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.timeout)
        self.abrir(root_url, expected=name, reload=True)

    def _grid(self):
        g = self.page.locator(GRID)
        if g.count() != 1:
            raise ArisError(f"Se esperaba una sola tabla visible; encontradas {g.count()}")
        return g

    def _visible_records(self) -> list[Item]:
        output = []
        for row in self._grid().locator('tr[__gwt_row]').all():
            group = row.locator('.itemTitle .titledesc[title]')
            if group.count() == 1:
                output.append(Item(group.get_attribute("title").strip(), "grupo"))
                continue
            model = row.locator('.dataGridCell div.title a.clickable')
            if model.count() == 1:
                output.append(Item(model.inner_text().strip(), "modelo"))
        return output

    def _scroll_grid(self, reset=False) -> bool:
        return bool(self._grid().evaluate(
            """(grid, reset) => {
                const divs = [grid, ...grid.querySelectorAll('*')];
                const scrollables = divs.filter(x => {
                    const css = getComputedStyle(x);
                    return x.scrollHeight > x.clientHeight + 6 &&
                        /auto|scroll/.test(css.overflowY);
                });
                scrollables.sort((a,b) =>
                    (b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
                const el = scrollables[0];
                if (!el) return false;
                const before = el.scrollTop;
                el.scrollTop = reset ? 0 :
                    Math.min(el.scrollHeight-el.clientHeight, before+Math.max(100,el.clientHeight-45));
                return Math.abs(el.scrollTop-before)>1;
            }""", reset
        ))

    def listar(self) -> list[Item]:
        self._scroll_grid(reset=True)
        self.page.wait_for_timeout(120)
        seen = {}
        for _ in range(180):
            for rec in self._visible_records():
                if rec.name in seen and seen[rec.name] != rec.kind:
                    raise ArisError(f"Nombre duplicado entre tipos de elemento: {rec.name}")
                seen[rec.name] = rec.kind
            if not self._scroll_grid():
                break
            self.page.wait_for_timeout(80)
        else:
            raise ArisError("La lista es demasiado larga para verificarla completamente")
        return [Item(k, v) for k, v in seen.items()]

    def _row(self, name: str, kind: str = "grupo"):
        self._scroll_grid(reset=True)
        self.page.wait_for_timeout(100)
        for _ in range(180):
            matches = []
            for row in self._grid().locator('tr[__gwt_row]').all():
                if kind == "grupo":
                    title = row.locator(".itemTitle .titledesc[title]")
                    actual = title.get_attribute("title").strip() if title.count() == 1 else None
                else:
                    title = row.locator(".dataGridCell div.title a.clickable")
                    actual = title.inner_text().strip() if title.count() == 1 else None
                if actual == name:
                    matches.append(row)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ArisError(f"Elemento ambiguo: {name}")
            if not self._scroll_grid():
                break
            self.page.wait_for_timeout(100)
        raise ArisError(f"No se encontró {kind} exacto: {name}")

    def entrar(self, parent_url: str, parent_name: str, child_name: str) -> str:
        self.abrir(parent_url, expected=parent_name)
        row = self._row(child_name, "grupo")
        row.locator(".itemTitle .titledesc a.clickable").dblclick()
        self._ready(child_name, timeout=60000)
        return self.page.url

    def seleccionar(self, name: str, kind: str = "grupo"):
        # Desmarcar únicamente casillas de filas, nunca la casilla global.
        for row in self._grid().locator('tr[__gwt_row]').all():
            ch = row.locator('input[role="checkbox"]')
            if ch.count() == 1 and ch.is_checked():
                row.locator("label.checkbox").click()
        row = self._row(name, kind)
        check = row.locator('input[role="checkbox"]')
        if check.count() != 1:
            raise ArisError("No se encontró una casilla única para el elemento")
        row.locator("label.checkbox").click()
        if not check.is_checked():
            raise ArisError(f"ARIS no seleccionó {name}")
        return row

    def _toolbar(self, action: str):
        sel = f"li.TOOLBAR_ITEM_{action}:visible a[aria-disabled='false']"
        element = self.page.locator(sel)
        if element.count() != 1:
            raise ArisError(f"Acción {action} no disponible ({element.count()})")
        return element

    def _dialog(self):
        d = self.page.locator(DIALOG)
        d.first.wait_for(state="visible", timeout=self.timeout)
        if d.count() != 1:
            raise ArisError(f"Diálogo ambiguo: {d.count()} visibles")
        return d

    def _guardar_dialogo(self, nuevo: str):
        d = self._dialog()
        fields = d.locator('input[type="text"]:visible, textarea:visible')
        if fields.count() != 1:
            raise ArisError(f"Campo de nombre ambiguo ({fields.count()})")
        fields.fill(nuevo)
        ok = d.get_by_role("button", name=re.compile(r"^(Aceptar|Crear|Guardar)$", re.I))
        if ok.count() != 1:
            raise ArisError(f"Botón de confirmación ambiguo ({ok.count()})")
        ok.click()  # una única acción de escritura

    def _esperar_nombre(self, nombre: str, kind: str = "grupo", absent=False):
        deadline = time.monotonic() + self.timeout / 1000
        while time.monotonic() < deadline:
            present = any(x.name == nombre and x.kind == kind for x in self.listar())
            if present != absent:
                return
            self.page.wait_for_timeout(300)
        raise NoConfirmado(f"ARIS no confirmó {'ausencia' if absent else 'presencia'}: {nombre}")

    def crear_grupo(self, parent_url: str, parent_name: str, nombre: str) -> str:
        self.abrir(parent_url, expected=parent_name)
        if any(i.name == nombre for i in self.listar()):
            raise ArisError(f"Ya existe el grupo {nombre}; no sobrescribir")
        self._toolbar("CREATE_GROUP").click()
        self._guardar_dialogo(nombre)
        self._esperar_nombre(nombre)
        self.log(f"Grupo creado: {nombre}")
        return self.entrar(parent_url, parent_name, nombre)

    def renombrar(self, parent_url: str, parent_name: str,
                  anterior: str, nuevo: str, kind="grupo"):
        self.abrir(parent_url, expected=parent_name)
        if any(i.name == nuevo and i.kind == kind for i in self.listar()):
            raise ArisError(f"Nombre destino ya existe: {nuevo}")
        self.seleccionar(anterior, kind)
        self._toolbar("RENAME").click()
        self._guardar_dialogo(nuevo)
        self._esperar_nombre(nuevo, kind)
        self.log(f"Renombrado ({kind}): {anterior} -> {nuevo}")

    def _pegar_una_vez(self):
        self._toolbar("PASTE").click()  # NO reintentar si el resultado es ambiguo

    def mover_grupo(self, src_url: str, src_parent: str, name: str,
                    dst_url: str, dst_parent: str):
        self.abrir(src_url, expected=src_parent)
        self.seleccionar(name)
        self._toolbar("CUT").click()
        self.abrir(dst_url, expected=dst_parent)
        if any(i.name == name for i in self.listar()):
            raise NoConfirmado(f"Destino ya tiene {name}; se interrumpe el movimiento")
        self._pegar_una_vez()
        self._esperar_nombre(name)
        self.log(f"Movido: {name} -> {dst_parent}")

    def copiar_plantilla(self, parent_url: str, parent_name: str) -> str:
        self.abrir(parent_url, expected=parent_name)
        antes = {i.name for i in self.listar()}
        if TEMPLATE not in antes:
            raise ArisError("No se encontró plantilla para copiar")
        self.seleccionar(TEMPLATE)
        self._toolbar("COPY").click()
        self._pegar_una_vez()
        deadline = time.monotonic() + self.timeout / 1000
        while time.monotonic() < deadline:
            despues = {i.name for i in self.listar()}
            nuevos = despues - antes
            if len(nuevos) == 1:
                copied = next(iter(nuevos))
                self.log(f"Plantilla copiada como {copied}")
                return copied
            if len(nuevos) > 1:
                break
            self.page.wait_for_timeout(350)
        raise NoConfirmado("No se pudo identificar UNA copia nueva de 00 Ejemplo")

    def _seleccionar_reporte(self, target: str):
        tab = self.page.locator('li[data-testid="REPORT_REPOSITORY_BUTTON"]')
        panel = self.page.locator(REPORT_PANEL)
        if not panel.is_visible():
            tab.locator('a[aria-label="Informes"]').click()
        panel.wait_for(state="visible", timeout=self.timeout)
        ctx = panel.locator(".reportContextPanel .label")
        self.page.wait_for_function(
            """name => {
                const x = document.querySelector(
                    '.sidebar.reportSidebar .reportContextPanel .label');
                return x && x.textContent.trim() === name;
            }""", arg=target, timeout=self.timeout
        )
        combo = panel.locator('.tcReportSelectReportDropDown input[role="combobox"]')
        panel.locator('.tcReportSelectReportDropDown a[aria-label="dropdown-toggle"]').click()
        self.page.wait_for_timeout(200)
        options = []
        for elem in self.page.get_by_text(REPORT, exact=True).all():
            if not elem.is_visible():
                continue
            valid = elem.evaluate("""el => !el.closest(
                '.reportPanel-reports-list, .scrollable-report-panel, .finished-report-item-row')
                && el.children.length === 0""")
            if valid:
                options.append(elem)
        if len(options) != 1:
            raise ArisError(f"Selección del informe ambigua ({len(options)})")
        options[0].click()
        if combo.input_value().strip() != REPORT:
            raise ArisError("El informe no quedó seleccionado")
        fmt = panel.locator('.tcReportSelectFormatDropDown input')
        if "Archivo de texto" not in fmt.input_value():
            panel.locator('.tcReportSelectFormatDropDown a[aria-label="dropdown-toggle"]').click()
            candidates = [x for x in self.page.get_by_text("Archivo de texto (*.txt)", exact=True).all()
                          if x.is_visible()]
            if len(candidates) != 1:
                raise ArisError("No se pudo elegir el formato TXT")
            candidates[0].click()
            if "Archivo de texto" not in fmt.input_value():
                raise ArisError("Formato TXT no confirmado")

    def lanzar_informe(self, parent_url: str, parent_name: str, detalle: str) -> bool:
        self.abrir(parent_url, expected=parent_name)
        self.seleccionar(detalle)
        self._seleccionar_reporte(detalle)
        lanzar = self.page.locator('#gwt-debug-tcReportStartBtn:visible')
        if lanzar.count() != 1:
            raise ArisError("No apareció el botón Lanzar")
        lanzar.click()  # una ejecución por proceso, nunca repetir
        self.log(f"Informe lanzado: {detalle}")
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            dialogs = self.page.locator(DIALOG)
            if dialogs.count() == 1:
                d = dialogs.first
                text = d.inner_text()
                if "El informe está completo." in text:
                    ok = d.get_by_role("button", name="Aceptar")
                    if ok.count() != 1:
                        raise NoConfirmado("Informe completo pero sin Aceptar único")
                    ok.click()
                    self.log(f"Informe concluido: {detalle}")
                    return True
                if (re.search(r"cancelad[oa]|ha fallado|con errores|se ha producido un error",
                              text, re.I)
                        and "Ejecutando informe" not in text):
                    ok = d.get_by_role("button", name="Aceptar")
                    if ok.count() == 1:
                        ok.click()
                    self.log(f"Informe fallido (sin reintento): {detalle}")
                    return False
            self.page.wait_for_timeout(350)
        raise NoConfirmado(f"No se obtuvo el resultado final del informe: {detalle}")
