"""Flujo 1: preparar estructura, lanzar informe y renombrar mapas en ARIS."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from aris import Aris, ArisError, NoConfirmado, TEMPLATE, PROCESOS, MAPAS, MAPA_GENERADO, nombre_proceso, validar_url

LOG = logging.getLogger(__name__)


@dataclass
class Resultado:
    base: str
    carpeta_proceso: str
    plantilla: str = "pendiente"
    movimiento: str = "pendiente"
    informe: str = "pendiente"
    mapa: str = "pendiente"
    diagrama: str = "pendiente"
    errores: list[str] = field(default_factory=list)

    def as_dict(self):
        return vars(self).copy()


def _registrar_error(item: Resultado, etapa: str, exc: Exception):
    setattr(item, etapa, "error")
    item.errores.append(f"{etapa}: {type(exc).__name__}: {exc}")
    LOG.error("%s | %s | %s", item.base, etapa, exc)


def _nombre_lote() -> str:
    # Evitar '/' y ':' que no pueden usarse en nombres de archivos de Windows
    # y que también pueden hacer ambiguo el trabajo entre zonas horarias.
    return "Procesados " + datetime.now().strftime("%d-%m-%Y %H-%M-%S")


def _guardar(path: Path, data: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _raiz_nombre(aris: Aris) -> str:
    place = aris.page.locator("#headerSearchBox").get_attribute("placeholder") or ""
    prefix = "Buscar en: "
    if not place.startswith(prefix):
        raise ArisError("No se pudo reconocer el nombre de la carpeta inicial")
    return place[len(prefix):].strip()


def _mapear(aris: Aris):
    all_items = aris.listar()
    names = [i.name for i in all_items if i.kind == "grupo"]
    originals = [n for n in names if n in ("00 Ejemplo", "Ejemplo")]
    if len(originals) != 1:
        raise ArisError("Debe existir una sola carpeta de plantilla Ejemplo o 00 Ejemplo")
    template = originals[0]
    folders = [n for n in names if n != template and re.search(r"\s*-\s*Detalle\s*$", n, re.I)]
    other = [n for n in names if n != template and n not in folders]
    if other:
        LOG.info("Grupos ajenos al lote ignorados: %s", other)
    if not folders:
        raise ArisError("No se encontraron carpetas terminadas en '- Detalle'")
    results = [Resultado(base=nombre_proceso(f), carpeta_proceso=f) for f in folders]
    duplicates = [x for x in {r.base for r in results}
                  if sum(1 for r in results if r.base == x) > 1]
    if duplicates:
        raise ArisError(f"Nombres repetidos después de quitar el sufijo: {duplicates}")
    return template, results


def _preflight_plantilla(aris: Aris, raiz_url: str, raiz: str, template: str):
    example_url = aris.entrar(raiz_url, raiz, template)
    children = {i.name for i in aris.listar() if i.kind == "grupo"}
    if PROCESOS not in children or MAPAS not in children:
        raise ArisError(f"La plantilla requiere {PROCESOS!r} y {MAPAS!r}")
    aris.abrir(raiz_url, expected=raiz)
    return example_url


def ejecutar_flujo_1(url: str, perfil: Path, carpeta_resultados: Path,
                     log: Callable[[str], None], simular: bool = True) -> Path:
    """Sin reintentos. El modo simular enumera y valida sin modificar ARIS.

    Las operaciones de estructura deben quedar confirmadas antes de seguir.
    Un informe fallido deja esa carpeta con resultado error y no se repite.
    Un error de escritura ambiguo detiene el lote para evitar perder objetos.
    """
    url = validar_url(url)
    carpeta_resultados.mkdir(parents=True, exist_ok=True)
    iniciado = datetime.now().astimezone().isoformat(timespec="seconds")
    ident = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = carpeta_resultados / f"flujo1_{ident}.json"
    doc = {
        "flujo": 1, "inicio": iniciado, "url": url, "modo": "simulacion" if simular else "ejecucion",
        "estado": "iniciado", "lote": None, "procesos": [],
        "errores_globales": [], "final": None
    }

    def save():
        _guardar(path, doc)

    def update(records):
        doc["procesos"] = [r.as_dict() for r in records]
        save()

    records = []
    try:
        with Aris(perfil, log) as aris:
            log("Validando sesión de ARIS. Si aparece SSO, inicia sesión en Chrome.")
            aris.abrir(url)
            raiz = _raiz_nombre(aris)
            template, records = _mapear(aris)
            log(f"[MAPEO] Carpeta de entrada: {raiz!r}")
            log(f"[MAPEO] Diagramas a procesar: {len(records)}")
            for index, record in enumerate(records, 1):
                log(f"[MAPEO] {index}/{len(records)} {record.carpeta_proceso!r} -> {record.base!r}")
            log(
                f"[PLAN DE COPIAS] {len(records)} estructuras en total: "
                f"1 plantilla original + {max(0, len(records) - 1)} copias nuevas"
            )
            log("[MAPEO] Las carpetas Procesados de lotes anteriores se ignoran; "
                "el mismo N5 puede procesarse en una solicitud nueva")
            doc["copias"] = {
                "total_esperado": len(records),
                "originales": 1,
                "copias_nuevas_esperadas": max(0, len(records) - 1),
                "copias_nuevas_observadas": 0,
                "copias_nuevas_validadas": 0,
                "estructura_total_validada": 0,
                "nombres": [template],
                "incidencias": []
            }
            doc["raiz"] = raiz
            update(records)
            _preflight_plantilla(aris, url, raiz, template)
            if simular:
                doc["estado"] = "simulacion_correcta"
                for record in records:
                    record.plantilla = "planificado"
                    record.movimiento = "planificado"
                    record.informe = "planificado"
                    record.mapa = "planificado"
                    record.diagrama = "planificado"
                update(records)
                log("Simulación validada; no se ha creado ni movido ningún objeto.")
                return path

            lote = _nombre_lote()
            doc["lote"] = lote
            save()
            # Escribir una sola vez; si no hay confirmación NO buscar otro nombre.
            lote_url = aris.crear_grupo(url, raiz, lote)
            log(f"Creada carpeta del lote: {lote}")
            # Mover la plantilla original dentro del lote. Se reutiliza como
            # primera estructura: hay N procesos, así que se requieren N-1 copias.
            aris.mover_grupo(url, raiz, template, lote_url, lote)
            log(f"[COPIAS] Plantilla original movida: {template!r} -> {lote!r}")
            aris.abrir(lote_url, expected=lote)
            # El preflight ya comprobó las dos subcarpetas de la plantilla original.
            copias = [template]
            doc["copias"]["estructura_total_validada"] = 1
            save()
            objetivo = len(records) - 1
            # Se requieren N estructuras, NO N copias adicionales.
            for numero in range(1, objetivo + 1):
                log(
                    f"[COPIAS] Solicitud {numero}/{objetivo}: "
                    f"origen={template!r}, destino={lote!r}"
                )
                try:
                    nueva = aris.copiar_plantilla(
                        lote_url, lote, template, url, raiz
                    )
                    doc["copias"]["copias_nuevas_observadas"] += 1
                    doc["copias"]["nombres"].append(nueva)
                    save()
                    log(f"[COPIAS] Grupo nuevo observado {numero}/{objetivo}: {nueva!r}")
                    # Una copia puede aparecer antes de que termine de cargar
                    # su contenido. Solo continuar al confirmar ambas subcarpetas.
                    aris.validar_copia_completa(lote_url, lote, nueva)
                    # Después de subir de nivel, usar la URL confirmada por ARIS.
                    # Algunos grupos movidos pueden recibir una ruta nueva.
                    lote_url = aris.page.url
                    doc["copias"]["copias_nuevas_validadas"] += 1
                    doc["copias"]["estructura_total_validada"] += 1
                    copias.append(nueva)
                    save()
                    log(
                        f"[COPIAS][OK] {numero}/{objetivo} copias nuevas; "
                        f"{len(copias)}/{len(records)} estructuras completas"
                    )
                except Exception as exc:
                    descripcion = (
                        f"copia {numero}/{objetivo}: {type(exc).__name__}: {exc}"
                    )
                    doc["copias"]["incidencias"].append(descripcion)
                    save()
                    log(f"[COPIAS][DETENIDO] {descripcion}; sin repetir pegado")
                    raise
            log(
                f"[COPIAS][BALANCE] Esperadas={len(records)}, "
                f"validadas={len(copias)}, "
                f"nuevas={doc['copias']['copias_nuevas_validadas']}"
            )
            if len(copias) != len(records) or len(set(copias)) != len(records):
                raise NoConfirmado(
                    f"Cantidad de estructuras no coincide: "
                    f"esperadas={len(records)}, confirmadas={len(copias)}. "
                    "No se comenzará a renombrar."
                )
            # Una comprobación de lectura antes de renombrar todo el lote.
            aris.abrir(lote_url, expected=lote)
            visibles = {x.name for x in aris.listar() if x.kind == "grupo"}
            faltantes = set(copias) - visibles
            if faltantes:
                raise NoConfirmado(
                    f"Las copias confirmadas ya no aparecen en el lote: "
                    f"{sorted(faltantes)!r}; no se continuará."
                )
            for record, copy in zip(records, copias):
                # Cada copia tiene las dos carpetas obligatorias.
                log(f"[RENOMBRADO] Plantilla {copy!r} -> {record.base!r}")
                aris.renombrar(lote_url, lote, copy, record.base)
                record.plantilla = "ok"
                update(records)

            # Las carpetas de origen estaban al mismo nivel que la plantilla.
            # Moverlas a <lote>/<proceso>/00 Procesos.
            for record in records:
                log(f"Preparando {record.base}")
                try:
                    process_url = aris.entrar(lote_url, lote, record.base)
                    procesos_url = aris.entrar(process_url, record.base, PROCESOS)
                    aris.mover_grupo(url, raiz, record.carpeta_proceso, procesos_url, PROCESOS)
                    record.movimiento = "ok"
                    update(records)
                except Exception as exc:
                    _registrar_error(record, "movimiento", exc)
                    update(records)
                    # No se conoce el estado de la operación; no mover ni lanzar más.
                    raise

            # Lanzar un solo informe por carpeta movida, sin reintentos.
            for record in records:
                if record.movimiento != "ok":
                    record.informe = "omitido"
                    continue
                log(f"Informe: {record.base}")
                try:
                    process_url = aris.entrar(lote_url, lote, record.base)
                    procesos_url = aris.entrar(process_url, record.base, PROCESOS)
                    resultado = aris.lanzar_informe(procesos_url, PROCESOS, record.carpeta_proceso)
                    record.informe = "ok" if resultado else "fallo_aris"
                    if not resultado:
                        record.errores.append("ARIS comunicó que falló el informe; no se reintentó")
                except NoConfirmado as exc:
                    _registrar_error(record, "informe", exc)
                    # Indeterminado: el informe pudo seguir ejecutándose;
                    # no lanzar otro informe en este contexto.
                    update(records)
                    raise
                except Exception as exc:
                    _registrar_error(record, "informe", exc)
                update(records)

            # Cerrar/reabrir la página inicial para refrescar los resultados.
            aris.recargar_raiz(url, raiz)
            lote_url = aris.entrar(url, raiz, lote)
            for record in records:
                if record.informe != "ok":
                    record.mapa = record.diagrama = "omitido"
                    update(records)
                    continue
                log(f"Renombrando mapa y modelo: {record.base}")
                try:
                    process_url = aris.entrar(lote_url, lote, record.base)
                    mapas_url = aris.entrar(process_url, record.base, MAPAS)
                    aris.renombrar(mapas_url, MAPAS, MAPA_GENERADO, record.base)
                    record.mapa = "ok"
                    update(records)
                    mapa_url = aris.entrar(mapas_url, MAPAS, record.base)
                    aris.renombrar(mapa_url, record.base, MAPA_GENERADO, record.base, "modelo")
                    record.diagrama = "ok"
                except Exception as exc:
                    _registrar_error(record, "mapa" if record.mapa != "ok" else "diagrama", exc)
                    # Renombrado sin confirmación: no repetir; sí seguir con
                    # otro proceso, si su ruta es inequívoca.
                update(records)
            doc["estado"] = "completado_con_errores" if any(
                r.errores for r in records) else "completado"
    except Exception as exc:
        doc["estado"] = "interrumpido"
        doc["errores_globales"].append(f"{type(exc).__name__}: {exc}")
        LOG.exception("Se interrumpió el flujo")
        log(f"Interrumpido: {exc}")
    finally:
        doc["final"] = datetime.now().astimezone().isoformat(timespec="seconds")
        doc["resumen"] = {
            "total": len(records),
            "estructuras_esperadas": (doc.get("copias") or {}).get("total_esperado", 0),
            "estructuras_validadas": (doc.get("copias") or {}).get("estructura_total_validada", 0),
            "informes_ok": sum(r.informe == "ok" for r in records),
            "informes_fallidos": sum(r.informe in ("fallo_aris", "error") for r in records),
            "completos": sum(r.mapa == r.diagrama == "ok" for r in records)
        }
        update(records)
    return path
