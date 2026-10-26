from pathlib import Path
from playwright.sync_api import sync_playwright


def procesar_aris(xml: Path, cfg: dict, perfil: Path):
    """Inspecciona ARIS; importa solo con selectores configurados y modo explícito."""
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(perfil), channel="chrome", headless=bool(cfg.get("headless", False)),
            accept_downloads=True,
        )
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.set_default_timeout(int(cfg.get("timeout_ms", 30000)))
            page.goto(cfg["aris_url"], wait_until="domcontentloaded")
            print("Página ARIS abierta. Autentícate manualmente si hace falta.")
            print("URL actual:", page.url)
            if cfg["modo_aris"] == "inspeccionar":
                print("Inspección manual: navega hasta la pantalla de importación.")
                input("Cuando termines de inspeccionar, pulsa Enter para cerrar (sin subir XML)... ")
                return {"estado": "inspeccionado", "importado": False}
            campos = ("selector_archivo", "selector_importar", "selector_confirmacion", "selector_error")
            if not all(cfg.get(k) for k in campos):
                raise RuntimeError("Para importar hay que configurar selectores de archivo, importar, confirmación y error")
            input("Coloca la página en la pantalla de importación y pulsa Enter para continuar... ")
            page.locator(cfg["selector_archivo"]).set_input_files(str(xml))
            # Una importación tiene efectos reales: confirmación local explícita.
            respuesta = input("Se seleccionó el XML. ¿Hacer clic en importar? Escribe IMPORTAR: ")
            if respuesta != "IMPORTAR":
                return {"estado": "cancelado", "importado": False}
            page.locator(cfg["selector_importar"]).click()
            try:
                page.locator(cfg["selector_confirmacion"]).wait_for(state="visible", timeout=int(cfg.get("timeout_ms",30000)))
                return {"estado": "importado", "importado": True, "url": page.url}
            except Exception:
                error = page.locator(cfg["selector_error"])
                error_texto = error.first.inner_text(timeout=2000)[:250] if error.count() else "Sin mensaje reconocible"
                return {"estado": "requiere_revision", "importado": None, "error": error_texto}
        finally:
            browser.close()
