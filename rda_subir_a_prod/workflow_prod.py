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
import time
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
                       nombre: str, origen_url: str,
                       codigos_esperados: set[str] | None = None,
                       continuar_vacios: bool = False) -> set[str]:
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
    codigos_esperados = codigos_esperados or set()
    # atencion_r puede albergar otras solicitudes. Nunca tocar las carpetas
    # ajenas, nunca crear otro grupo con un nombre ya presente.
    colisiones = [
        (item.name, item.kind) for item in encontrados
        if item.name in codigos_esperados and item.kind != "grupo"
    ]
    if colisiones:
        raise ArisError(
            "Hay objetos/modelos en el destino con un nombre N5 solicitado "
            f"(no son grupos): {colisiones!r}. No se creará otro objeto homónimo."
        )
    existentes = {
        item.name for item in encontrados
        if item.kind == "grupo" and item.name in codigos_esperados
    }
    ajenos = [
        (item.name, item.kind) for item in encontrados
        if item.name not in codigos_esperados
    ]
    aris.log(
        f"[PROD][DESTINO][INVENTARIO] total={len(encontrados)}; "
        f"N5 del lote existentes={sorted(existentes)!r}; "
        f"otros elementos conservados={len(ajenos)}; "
        f"N5 a crear={sorted(codigos_esperados - existentes)!r}"
    )
    if existentes and not continuar_vacios:
        raise ArisError(
            "Ya existen carpetas N5 del lote actual. "
            f"Coincidencias: {sorted(existentes)!r}. "
            "Marca 'usar N5 existentes SOLO si están vacíos' para "
            "verificarlas y reutilizarlas. Las carpetas ajenas al lote "
            "no bloquean la ejecución y no se modifican."
        )
    if existentes:
        aris.log(
            f"[PROD][REANUDACION] Validando {len(existentes)} N5 "
            "del lote que ya existen; no se volverán a crear"
        )
    # El nombre 'atencion_r' no basta: podría existir otro homónimo.
    # Verificamos la ruta usando el contenido del repositorio, no solo
    # #headerSearchBox, que en ARIS puede mostrar el padre del grupo
    # SELECCIONADO mientras la tabla muestra sus hijos.
    aris.log(
        f"[PROD][DESTINO] Contenido inicial de {nombre!r}: "
        f"{len(existentes)} N5 del lote preexistentes; "
        f"{len(ajenos)} elementos ajenos conservados; "
        "validando cadena 04 PERU / 90 OTROS MODELOS / atencion_r"
    )
    try:
        # Primer salto validado en la sesión real: atencion_r -> 90 OTROS MODELOS.
        aris.subir_nivel(DESTINO_WIZARD[1])
        aris.esperar_grupos(
            aris.page.url, DESTINO_WIZARD[1], {DESTINO_WIZARD[2]}
        )
        aris.log(
            "[PROD][DESTINO] Padre 90 OTROS MODELOS confirmado: "
            "contiene atencion_r"
        )

        # El siguiente clic puede mostrar Buscar en: PERU_PRODUCCION con
        # 04 PERU seleccionado (captura real 03/11/2026). En ese estado
        # el centro muestra los 8 HIJOS de 04 PERU, incluido 90 OTROS MODELOS.
        # Exigir _ready('04 PERU') genera un falso error y 60 s de espera.
        aris.log("[PROD][DESTINO] Subiendo desde 90 OTROS MODELOS")
        aris._toolbar("LEVEL_UP").click()  # navegación, NUNCA una escritura
        plazo = time.monotonic() + 90
        ultimo_estado = None
        siguiente_log = 0
        while time.monotonic() < plazo:
            try:
                cabecera = aris._grupo_actual()
                grupos = {
                    item.name for item in aris.listar() if item.kind == "grupo"
                }
            except ArisError as exc:
                # Durante el repintado de GWT el encabezado o la tabla pueden
                # desaparecer brevemente. Es lectura: se permite observar
                # de nuevo, NUNCA repetir el clic de navegación.
                if time.monotonic() >= siguiente_log:
                    aris.log(
                        "[PROD][DESTINO][ESPERA] ARIS está actualizando "
                        f"la vista: {exc}"
                    )
                    siguiente_log = time.monotonic() + 6
                aris.page.wait_for_timeout(350)
                continue
            # '90 OTROS MODELOS' no es hijo de sí mismo. Su aparición
            # confirma que la tabla ya está mostrando el nivel padre.
            if DESTINO_WIZARD[1] in grupos:
                aris.log(
                    "[PROD][DESTINO OK] Jerarquía confirmada por los hijos: "
                    f"{DESTINO_WIZARD[0]} contiene {DESTINO_WIZARD[1]}; "
                    f"cabecera ARIS={cabecera!r}"
                )
                break
            estado = (cabecera, tuple(sorted(grupos)))
            if estado != ultimo_estado or time.monotonic() >= siguiente_log:
                aris.log(
                    "[PROD][DESTINO][ESPERA] Navegando hacia 04 PERU; "
                    f"cabecera={cabecera!r}; grupos visibles={sorted(grupos)!r}"
                )
                ultimo_estado = estado
                siguiente_log = time.monotonic() + 6
            aris.page.wait_for_timeout(350)
        else:
            raise ArisError(
                "No se pudo confirmar que 04 PERU contiene 90 OTROS MODELOS. "
                f"Contexto: {aris.diagnostico()}"
            )
    finally:
        # Volver al enlace exacto del usuario, sin dejar la prueba en un padre.
        # No se crea ni publica nada en este procedimiento.
        aris.log("[PROD][DESTINO] Regresando al Link 1 (atencion_r)")
        # Es una validación de solo lectura: no hay portapapeles por preservar.
        # Una navegación completa evita otro cambio de hash que ARIS ignore.
        aris.abrir(destino_url, expected=nombre, reload=True)
        aris.log("[PROD][DESTINO] Link 1 restaurado y verificado")
    # Reutilizar solo destinos sin contenido. Se lee cada N5 exacto,
    # no basta con que la carpeta padre tenga el nombre correcto.
    try:
        for codigo in sorted(existentes):
            aris.log(f"[PROD][REANUDACION] Comprobando N5 vacío: {codigo}")
            aris.entrar(destino_url, nombre, codigo)
            hijos = aris.listar()
            if hijos:
                raise ArisError(
                    f"El destino N5 {codigo!r} ya contiene {len(hijos)} "
                    "elementos; se detiene para no sobrescribir producción."
                )
            aris.log(f"[PROD][REANUDACION OK] {codigo}: carpeta vacía")
    finally:
        if existentes:
            aris.abrir(destino_url, expected=nombre, reload=True)
    LOG.info(
        "Destino validado por niveles consecutivos: %s",
        " / ".join(DESTINO_WIZARD)
    )
    return existentes


def ejecutar_flujo_2(destino_url: str, origen_url: str, perfil: Path,
                     carpeta_resultados: Path, log: Callable[[str], None],
                     simular: bool = True,
                     continuar_n5_vacios: bool = False,
                     resume_path: Path | None = None) -> Path:
    """Punto de entrada del flujo 2, ahora por bloques recuperables de diez."""
    from workflow_prod_batch import ejecutar_flujo_2_batched
    return ejecutar_flujo_2_batched(
        destino_url, origen_url, perfil, carpeta_resultados, log,
        simular=simular, continuar_n5_vacios=continuar_n5_vacios,
        resume_path=resume_path,
    )
