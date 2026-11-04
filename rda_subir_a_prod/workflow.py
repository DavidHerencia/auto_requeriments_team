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
    carpeta_inicial: str = ""
    modelo_origen: str = ""
    normalizacion: str = "pendiente"
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
    results = [
        Resultado(
            base=nombre_proceso(f),
            carpeta_proceso=f,
            carpeta_inicial=f,
        ) for f in folders
    ]
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


def _normalizar_nombres_origen(
    aris: Aris, raiz_url: str, raiz: str,
    records: list[Resultado], log: Callable[[str], None],
    simular: bool, actualizar: Callable[[], None] | None = None
):
    """Alinear el nombre del grupo importado con su MODELO interno.

    Ejemplo:
      grupo inicial: '7.3.3.1_P02 Protección de datos- Gestión ... - Detalle'
      modelo:        '7.3.3.1_P02 Protección de datos: Gestión ...'
      grupo final:   '7.3.3.1_P02 Protección de datos: Gestión ... - Detalle'

    Dos fases: inspeccionar y validar TODOS los modelos primero; después,
    renombrar solo los grupos que difieran. En simulación no escribir nada.
    No se modifican modelos, procesos ni mapas, solo el grupo importado.
    """
    def guardar_estado():
        if actualizar is not None:
            actualizar()

    # Se revisan todas las carpetas antes de modificar ninguna.
    grupos = {
        i.name.casefold() for i in aris.listar() if i.kind == "grupo"
    }
    propuestas: list[tuple[Resultado, str, str]] = []
    for index, record in enumerate(records, 1):
        anterior = record.carpeta_proceso
        log(
            f"[NORMALIZACION][INSPECCION] {index}/{len(records)}: "
            f"abriendo {anterior!r}"
        )
        try:
            aris.entrar(raiz_url, raiz, anterior)
            modelos = [i.name for i in aris.listar() if i.kind == "modelo"]
            if len(modelos) != 1:
                raise ArisError(
                    f"{anterior!r}: se esperaba exactamente 1 modelo interno; "
                    f"encontrados={modelos!r}. No se renombra por suposición."
                )
            modelo = modelos[0]
            if not modelo or not modelo.strip():
                raise ArisError(
                    f"{anterior!r}: el modelo interno no tiene nombre válido"
                )
            esperado = f"{modelo} - Detalle"
            record.modelo_origen = modelo
            propuestas.append((record, modelo, esperado))
            log(
                f"[NORMALIZACION][MODELO] carpeta={anterior!r}; "
                f"modelo={modelo!r}; nombre_esperado={esperado!r}"
            )
            guardar_estado()
        except Exception as exc:
            record.normalizacion = "error_inspeccion"
            record.errores.append(
                f"normalizacion: {type(exc).__name__}: {exc}"
            )
            guardar_estado()
            raise

    # Ninguna escritura si hay nombres de salida repetidos o colisiones
    # con otras carpetas (incluidas las ajenas al lote).
    objetivos = [esperado.casefold() for _, _, esperado in propuestas]
    if len(set(objetivos)) != len(objetivos):
        raise ArisError(
            "Dos modelos generan el mismo nombre de carpeta - Detalle. "
            "No se renombró ninguna carpeta."
        )
    originales = {r.carpeta_proceso.casefold() for r in records}
    for record, modelo, esperado in propuestas:
        if esperado.casefold() in grupos and (
            esperado.casefold() != record.carpeta_proceso.casefold()
        ):
            raise ArisError(
                f"El nombre destino {esperado!r} ya existe en la carpeta "
                "de origen. No se renombró ninguna carpeta."
            )
        # Colisiones no relacionadas con el lote también quedan bloqueadas.
        # Se conserva el nombre EXACTO del modelo; no se sustituyen :, /, etc.
        if esperado.casefold() in originales and (
            esperado.casefold() != record.carpeta_proceso.casefold()
        ):
            raise ArisError(
                f"El nombre destino {esperado!r} coincide con otra "
                "carpeta del lote. No se renombró ninguna carpeta."
            )
    log(
        f"[NORMALIZACION][PLAN] Revisadas={len(propuestas)}; "
        f"a_renombrar={sum(r.carpeta_proceso != expected for r, _, expected in propuestas)}; "
        f"sin_cambios={sum(r.carpeta_proceso == expected for r, _, expected in propuestas)}"
    )
    guardar_estado()

    for record, modelo, esperado in propuestas:
        anterior = record.carpeta_proceso
        if anterior == esperado:
            record.base = modelo
            record.normalizacion = "sin_cambios"
            log(f"[NORMALIZACION][OK] Ya coincide: {anterior!r}")
            guardar_estado()
            continue
        if simular:
            record.base = modelo
            record.normalizacion = "planificado"
            log(
                f"[NORMALIZACION][SIMULACION] "
                f"{anterior!r} -> {esperado!r} (sin cambios en ARIS)"
            )
            guardar_estado()
            continue
        log(
            f"[NORMALIZACION][RENOMBRAR] "
            f"{anterior!r} -> {esperado!r}"
        )
        try:
            # Un único renombrado. Si ARIS no confirma, se detiene el lote.
            aris.renombrar(raiz_url, raiz, anterior, esperado)
        except Exception as exc:
            record.normalizacion = "no_confirmado"
            record.errores.append(
                f"normalizacion: {type(exc).__name__}: {exc}"
            )
            guardar_estado()
            log(
                f"[NORMALIZACION][DETENIDO] No se confirmó {anterior!r} "
                "y no se repetirá el renombrado"
            )
            raise
        record.carpeta_proceso = esperado
        record.base = modelo
        record.normalizacion = "ok"
        log(
            f"[NORMALIZACION][OK] Carpeta corregida a partir "
            f"del nombre del modelo: {esperado!r}"
        )
        guardar_estado()


def ejecutar_flujo_1(url: str, perfil: Path, carpeta_resultados: Path,
                     log: Callable[[str], None], simular: bool = True,
                     resume_path: Path | None = None) -> Path:
    """Punto de entrada estable del flujo 1, ahora por bloques de 10.

    Las funciones de normalización y mapeo anteriores se mantienen para
    compatibilidad con tests y uso histórico; la implementación por
    bloques vive en workflow_batch.py.
    """
    from workflow_batch import ejecutar_flujo_1_batched
    return ejecutar_flujo_1_batched(
        url, perfil, carpeta_resultados, log,
        simular=simular, resume_path=resume_path,
    )
