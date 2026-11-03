"""Flujo 2: carpetas destino y publicación de modelos preparados en ARIS.

Destino (link 1) -> atencion_r, inicialmente vacío, solo códigos N5.
Origen (link 2) -> estructuras validadas del flujo 1.

Antes del primer informe: preflight de TODOS los procesos y creación de
TODOS los grupos destino. Si una escritura es ambigua, se detiene sin retry.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from aris import (
    Aris, ArisError, NoConfirmado, PROCESOS, MAPAS,
    validar_url, nombre_proceso,
)
from publicacion import Publicador, DESTINO_WIZARD
from workflow import _guardar, _raiz_nombre

LOG = logging.getLogger(__name__)
CODIGO_RE = re.compile(r"^(\d+(?:\.\d+)+_P\d+)(?:\s|$)", re.I)


def extraer_codigo(nombre: str) -> str:
    """Solo extrae el código N5 al inicio: '3.3.1.1_P03 Proceso' -> '3.3.1.1_P03'."""
    match = CODIGO_RE.match(nombre.strip())
    if not match:
        raise ArisError(
            f"La carpeta {nombre!r} no empieza con un código tipo '3.3.1.1_P03'"
        )
    return match.group(1)


@dataclass
class RegistroProd:
    base: str
    codigo: str
    detalle: str = ""
    destino: str = "pendiente"
    publicacion: str = "pendiente"
    errores: list[str] = field(default_factory=list)

    def as_dict(self):
        return vars(self).copy()


def _plan(aris: Aris, origen_url: str, origen_nombre: str,
          log: Callable[[str], None]) -> list[RegistroProd]:
    aris.abrir(origen_url, expected=origen_nombre)
    grupos = sorted(
        (x.name for x in aris.listar() if x.kind == "grupo"),
        key=str.casefold
    )
    if not grupos:
        raise ArisError("La carpeta de origen (link 2) está vacía")
    registros = []
    for base in grupos:
        code = extraer_codigo(base)
        registros.append(RegistroProd(base=base, codigo=code))
    repetidos = sorted({
        r.codigo for r in registros
        if sum(x.codigo.casefold() == r.codigo.casefold() for x in registros) > 1
    })
    if repetidos:
        raise ArisError(
            f"Varias carpetas de origen comparten código N5: {repetidos!r}"
        )
    log(f"[PROD][MAPEO] {len(registros)} procesos encontrados en el link 2")
    for i, r in enumerate(registros, 1):
        log(f"[PROD][MAPEO] {i}/{len(registros)}: {r.codigo} -> {r.base!r}")
    return registros


def _verificar_preparado(aris: Aris, origen_url: str, origen_nombre: str,
                        r: RegistroProd, log):
    """Solo lectura: verifica toda la estructura antes de preparar producción."""
    url_proceso = aris.entrar(origen_url, origen_nombre, r.base)
    subs = [x.name for x in aris.listar() if x.kind == "grupo"]
    faltantes = {PROCESOS, MAPAS} - set(subs)
    if faltantes:
        raise ArisError(f"{r.base!r} no tiene {sorted(faltantes)!r}")
    proc_url = aris.entrar(url_proceso, r.base, PROCESOS)
    details = [x.name for x in aris.listar()
               if x.kind == "grupo" and
               re.search(r"\s*-\s*Detalle\s*$", x.name, re.I)]
    if len(details) != 1:
        raise ArisError(
            f"{r.base!r}/00 Procesos requiere exactamente 1 grupo - Detalle "
            f"(encontrados={details!r})"
        )
    detalle = details[0]
    if nombre_proceso(detalle) != r.base:
        raise ArisError(
            f"El grupo detalle {detalle!r} no corresponde a {r.base!r}"
        )
    detail_url = aris.entrar(proc_url, PROCESOS, detalle)
    detail_models = [x.name for x in aris.listar() if x.kind == "modelo"]
    if detail_models != [r.base]:
        raise ArisError(
            f"El grupo - Detalle de {r.base!r} debería tener un modelo "
            f"llamado exactamente igual; encontró {detail_models!r}"
        )

    mapas_url = aris.entrar(url_proceso, r.base, MAPAS)
    map_folders = [x.name for x in aris.listar() if x.kind == "grupo"]
    if map_folders != [r.base]:
        raise ArisError(
            f"{r.base!r}/01 Mapas y Catálogos necesita el grupo {r.base!r}; "
            f"encontró {map_folders!r}"
        )
    map_url = aris.entrar(mapas_url, MAPAS, r.base)
    map_models = [x.name for x in aris.listar() if x.kind == "modelo"]
    if map_models != [r.base]:
        raise ArisError(
            f"El mapa de roles de {r.base!r} debe llamarse igual "
            f"y ser un modelo; encontró {map_models!r}"
        )
    r.detalle = detalle
    log(f"[PROD][ESTRUCTURA OK] {r.codigo}: procesos, detalle y mapa verificados")


def _verificar_destino(aris: Aris, destino_url: str,
                       nombre: str, origen_url: str):
    if destino_url == origen_url:
        raise ArisError("Los links 1 y 2 apuntan a la misma carpeta")
    # En el asistente real capturado, el destino es exactamente:
    # 04 PERU / 90 OTROS MODELOS / atencion_r / <código>
    # Si link1 apunta a otra ubicación NO tenemos evidencia de equivalencia.
    if nombre != DESTINO_WIZARD[-1]:
        raise ArisError(
            f"El asistente publica en {'/'.join(DESTINO_WIZARD)!r}, "
            f"pero el link 1 abre {nombre!r}. "
            "No se publica en un destino potencialmente distinto."
        )
    aris.abrir(destino_url, expected=nombre)
    encontrados = aris.listar()
    if encontrados:
        raise ArisError(
            "El destino (link 1) debe estar inicialmente vacío para este lote. "
            f"Hay {len(encontrados)} elementos. No se sobrescribirá nada."
        )
    # El nombre 'atencion_r' no basta: puede repetirse en otro repositorio.
    # Confirmar su padre y abuelo usando solo la navegación nativa.
    try:
        aris.subir_nivel(DESTINO_WIZARD[1])
        aris.subir_nivel(DESTINO_WIZARD[0])
    finally:
        aris.abrir(destino_url, expected=nombre)
    LOG.info(
        "Destino validado en la jerarquía %s",
        " / ".join(DESTINO_WIZARD)
    )


def ejecutar_flujo_2(destino_url: str, origen_url: str, perfil: Path,
                     carpeta_resultados: Path, log: Callable[[str], None],
                     simular: bool = True) -> Path:
    destino_url = validar_url(destino_url)
    origen_url = validar_url(origen_url)
    carpeta_resultados.mkdir(parents=True, exist_ok=True)
    ident = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = carpeta_resultados / f"flujo2_{ident}.json"
    doc = {
        "flujo": 2,
        "estado": "iniciado",
        "simulacion": simular,
        "inicio": datetime.now().astimezone().isoformat(timespec="seconds"),
        "link1_destino": destino_url,
        "link2_origen": origen_url,
        "ruta_publicacion_esperada": list(DESTINO_WIZARD),
        "registros": [],
        "errores_globales": [],
    }
    registros: list[RegistroProd] = []

    def save():
        _guardar(path, doc)

    def update():
        doc["registros"] = [r.as_dict() for r in registros]
        save()

    save()
    try:
        with Aris(perfil, log) as aris:
            aris.abrir(origen_url)
            origen_nombre = _raiz_nombre(aris)
            registros = _plan(aris, origen_url, origen_nombre, log)
            update()
            # Preflight completo sin escrituras, para no descubrir una
            # estructura rota después de haber creado carpetas en producción.
            for i, r in enumerate(registros, 1):
                log(f"[PROD][PREFLIGHT] {i}/{len(registros)} {r.base}")
                _verificar_preparado(
                    aris, origen_url, origen_nombre, r, log
                )
                update()
            aris.abrir(destino_url)
            destino_nombre = _raiz_nombre(aris)
            _verificar_destino(
                aris, destino_url, destino_nombre, origen_url
            )
            doc["origen_nombre"] = origen_nombre
            doc["destino_nombre"] = destino_nombre
            update()
            log(
                f"[PROD][PLAN] {len(registros)} carpetas N5 nuevas bajo "
                f"{destino_nombre!r}; {len(registros)} informes de publicación"
            )
            if simular:
                doc["estado"] = "simulacion_correcta"
                log("[PROD][SIMULACION] Preflight completado sin modificar ARIS")
            else:
                # Crear todos los destinos primero; exactamente 1 clic por grupo.
                for i, r in enumerate(registros, 1):
                    log(
                        f"[PROD][CREACION] {i}/{len(registros)}: "
                        f"{destino_nombre}/{r.codigo}"
                    )
                    try:
                        aris.crear_grupo(
                            destino_url, destino_nombre, r.codigo
                        )
                        r.destino = "creado"
                        update()
                        log(f"[PROD][CREACION OK] {r.codigo}")
                    except Exception as exc:
                        r.destino = "no_confirmado"
                        r.errores.append(
                            f"creación: {type(exc).__name__}: {exc}"
                        )
                        update()
                        log(f"[PROD][CREACION DETENIDA] {aris.diagnostico()}")
                        raise

                # Verificar todas las carpetas antes de la primera publicación.
                aris.esperar_grupos(
                    destino_url, destino_nombre,
                    {r.codigo for r in registros}
                )
                log("[PROD][DESTINOS OK] Todas las carpetas N5 visibles")
                publicador = Publicador(aris, log)
                for i, r in enumerate(registros, 1):
                    log(
                        f"[PROD][INFORME] {i}/{len(registros)} "
                        f"{r.codigo} -> {'/'.join(DESTINO_WIZARD)}/{r.codigo}"
                    )
                    r.publicacion = "en_curso"
                    update()
                    try:
                        proc_url = aris.entrar(
                            origen_url, origen_nombre, r.base
                        )
                        ok = publicador.publicar(
                            proc_url, r.base, r.detalle, r.codigo, r.base
                        )
                        r.publicacion = "ok" if ok else "fallo_aris"
                        if not ok:
                            r.errores.append(
                                "ARIS indicó que el informe fue cancelado o falló"
                            )
                        update()
                        log(
                            f"[PROD][INFORME RESULTADO] "
                            f"{r.codigo}: {r.publicacion}"
                        )
                    except Exception as exc:
                        r.publicacion = "no_confirmado"
                        r.errores.append(
                            f"publicación: {type(exc).__name__}: {exc}"
                        )
                        doc["ultimo_diagnostico"] = aris.diagnostico()
                        update()
                        log(
                            "[PROD][DETENIDO] Publicación ambigua: "
                            "no habrá segundo intento automático"
                        )
                        raise
                doc["estado"] = (
                    "completado_con_errores" if any(
                        r.errores for r in registros
                    ) else "completado"
                )
    except Exception as exc:
        doc["estado"] = "interrumpido"
        doc["errores_globales"].append(f"{type(exc).__name__}: {exc}")
        LOG.exception("Flujo 2 interrumpido")
        log(f"[PROD][ERROR] {type(exc).__name__}: {exc}")
    finally:
        doc["final"] = datetime.now().astimezone().isoformat(timespec="seconds")
        doc["resumen"] = {
            "total": len(registros),
            "destinos_creados": sum(r.destino == "creado" for r in registros),
            "informes_ok": sum(r.publicacion == "ok" for r in registros),
            "informes_fallidos": sum(
                r.publicacion == "fallo_aris" for r in registros
            ),
            "informes_no_confirmados": sum(
                r.publicacion == "no_confirmado" for r in registros
            )
        }
        update()
    return path
