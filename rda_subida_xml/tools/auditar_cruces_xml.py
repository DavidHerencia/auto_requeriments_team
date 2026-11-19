"""Auditoría de SOLO LECTURA: asociación N5 -> XML original -> HERO -> final.

Uso, desde rda_subida_xml:
    python tools/auditar_cruces_xml.py --checkpoint data/exportaciones/procesados_LOTE.json

No abre Chrome ni ARIS, no escribe XML, no corrige nada automáticamente.
Produce CSV y JSON únicamente con resúmenes (nunca el XML completo).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

CODIGO = re.compile(r"(?<!\w)(\d+(?:\.\d+)+_P\d+)(?!\w)", re.I)
NODOS = {
    "task", "userTask", "serviceTask", "scriptTask", "manualTask",
    "businessRuleTask", "sendTask", "receiveTask", "callActivity",
    "subProcess", "transaction", "adHocSubProcess",
}
EXCLUIR = {"inicio", "fin", "start", "end", "proceso", "actividad"}


def local(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def simple(texto):
    s = unicodedata.normalize("NFKD", str(texto or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", s))


def codigo_en(texto):
    encontrado = CODIGO.search(str(texto or ""))
    return encontrado.group(1).upper() if encontrado else ""


def datos_xml(texto):
    """Metadatos y actividades: nunca se modifica el documento."""
    if not texto:
        return {"error": "No se dispone del XML", "nombres": set(), "proceso": "",
                "lane": "", "actividades": 0}
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError as exc:
        return {"error": f"XML no parseable: {exc}", "nombres": set(),
                "proceso": "", "lane": "", "actividades": 0}
    procesos = [x for x in raiz.iter() if local(x.tag) == "process"]
    p = procesos[0].get("name", "") if len(procesos) == 1 else ""
    lanes = [x for x in raiz.iter() if local(x.tag) == "lane"
             and x.get("id") in ("LanePrincipal", "Lane_Principal")]
    lane = lanes[0].get("name", "") if len(lanes) == 1 else ""
    nodos = [x for x in raiz.iter() if local(x.tag) in NODOS]
    nombres = {simple(x.get("name", "")) for x in nodos}
    nombres.difference_update(EXCLUIR)
    nombres.discard("")
    return {
        "error": "" if len(procesos) == 1 else
                 f"Se encontraron {len(procesos)} elementos process",
        "nombres": nombres,
        "proceso": p,
        "lane": lane,
        "actividades": len(nodos),
    }


def similitud(a, b):
    """Jaccard de actividades nombradas, excluyendo process/lane renombrados."""
    a, b = a["nombres"], b["nombres"]
    if not a or not b:
        return None, 0
    comunes = len(a & b)
    return comunes / len(a | b), comunes


def archivo_xml(ruta):
    if not ruta:
        return "", "Sin ruta"
    p = Path(ruta)
    if not p.is_file():
        return "", f"No se encontró: {p}"
    try:
        return p.read_text(encoding="utf-8-sig"), ""
    except (OSError, UnicodeError) as exc:
        return "", f"No se pudo leer {p.name}: {type(exc).__name__}"


def auditar(registros):
    """Relaciona por id_solicitud, jamás por posición ni por nombre de archivo."""
    originales = {}
    metadata = {}
    for r in registros:
        sid = str(r.get("id_solicitud", ""))
        key = str(r.get("_audit_key", sid))
        if not sid or key in originales:
            raise ValueError(f"id_solicitud ausente o duplicado en la fuente: {key!r}")
        originales[key] = datos_xml(r.get("xml", ""))
        metadata[key] = {
            "id_solicitud": sid, "id_n5": str(r.get("id_n5", "")).upper()
        }

    salida = []
    for r in registros:
        sid = r["id_solicitud"]
        key = str(r.get("_audit_key", sid))
        n5 = str(r.get("id_n5", "")).strip().upper()
        nombre = str(r.get("nombre_proceso", "")).strip()
        original = originales[key]
        captura_texto, captura_error = archivo_xml(r.get("xml_capturado_ruta"))
        final_texto, final_error = archivo_xml(r.get("xml_corregido_ruta"))
        captura = datos_xml(captura_texto)
        final = datos_xml(final_texto)
        disponible = captura if captura_texto else final
        fase = "capturado" if captura_texto else "final" if final_texto else "sin_archivo"
        score, comunes = similitud(original, disponible)
        posibles = []
        for otro_id, otro in originales.items():
            if (otro_id == key
                    or metadata[otro_id]["id_solicitud"] == sid):
                continue
            alt, coincidentes = similitud(disponible, otro)
            if alt is not None and coincidentes >= 3:
                posibles.append((alt, otro_id, coincidentes))
        posibles.sort(reverse=True)
        mejor = posibles[0] if posibles else None

        source_code = codigo_en(original["proceso"])
        lane_code = codigo_en(original["lane"])
        expected_title = f"{n5} {nombre}"
        causa = []
        if source_code and source_code != n5:
            causa.append("XML_ORIGINAL_PROCESS_CON_OTRO_N5")
        if lane_code and lane_code != n5:
            causa.append("XML_ORIGINAL_LANE_CON_OTRO_N5")
        if final_texto and final["proceso"] != expected_title:
            causa.append("TITULO_PROCESS_FINAL_NO_COINCIDE")
        if final_texto and final["lane"] != expected_title:
            causa.append("LANE_FINAL_NO_COINCIDE")
        if (mejor and score is not None
                and mejor[0] >= 0.70
                and mejor[0] >= score + 0.30):
            causa.append("CONTENIDO_SE_PARECE_MAS_A_OTRA_SOLICITUD")
        if (score is not None and score < 0.35
                and len(original["nombres"]) >= 3
                and len(disponible["nombres"]) >= 3):
            causa.append("ACTIVIDADES_CAMBIARON_SUSTANCIALMENTE")
        if not captura_texto and not final_texto:
            causa.append("SIN_XML_CAPTURADO_O_FINAL")
        if original["error"]:
            causa.append("XML_ORIGINAL_NO_EVALUABLE")
        if final_texto and final["error"]:
            causa.append("XML_FINAL_NO_EVALUABLE")

        criticos = {
            "XML_ORIGINAL_PROCESS_CON_OTRO_N5",
            "XML_ORIGINAL_LANE_CON_OTRO_N5",
            "CONTENIDO_SE_PARECE_MAS_A_OTRA_SOLICITUD",
        }
        estado = (
            "SOSPECHA_ALTA" if criticos.intersection(causa)
            else "REVISAR" if causa
            else "SIN_INDICIOS_EN_ESTA_AUDITORIA"
        )
        salida.append({
            "id_solicitud": sid,
            "archivo_checkpoint": r.get("_checkpoint", ""),
            "id_n5": n5,
            "nombre_proceso": nombre,
            "estado": estado,
            "senales": ";".join(causa),
            "fase_comparada": fase,
            "codigo_xml_original_process": source_code,
            "codigo_xml_original_lane": lane_code,
            "proceso_xml_original": original["proceso"],
            "proceso_xml_final": final["proceso"],
            "actividades_original": original["actividades"],
            "actividades_capturado": captura["actividades"],
            "actividades_final": final["actividades"],
            "coincidencia_con_su_original": (
                round(score, 4) if score is not None else ""
            ),
            "actividad_nombres_comunes": comunes,
            "mejor_otra_solicitud": mejor[1] if mejor else "",
            "similitud_con_otra": round(mejor[0], 4) if mejor else "",
            "problemas_lectura": ";".join(
                v for v in (captura_error, final_error,
                            original["error"], captura["error"] if captura_texto else "",
                            final["error"] if final_texto else "") if v
            ),
        })
    return salida


def ejecutar(checkpoint: Path | None = None,
            output_prefix: Path | None = None,
            directorio: Path | None = None):
    if (checkpoint is None) == (directorio is None):
        raise ValueError("Indica exactamente uno: --checkpoint o --directorio")
    fuentes = (
        [checkpoint] if checkpoint else sorted(
            directorio.glob("procesados_*.json")
        )
    )
    if not fuentes:
        raise ValueError("No se encontraron archivos procesados_*.json")
    registros = []
    for fuente in fuentes:
        contenido = json.loads(fuente.read_text(encoding="utf-8"))
        actuales = contenido.get("registros")
        if not isinstance(actuales, list):
            raise ValueError(f"El checkpoint {fuente} no tiene registros[]")
        for r in actuales:
            entrada = dict(r)
            entrada["_checkpoint"] = fuente.name
            # Permite solicitudes repetidas en distintos lotes sin juntar
            # accidentalmente sus XML, e impide compararlas consigo mismas.
            entrada["_audit_key"] = (
                fuente.name + "::" + str(r.get("id_solicitud", ""))
            )
            registros.append(entrada)
    salida = auditar(registros)
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_prefix or (checkpoint.parent if checkpoint else directorio) / (
        f"auditoria_cruces_{marca}"
    )
    base.parent.mkdir(parents=True, exist_ok=True)
    csv_path = base.with_suffix(".csv")
    json_path = base.with_suffix(".json")
    if csv_path.exists() or json_path.exists():
        raise FileExistsError("Ya existe un reporte; elegir otro nombre")
    cols = list(salida[0]) if salida else ["id_solicitud", "estado"]
    with csv_path.open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(salida)
    with json_path.open("x", encoding="utf-8") as f:
        json.dump({"fuentes": [str(x) for x in fuentes], "total": len(salida),
                   "estados": dict(Counter(r["estado"] for r in salida)),
                   "registros": salida}, f, ensure_ascii=False, indent=2)
    return csv_path, json_path, Counter(r["estado"] for r in salida)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    origen = parser.add_mutually_exclusive_group(required=True)
    origen.add_argument("--checkpoint", type=Path,
                        help="Un solo procesados_*.json del RDA XML")
    origen.add_argument("--directorio", type=Path,
                        help="Todos los procesados_*.json de una carpeta")
    parser.add_argument("--salida", type=Path,
                        help="Prefijo de salida, sin extensión (opcional)")
    args = parser.parse_args()
    csv_path, json_path, conteo = ejecutar(
        args.checkpoint, args.salida, args.directorio
    )
    print("Auditoría local de SOLO LECTURA (sin modificar XML ni ARIS)")
    print("Estados:", dict(conteo))
    print("CSV:", csv_path)
    print("JSON:", json_path)
    print("Una coincidencia semántica aproximada NO demuestra un cruce.")
    print("Los casos SOSPECHA_ALTA y REVISAR requieren contrastar con el origen.")


if __name__ == "__main__":
    main()
