from pathlib import Path
from datetime import datetime, timezone
from modules.files import preparar_xml
from modules.aris import procesar_aris
from modules.reports import guardar_reporte
from config import DATA


def ejecutar_desde_aris(xml: Path, config: dict):
    """Inyección temporal en el pipeline: etapas previas simuladas con XML local."""
    trabajo = {"source_id": "local_prueba", "etapa": "preparacion", "estado": "pendiente", "fecha": datetime.now(timezone.utc).isoformat()}
    preparado = None
    try:
        preparado = preparar_xml(xml, DATA / "xml")
        trabajo.update(etapa="aris", estado="preparado", archivo=preparado.name)
        resultado = procesar_aris(preparado, config, DATA / "chrome_profile")
        trabajo.update(etapa="aris", estado=resultado["estado"], importado=resultado["importado"])
        if resultado.get("error"):
            trabajo["error"] = resultado["error"]
    except Exception as exc:
        trabajo.update(estado="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        ruta = guardar_reporte(trabajo, DATA / "reportes")
        print("Estado:", trabajo["estado"], "Reporte:", ruta)
    return trabajo
