"""Flujo 1 por bloques: un lote ARIS, sublotes de 10, checkpoint JSON.

Se conserva el comportamiento ARIS probado: copiar Ejemplo, renombrar grupo,
mover - Detalle, lanzar informe, renombrar mapa y modelo. No hay reintentos de
escrituras; en un reinicio, los estados indeterminados quedan para revisión.
"""
from __future__ import annotations

import json
import re
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Callable

from aris import (
    Aris, ArisError, NoConfirmado, PROCESOS, MAPAS, MAPA_GENERADO,
    nombre_proceso, validar_url,
)
from workflow import (
    Resultado, _guardar, _nombre_lote, _preflight_plantilla, _raiz_nombre,
)

BATCH_SIZE = 10
COPIA_RE = re.compile(r"^00 Ejemplo\((\d+)\)$")
TERMINALES = {"ok", "fallo_aris", "omitido"}


def bloques(items, size=BATCH_SIZE):
    if not isinstance(size, int) or size < 1:
        raise ValueError("Tamaño de bloque inválido")
    for start in range(0, len(items), size):
        yield start // size + 1, items[start:start + size]


def _reconstruir(raw):
    permitidos = {f.name for f in fields(Resultado)}
    return Resultado(**{k: v for k, v in raw.items() if k in permitidos})


def _preparar_registros(aris, raiz_url, raiz, doc, log, simular, update):
    """Una enumeración de raíz + una entrada a cada origen para mapear y leer modelo.

    La fase de renombrados es posterior porque primero deben conocerse
    TODOS los nombres objetivo para detectar colisiones, sin cambios parciales.
    """
    aris.abrir(raiz_url, expected=raiz)
    items = aris.listar()  # UNA lectura raíz para mapeo y colisiones
    grupos = [x.name for x in items if x.kind == "grupo"]
    templates = [x for x in grupos if x in ("00 Ejemplo", "Ejemplo")]
    if len(templates) != 1:
        raise ArisError(
            "Se requiere una única plantilla Ejemplo / 00 Ejemplo en origen. "
            "Si la plantilla fue movida en un intento anterior, reanuda el "
            "JSON de esa ejecución, no inicies un lote nuevo."
        )
    plantilla = templates[0]
    candidatos = sorted(
        (x for x in grupos if x != plantilla and re.search(
            r"\s*-\s*Detalle\s*$", x, re.I
        )),
        key=str.casefold,
    )
    if not candidatos:
        raise ArisError("No se encontraron carpetas - Detalle en el origen")
    doc["plantilla"] = plantilla
    doc["total_inicial"] = len(candidatos)
    log(f"[MAPEO+NORMALIZACION] Origen={raiz!r}; procesos={len(candidatos)}")
    _preflight_plantilla(aris, raiz_url, raiz, plantilla)

    records = []
    for i, carpeta in enumerate(candidatos, 1):
        aris.entrar(raiz_url, raiz, carpeta)
        modelos = [x.name for x in aris.listar() if x.kind == "modelo"]
        if len(modelos) != 1 or not modelos[0].strip():
            raise ArisError(
                f"[{i}/{len(candidatos)}] {carpeta!r} requiere exactamente "
                f"un modelo interno con nombre: {modelos!r}."
            )
        modelo = modelos[0]
        esperado = f"{modelo} - Detalle"
        record = Resultado(
            base=modelo, carpeta_proceso=carpeta,
            carpeta_inicial=carpeta, modelo_origen=modelo,
            normalizacion="pendiente" if carpeta != esperado else "sin_cambios",
            batch=(i - 1) // BATCH_SIZE + 1,
        )
        records.append(record)
        doc["procesos"] = [r.as_dict() for r in records]
        update()
        log(
            f"[MAPEO+NORMALIZACION] {i}/{len(candidatos)} "
            f"modelo={modelo!r}; grupo={carpeta!r}; "
            f"accion={'renombrar' if carpeta != esperado else 'conservar'}"
        )
    objetivos = [f"{r.modelo_origen} - Detalle" for r in records]
    if len({x.casefold() for x in objetivos}) != len(objetivos):
        raise ArisError(
            "Dos modelos producen el mismo nombre de grupo - Detalle; "
            "no se renombró nada"
        )
    iniciales = {x.casefold() for x in grupos}
    for r, objetivo in zip(records, objetivos):
        if (objetivo.casefold() in iniciales
                and objetivo.casefold() != r.carpeta_proceso.casefold()):
            raise ArisError(
                f"El nombre objetivo {objetivo!r} ya existe en el origen; "
                "no se renombró nada"
            )
    log(
        f"[NORMALIZACION][PLAN] {len(records)} inspeccionados; "
        f"{sum(r.carpeta_proceso != esperado for r, esperado in zip(records, objetivos))} "
        "grupos necesitan renombrarse"
    )
    if simular:
        for r, objetivo in zip(records, objetivos):
            r.normalizacion = (
                "planificado" if r.carpeta_proceso != objetivo
                else "sin_cambios"
            )
        doc["procesos"] = [r.as_dict() for r in records]
        update()
        return plantilla, records

    for r, objetivo in zip(records, objetivos):
        if r.carpeta_proceso == objetivo:
            continue
        anterior = r.carpeta_proceso
        r.normalizacion = "en_curso"
        doc["procesos"] = [x.as_dict() for x in records]
        update()
        log(f"[NORMALIZACION][RENOMBRAR] {anterior!r} -> {objetivo!r}")
        # No repetir si la operación quedó indeterminada.
        aris.renombrar(raiz_url, raiz, anterior, objetivo)
        r.carpeta_proceso = objetivo
        r.normalizacion = "ok"
        doc["procesos"] = [x.as_dict() for x in records]
        update()
    aris.abrir(raiz_url, expected=raiz)
    return plantilla, records


def _grupos_lote(aris, lote_url, lote):
    aris.abrir(lote_url, expected=lote)
    return {x.name for x in aris.listar() if x.kind == "grupo"}


def _grupos_libres(grupos, plantilla):
    # Si Ejemplo se llamaba 'Ejemplo', GWT crea Ejemplo(n).
    sufijo = re.compile(r"^" + re.escape(plantilla) + r"\((\d+)\)$")
    return sorted(
        (x for x in grupos if sufijo.fullmatch(x)),
        key=lambda x: int(sufijo.fullmatch(x).group(1)),
    )


def _verificar_checkpoint_estructura(grupos, r):
    if r.plantilla == "ok" and r.base not in grupos:
        raise ArisError(
            f"Checkpoint dice que existe {r.base!r}, pero ARIS no lo muestra. "
            "No se crea otro grupo."
        )
    if r.plantilla == "pendiente" and r.base in grupos:
        raise ArisError(
            f"Grupo {r.base!r} ya existe, pero el checkpoint no confirma "
            "su creación. Inspeccionar manualmente antes de reanudar."
        )


def _estado_incierto(r):
    # Operaciones de escritura ejecutadas pero sin confirmación final:
    # nunca volver a pulsarlas en un relanzamiento.
    return (
        r.plantilla in {"copiando", "copiada", "en_curso", "no_confirmado", "error"}
        or r.movimiento in {"en_curso", "error", "no_confirmado"}
        or r.informe in {"en_curso", "error", "no_confirmado"}
        or r.mapa in {"en_curso", "error", "no_confirmado"}
        or r.diagrama in {"en_curso", "error", "no_confirmado"}
    )


def _resumen(doc, records):
    doc["resumen"] = {
        "total": len(records),
        "bloques": len(list(bloques(records, doc.get("batch_size", BATCH_SIZE)))),
        "estructuras": sum(r.plantilla == "ok" for r in records),
        "movidos": sum(r.movimiento == "ok" for r in records),
        "informes_ok": sum(r.informe == "ok" for r in records),
        "informes_fallidos": sum(r.informe == "fallo_aris" for r in records),
        "completos": sum(
            r.plantilla == r.movimiento == r.informe == r.mapa == r.diagrama == "ok"
            for r in records
        ),
        "requieren_revision": sum(_estado_incierto(r) for r in records),
        "pendientes": sum(
            r.plantilla != "ok" or r.movimiento != "ok"
            or r.informe == "pendiente" for r in records
        ),
    }


def ejecutar_flujo_1_batched(url: str, perfil: Path, carpeta_resultados: Path,
                              log: Callable[[str], None],
                              simular: bool = True, resume_path=None,
                              batch_size: int = BATCH_SIZE) -> Path:
    url = validar_url(url)
    if batch_size != BATCH_SIZE:
        # El tamaño queda fijo en la versión actual para que el checkpoint
        # tenga límites de bloque estables también al reanudar.
        raise ValueError("El tamaño de lote admitido actualmente es 10")
    carpeta_resultados.mkdir(parents=True, exist_ok=True)
    if resume_path:
        path = Path(resume_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        if (doc.get("flujo") != 1 or doc.get("url") != url or
                doc.get("modo") != "ejecucion"):
            raise ArisError("El reporte seleccionado no corresponde a este flujo y Link 1")
        if doc.get("estado") == "completado":
            raise ArisError("El lote figura completado; no se debe repetir")
        if simular:
            raise ArisError("Reanudar requiere modo real explícito, no Simulación")
        if not doc.get("lote") or not doc.get("procesos"):
            raise ArisError("El reporte no conserva un lote y procesos recuperables")
        records = [_reconstruir(d) for d in doc["procesos"]]
        doc.setdefault("historial_reanudaciones", []).append(
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        doc["batch_size"] = BATCH_SIZE
        doc["estado"] = "reanudando"
    else:
        path = carpeta_resultados / (
            "flujo1_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json"
        )
        records = []
        doc = {
            "flujo": 1, "url": url,
            "modo": "simulacion" if simular else "ejecucion",
            "inicio": datetime.now().astimezone().isoformat(timespec="seconds"),
            "estado": "iniciado", "lote": None,
            "procesos": [], "batch_size": BATCH_SIZE,
            "bloques": [], "errores_globales": [],
        }

    doc.setdefault("copias", {
        "total_esperado": len(records), "originales": 1,
        "copias_nuevas_esperadas": max(0, len(records) - 1),
        "copias_nuevas_observadas": 0,
        "copias_nuevas_validadas": 0,
        "estructura_total_validada": 1,
        "nombres": [], "incidencias": [],
    })

    def save():
        doc["copias"]["asignadas_a_procesos"] = sum(
            r.plantilla == "ok" for r in records
        )
        if records:
            doc["procesos"] = [r.as_dict() for r in records]
        else:
            # Durante el primer escaneo, el mapeo provisional vive en doc
            # hasta que la función devuelve todos los records al llamador.
            doc.setdefault("procesos", [])
        _resumen(doc, records or [
            _reconstruir(r) for r in doc["procesos"]
        ])
        _guardar(path, doc)

    save()
    try:
        with Aris(perfil, log) as aris:
            aris.abrir(url)
            raiz = _raiz_nombre(aris)
            if resume_path:
                if doc.get("raiz") != raiz:
                    raise ArisError(
                        "La raíz ARIS no coincide con la del reporte original"
                    )
                lote = doc["lote"]
                lote_url = aris.entrar(url, raiz, lote)
                log(
                    f"[REANUDACION][FLUJO1] Lote original={lote!r}; "
                    f"procesos={len(records)}, checkpoints conservados"
                )
                plantilla = doc.get("plantilla") or "00 Ejemplo"
                grupos = _grupos_lote(aris, lote_url, lote)
                if plantilla not in grupos and any(
                    r.plantilla == "pendiente" for r in records
                ):
                    raise ArisError(
                        "La plantilla ya no está en el lote, pero aún hay "
                        "estructuras sin preparar. No se puede copiar con seguridad."
                    )
            else:
                doc["raiz"] = raiz
                # Un listado inicial de la raíz; después se entra a cada
                # modelo una sola vez para mapearlo y obtener su título.
                # Durante el escaneo guardar el borrador directamente,
                # pues 'records' aún no se asignó en este método.
                plantilla, records = _preparar_registros(
                    aris, url, raiz, doc, log, simular,
                    lambda: _guardar(path, doc)
                )
                save()
                if simular:
                    doc["estado"] = "simulacion_correcta"
                    log("[BATCH][SIMULACION] Normalización global validada, "
                        "los bloques de 10 se planificaron sin modificar ARIS")
                    save()
                    return path
                lote = _nombre_lote()
                doc["lote"] = lote
                doc["plantilla"] = plantilla
                save()
                lote_url = aris.crear_grupo(url, raiz, lote)
                doc["lote_creado"] = True
                save()
                aris.mover_grupo(url, raiz, plantilla, lote_url, lote)
                doc["plantilla_movida"] = True
                save()
                log(f"[BATCH][INICIO] Lote {lote!r}; "
                    f"{len(records)} procesos / "
                    f"{len(list(bloques(records)))} bloques")

            # Conservar los contadores de la versión antigua, si existen.
            # En lotes nuevos se conocen al terminar el mapeo global.
            doc["copias"]["total_esperado"] = len(records)
            doc["copias"]["copias_nuevas_esperadas"] = max(0, len(records) - 1)
            if plantilla not in doc["copias"]["nombres"]:
                doc["copias"]["nombres"].insert(0, plantilla)
            save()

            # La plantilla original se reserva hasta el ÚLTIMO proceso.
            # Esto permite generar nuevas copias después de un bloque completo.
            for numero, chunk in bloques(records):
                doc["bloque_actual"] = numero
                save()
                if all(
                    r.plantilla == r.movimiento == "ok"
                    and r.informe in TERMINALES
                    and r.mapa in {"ok", "omitido"}
                    and r.diagrama in {"ok", "omitido"}
                    for r in chunk
                ):
                    log(f"[BATCH][OMITIDO] {numero}: ya estaba completo")
                    continue
                log(
                    f"[BATCH][INICIO] {numero}: {len(chunk)} procesos; "
                    f"progreso global={sum(r.informe in TERMINALES for r in records)}/"
                    f"{len(records)}"
                )
                # Fase A. Preparar únicamente las estructuras de este bloque.
                for r in chunk:
                    if _estado_incierto(r):
                        log(
                            f"[BATCH][REVISION] {r.base!r}: operación "
                            "anterior indeterminada, no se reintenta"
                        )
                        continue
                    grupos = _grupos_lote(aris, lote_url, lote)
                    _verificar_checkpoint_estructura(grupos, r)
                    if r.plantilla == "ok":
                        continue
                    disponibles = _grupos_libres(grupos, plantilla)
                    ultimo = r is records[-1]
                    usar_original = (
                        ultimo and not disponibles and
                        all(other.plantilla == "ok" for other in records[:-1])
                    )
                    if disponibles:
                        libre = disponibles[0]
                        log(f"[BATCH][REUSO] Grupo vacío previo {libre!r} -> {r.base!r}")
                        aris.validar_copia_completa(lote_url, lote, libre)
                        lote_url = aris.page.url
                        if libre not in doc["copias"]["nombres"]:
                            # Por ejemplo: copia 47 finalizada en ARIS pero
                            # ausente del JSON antiguo al quedar un modal.
                            doc["copias"]["nombres"].append(libre)
                            doc["copias"]["copias_nuevas_observadas"] += 1
                            doc["copias"]["copias_nuevas_validadas"] += 1
                            doc["copias"]["estructura_total_validada"] += 1
                            save()
                    elif usar_original:
                        libre = plantilla
                        log("[BATCH][PLANTILLA] Último proceso usará la plantilla original")
                    else:
                        if plantilla not in grupos:
                            raise ArisError(
                                "La plantilla original no está disponible para más copias"
                            )
                        # Registrar intención ANTES del pegado, pues podría
                        # completarse en ARIS aunque Playwright pierda el
                        # resultado. En un resume no reemitir este COPY/PASTE.
                        r.plantilla = "copiando"
                        save()
                        libre = aris.copiar_plantilla(
                            lote_url, lote, plantilla, url, raiz
                        )
                        r.estructura = libre
                        r.plantilla = "copiada"
                        if libre not in doc["copias"]["nombres"]:
                            doc["copias"]["nombres"].append(libre)
                            doc["copias"]["copias_nuevas_observadas"] += 1
                        save()
                        log(f"[BATCH][COPIA] Copia nueva confirmada {libre!r}")
                        aris.validar_copia_completa(lote_url, lote, libre)
                        lote_url = aris.page.url
                        doc["copias"]["copias_nuevas_validadas"] += 1
                        doc["copias"]["estructura_total_validada"] += 1
                        save()
                    r.estructura = libre
                    r.plantilla = "en_curso"
                    save()
                    aris.renombrar(lote_url, lote, libre, r.base)
                    r.plantilla = "ok"
                    save()
                    log(f"[BATCH][ESTRUCTURA OK] {r.base!r}")

                # Fase B. Mover solo los - Detalle de este bloque.
                for r in chunk:
                    if _estado_incierto(r) or r.plantilla != "ok":
                        continue
                    if r.movimiento == "ok":
                        continue
                    proc_url = aris.entrar(lote_url, lote, r.base)
                    procesos_url = aris.entrar(proc_url, r.base, PROCESOS)
                    r.movimiento = "en_curso"
                    save()
                    aris.mover_grupo(
                        url, raiz, r.carpeta_proceso, procesos_url, PROCESOS
                    )
                    r.movimiento = "ok"
                    save()
                    log(f"[BATCH][MOVIMIENTO OK] {r.base!r}")

                # Fase C. Lanzar informes solamente para este bloque.
                for r in chunk:
                    if (_estado_incierto(r) or r.movimiento != "ok"
                            or r.informe != "pendiente"):
                        continue
                    r.informe = "en_curso"
                    save()
                    proc_url = aris.entrar(lote_url, lote, r.base)
                    procesos_url = aris.entrar(proc_url, r.base, PROCESOS)
                    ok = aris.lanzar_informe(
                        procesos_url, PROCESOS, r.carpeta_proceso
                    )
                    r.informe = "ok" if ok else "fallo_aris"
                    if not ok:
                        r.errores.append("ARIS informó fallo, sin reintentos")
                    save()
                    log(f"[BATCH][INFORME] {r.base!r}: {r.informe}")

                # Fase D. Refrescar y renombrar los mapas de este mismo bloque.
                aris.recargar_raiz(url, raiz)
                lote_url = aris.entrar(url, raiz, lote)
                for r in chunk:
                    if _estado_incierto(r) or r.informe != "ok":
                        if r.informe == "fallo_aris":
                            r.mapa = r.diagrama = "omitido"
                            save()
                        continue
                    proc_url = aris.entrar(lote_url, lote, r.base)
                    mapas_url = aris.entrar(proc_url, r.base, MAPAS)
                    if r.mapa != "ok":
                        r.mapa = "en_curso"
                        save()
                        aris.renombrar(
                            mapas_url, MAPAS, MAPA_GENERADO, r.base
                        )
                        r.mapa = "ok"
                        save()
                    if r.diagrama != "ok":
                        mapa_url = aris.entrar(mapas_url, MAPAS, r.base)
                        r.diagrama = "en_curso"
                        save()
                        aris.renombrar(
                            mapa_url, r.base, MAPA_GENERADO, r.base, "modelo"
                        )
                        r.diagrama = "ok"
                        save()
                    log(f"[BATCH][MAPA OK] {r.base!r}")
                completos = sum(
                    r.plantilla == r.movimiento == r.informe
                    == r.mapa == r.diagrama == "ok" for r in chunk
                )
                log(
                    f"[BATCH][FIN] {numero}: completos={completos}/{len(chunk)}; "
                    f"procesados_global={sum(r.informe in TERMINALES for r in records)}"
                    f"/{len(records)}"
                )
                save()

            problemas = any(
                r.errores or _estado_incierto(r) or
                r.plantilla != "ok" or r.movimiento != "ok"
                or r.informe == "pendiente" for r in records
            )
            doc["estado"] = (
                "completado_con_errores" if problemas else "completado"
            )
    except Exception as exc:
        doc["estado"] = "interrumpido"
        doc.setdefault("errores_globales", []).append(
            f"{type(exc).__name__}: {exc}"
        )
        log(f"[BATCH][DETENIDO] {type(exc).__name__}: {exc}")
        log("[BATCH][CHECKPOINT] Los bloques completos están guardados; "
            "reanudar con este JSON, sin volver a emitir operaciones inciertas.")
    finally:
        doc["final"] = datetime.now().astimezone().isoformat(timespec="seconds")
        save()
    return path
