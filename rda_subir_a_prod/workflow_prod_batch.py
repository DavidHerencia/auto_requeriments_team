"""Flujo 2 en bloques de diez, con journal de publicación y reanudación.

Se reutilizan el preflight de estructuras, selección del informe y
confirmaciones ARIS ya validados. Nunca se vuelve a publicar un proceso
marcado ok, fallo o con resultado incierto en el checkpoint.
"""
from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Callable

from aris import Aris, ArisError, validar_url
from publicacion import Publicador, DESTINO_WIZARD
from workflow import _guardar, _raiz_nombre
from workflow_batch import BATCH_SIZE, bloques
from workflow_prod import (
    RegistroProd, _plan, _verificar_preparado, _verificar_destino,
)


def _leer(raw):
    permitidos = {x.name for x in fields(RegistroProd)}
    return RegistroProd(**{k: v for k, v in raw.items() if k in permitidos})


def _estado_bloqueado(r):
    return (
        r.publicacion in {
            "en_curso", "no_confirmado", "fallo_aris", "error", "ok"
        }
        or r.destino in {"no_confirmado", "en_curso", "error"}
    )


def _resumen(doc, registros):
    doc["resumen"] = {
        "total": len(registros),
        "bloques": len(list(bloques(registros))),
        "destinos_creados": sum(x.destino == "creado" for x in registros),
        "destinos_reutilizados": sum(
            x.destino == "reutilizado_vacio" for x in registros
        ),
        "informes_ok": sum(x.publicacion == "ok" for x in registros),
        "informes_fallidos": sum(
            x.publicacion == "fallo_aris" for x in registros
        ),
        "informes_no_confirmados": sum(
            x.publicacion in {"no_confirmado", "en_curso", "error"}
            for x in registros
        ),
        "pendientes": sum(
            x.publicacion == "pendiente" for x in registros
        ),
    }


def ejecutar_flujo_2_batched(
    destino_url: str, origen_url: str, perfil: Path,
    carpeta_resultados: Path, log: Callable[[str], None],
    simular: bool = True, continuar_n5_vacios: bool = False,
    resume_path=None,
) -> Path:
    destino_url = validar_url(destino_url)
    origen_url = validar_url(origen_url)
    carpeta_resultados.mkdir(parents=True, exist_ok=True)
    if resume_path:
        path = Path(resume_path)
        doc = json.loads(path.read_text(encoding="utf-8"))
        if (doc.get("flujo") != 2 or
                doc.get("link1_destino") != destino_url or
                doc.get("link2_origen") != origen_url):
            raise ArisError("El JSON de reanudación no corresponde a los links actuales")
        if doc.get("estado") == "completado":
            raise ArisError("El lote ya figura completado; no se republicará")
        if simular:
            raise ArisError("La reanudación requiere ejecución real confirmada")
        registros = [_leer(raw) for raw in doc.get("registros", [])]
        if not registros:
            raise ArisError("El reporte no contiene procesos recuperables")
        doc.setdefault("historial_reanudaciones", []).append(
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        doc["estado"] = "reanudando"
    else:
        path = carpeta_resultados / (
            "flujo2_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json"
        )
        registros = []
        doc = {
            "flujo": 2,
            "estado": "iniciado",
            "simulacion": simular,
            "continuar_n5_vacios": continuar_n5_vacios,
            "inicio": datetime.now().astimezone().isoformat(timespec="seconds"),
            "link1_destino": destino_url, "link2_origen": origen_url,
            "ruta_publicacion_esperada": list(DESTINO_WIZARD),
            "batch_size": BATCH_SIZE, "registros": [], "errores_globales": [],
        }

    def save():
        doc["registros"] = [r.as_dict() for r in registros]
        _resumen(doc, registros)
        _guardar(path, doc)

    save()
    try:
        with Aris(perfil, log) as aris:
            aris.abrir(origen_url)
            origen_nombre = _raiz_nombre(aris)
            if resume_path:
                if doc.get("origen_nombre") != origen_nombre:
                    raise ArisError("La carpeta origen cambió respecto al JSON")
                log(
                    f"[PROD][REANUDACION] {len(registros)} procesos del checkpoint; "
                    f"{sum(x.publicacion == 'ok' for x in registros)} ya publicados; "
                    "los resultados inciertos no se repetirán"
                )
            else:
                registros = _plan(aris, origen_url, origen_nombre, log)
                save()
            doc["origen_nombre"] = origen_nombre

            # Validación global de todos los procesos aún pendientes.
            pendientes = [r for r in registros
                          if r.publicacion == "pendiente"
                          and r.destino not in {
                              "en_curso", "no_confirmado", "error"
                          }]
            for index, r in enumerate(pendientes, 1):
                log(f"[PROD][PREFLIGHT] {index}/{len(pendientes)} {r.base}")
                _verificar_preparado(
                    aris, origen_url, origen_nombre, r, log
                )
                save()

            aris.abrir(destino_url)
            destino_nombre = _raiz_nombre(aris)
            # SOLO códigos pendientes; los ya publicados/ambiguos no se
            # tocan ni se exige que sus carpetas estén vacías.
            existentes = _verificar_destino(
                aris, destino_url, destino_nombre, origen_url,
                codigos_esperados={r.codigo for r in pendientes},
                continuar_vacios=(continuar_n5_vacios or bool(resume_path)),
            )
            doc["destino_nombre"] = destino_nombre
            doc["n5_reutilizables"] = sorted(existentes)
            save()
            log(
                f"[PROD][PLAN BATCH] total={len(registros)}, "
                f"pendientes={len(pendientes)}, "
                f"bloques={len(list(bloques(registros)))} de max. 10"
            )
            if simular:
                doc["estado"] = "simulacion_correcta"
                save()
                log("[PROD][SIMULACION] No se crearon carpetas ni informes")
                return path

            publicador = Publicador(aris, log)
            for numero, chunk in bloques(registros):
                doc["bloque_actual"] = numero
                save()
                activos = [
                    r for r in chunk
                    if r.publicacion == "pendiente"
                    and r.destino not in {
                        "en_curso", "no_confirmado", "error"
                    }
                ]
                if not activos:
                    log(
                        f"[PROD][BATCH][OMITIDO] {numero}: "
                        "todos ya publicados o pendientes de revisión"
                    )
                    continue
                log(
                    f"[PROD][BATCH][INICIO] {numero}: "
                    f"{len(activos)} procesos pendientes de {len(chunk)}"
                )
                # Fase A: únicamente las carpetas N5 del bloque.
                for i, r in enumerate(activos, 1):
                    if r.codigo in existentes:
                        r.destino = "reutilizado_vacio"
                        save()
                        log(
                            f"[PROD][BATCH][REUSO] {numero} {i}/{len(activos)} "
                            f"N5 {r.codigo} vacía, no se crea de nuevo"
                        )
                        continue
                    if r.destino not in {"pendiente", "creado", "reutilizado_vacio"}:
                        raise ArisError(
                            f"Estado inesperado del destino {r.codigo}: {r.destino}"
                        )
                    if r.destino in {"creado", "reutilizado_vacio"}:
                        raise ArisError(
                            f"El reporte registra {r.codigo} creado pero la "
                            "carpeta no figura entre las existentes verificadas. "
                            "No se repetirá Crear grupo."
                        )
                    r.destino = "en_curso"
                    save()
                    log(
                        f"[PROD][BATCH][CREACION] {numero} {i}/{len(activos)}: "
                        f"{r.codigo}"
                    )
                    aris.crear_grupo(destino_url, destino_nombre, r.codigo)
                    r.destino = "creado"
                    save()
                    log(f"[PROD][BATCH][CREACION OK] {r.codigo}")

                # Comprobar por lectura todos los N5 de este bloque.
                aris.esperar_grupos(
                    destino_url, destino_nombre,
                    {r.codigo for r in activos}
                )
                log(
                    f"[PROD][BATCH][DESTINOS OK] {numero}: "
                    f"{len(activos)} N5 presentes"
                )

                # Fase B: ejecutar informes y confirmar resultado del bloque.
                for i, r in enumerate(activos, 1):
                    r.publicacion = "en_curso"
                    save()
                    log(
                        f"[PROD][BATCH][INFORME] {numero} {i}/{len(activos)} "
                        f"{r.codigo} -> {'/'.join(DESTINO_WIZARD)}/{r.codigo}"
                    )
                    proc_url = aris.entrar(
                        origen_url, origen_nombre, r.base
                    )
                    ok = publicador.publicar(
                        proc_url, r.base, r.detalle, r.codigo, r.base
                    )
                    r.publicacion = "ok" if ok else "fallo_aris"
                    if not ok:
                        r.errores.append(
                            "ARIS informó fallo del informe; no se reintenta"
                        )
                    save()
                    log(
                        f"[PROD][BATCH][RESULTADO] {r.codigo}: {r.publicacion}"
                    )
                log(
                    f"[PROD][BATCH][FIN] {numero}: "
                    f"publicados_ok={sum(x.publicacion == 'ok' for x in chunk)}, "
                    f"fallidos={sum(x.publicacion == 'fallo_aris' for x in chunk)}, "
                    f"total_global_ok={sum(x.publicacion == 'ok' for x in registros)}"
                )
                save()

            errores = any(
                x.publicacion != "ok" or x.errores for x in registros
            )
            doc["estado"] = (
                "completado_con_errores" if errores else "completado"
            )
    except Exception as exc:
        doc["estado"] = "interrumpido"
        doc.setdefault("errores_globales", []).append(
            f"{type(exc).__name__}: {exc}"
        )
        log(f"[PROD][BATCH][DETENIDO] {type(exc).__name__}: {exc}")
        log(
            "[PROD][BATCH][CHECKPOINT] Las publicaciones confirmadas "
            "no se repetirán al reanudar este JSON"
        )
    finally:
        doc["final"] = datetime.now().astimezone().isoformat(timespec="seconds")
        save()
    return path
