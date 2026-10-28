"""Pipeline mínimo: Sheet -> GPT (1 reintento) -> ARIS -> GPT/ARIS final.

Cada pasada es secuencial. Rechazo explícito de ARIS habilita UN nuevo
ciclo de corrección; un estado incierto NO habilita reimportar a ciegas.
El reporte no incluye contenido XML ni credenciales y se actualiza por evento.
"""
import json
import logging
import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import DATA, CHROME_PROFILE
from modules.download_xmls import descargar_xmls, validar
from modules.chatgpt import procesar_registros, escribir_json, nombre_seguro
from modules.aris import SesionAris

LOG = logging.getLogger(__name__)
REINTENTABLES_GPT = {'correccion_fallida', 'incompleto', 'postprocesamiento_pendiente'}
CIERRE_OK = 'informe_completo'


def _fecha():
    return datetime.now(timezone.utc).isoformat()


def _guardar(ruta, doc):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporal.open('x', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, ruta)
    finally:
        temporal.unlink(missing_ok=True)


def _leer(ruta):
    return json.loads(Path(ruta).read_text(encoding='utf-8'))


def _seleccionar(origen, limite, ids):
    paquete = _leer(origen)
    registros = validar(paquete['registros'])
    if ids:
        if len(set(ids)) != len(ids):
            raise ValueError('IDs repetidos en la selección')
        por_id = {r['id_solicitud']: r for r in registros}
        faltantes = set(ids) - set(por_id)
        if faltantes:
            raise ValueError('IDs no presentes en la exportación')
        elegidos = [por_id[x] for x in ids]
    else:
        elegidos = registros[:limite]
    if not elegidos:
        raise ValueError('No hay registros seleccionados')
    if len(elegidos) > limite:
        raise ValueError('Los IDs solicitados superan --limite')
    nombres = [nombre_seguro(r['id_n5'], r['nombre_proceso']).casefold()
               for r in elegidos]
    if len(nombres) != len(set(nombres)):
        raise ValueError('Nombre de XML duplicado; no se pueden compartir rutas')
    return elegidos


def _preparar_lote(origen, elegidos):
    if origen.stem.startswith('lote_'):
        actuales = [r['id_solicitud'] for r in validar(_leer(origen)['registros'])]
        if actuales != [r['id_solicitud'] for r in elegidos]:
            raise ValueError('Lote existente no coincide con la selección')
        return origen
    destino = DATA / 'exportaciones' / ('lote_' + origen.stem + '_' +
                                       uuid.uuid4().hex[:8] + '.json')
    escribir_json(destino, elegidos)
    return destino


def _reporte_inicial(ruta, registros):
    if ruta.exists():
        doc = _leer(ruta)
        if [r['id_solicitud'] for r in doc['registros']] != [
            r['id_solicitud'] for r in registros
        ]:
            raise ValueError('Reporte anterior incompatible con este lote')
        return doc
    return {
        'version': 1,
        'inicio_utc': _fecha(),
        'estado': 'en_curso',
        'etapas_completadas': [],
        'registros': [{
            'id_solicitud': r['id_solicitud'],
            'id_n5': r['id_n5'],
            'nombre_proceso': r['nombre_proceso'],
            'estado_final': 'pendiente',
            'etapa_fallo': None,
            'motivo': None,
            'intentos_gpt': [],
            'intentos_aris': [],
        } for r in registros],
        'resumen': {}
    }


def _guardar_reporte(ruta, doc):
    registros = doc['registros']
    ok = sum(x['estado_final'] == 'informe_completo' for x in registros)
    pendientes = sum(x['estado_final'] in (
        'pendiente', 'en_curso', 'aris_iniciado'
    ) for x in registros)
    doc['resumen'] = {
        'total': len(registros), 'pasaron': ok,
        'no_pasaron': len(registros) - ok - pendientes, 'pendientes': pendientes
    }
    doc['actualizado_utc'] = _fecha()
    _guardar(ruta, doc)


def _marcar_etapa(ruta, doc, etapa):
    if etapa not in doc['etapas_completadas']:
        doc['etapas_completadas'].append(etapa)
    _guardar_reporte(ruta, doc)


def _sync_gpt(ruta, doc, procesados, etiqueta, solo=None):
    por_id = {r['id_solicitud']: r for r in _leer(procesados)['registros']}
    for reg in doc['registros']:
        sid = reg['id_solicitud']
        if solo is not None and sid not in solo:
            continue
        r = por_id[sid]
        pasado = {'pasada': etiqueta, 'estado': r.get('estado', 'pendiente'),
                  'motivo': r.get('motivo')}
        if not any(x['pasada'] == etiqueta for x in reg['intentos_gpt']):
            reg['intentos_gpt'].append(pasado)
        if r.get('estado') == 'correccion_finalizada':
            ruta_xml = Path(r.get('xml_corregido_ruta', '')).resolve()
            carpeta = (DATA / 'corregidos').resolve()
            if not ruta_xml.is_file() or not ruta_xml.is_relative_to(carpeta):
                raise ValueError('XML corregido fuera de la carpeta autorizada')
            reg['sha256_xml'] = hashlib.sha256(ruta_xml.read_bytes()).hexdigest()
            reg['xml_corregido_ruta'] = str(ruta_xml)
            reg['estado_final'] = 'pendiente'
            reg['etapa_fallo'] = None
            reg['motivo'] = None
        else:
            reg['estado_final'] = 'no_generado'
            reg['etapa_fallo'] = 'chatgpt'
            reg['motivo'] = r.get('motivo') or r.get('estado', 'sin XML')
    _guardar_reporte(ruta, doc)
    return por_id


def _pasada_gpt(lote, cfg, ruta, doc, etapa, etiqueta, solicitudes,
                reintentar=False):
    if etapa in doc['etapas_completadas'] or not solicitudes:
        return
    LOG.info('Pipeline %s: GPT de %d XML(s)', etapa, len(solicitudes))
    try:
        procesados = procesar_registros(
            lote, cfg, DATA, limite=None, solicitudes=solicitudes,
            reintentar=reintentar, intento=etiqueta, confirmar=False
        )
    except Exception:
        LOG.exception('La pasada %s terminó con excepción; conservar checkpoints', etapa)
        raise
    _sync_gpt(ruta, doc, procesados, etiqueta, set(solicitudes))
    _marcar_etapa(ruta, doc, etapa)


def _pasada_aris(cfg, ruta, doc, etapa, etiqueta, seleccion, por_id):
    """No repetir un informe ya lanzado, ni aunque haya timeout/interrupción."""
    if etapa in doc['etapas_completadas']:
        return
    candidatos = []
    for reg in doc['registros']:
        sid = reg['id_solicitud']
        if sid not in seleccion:
            continue
        if por_id[sid].get('estado') != 'correccion_finalizada':
            continue
        if any(x['pasada'] == etiqueta for x in reg['intentos_aris']):
            continue
        if reg['estado_final'] in ('informe_completo', 'aris_iniciado', 'requiere_revision'):
            continue
        candidatos.append(reg)
    LOG.info('Pipeline %s: ARIS de %d XML(s), en serie', etapa, len(candidatos))
    if not candidatos:
        _marcar_etapa(ruta, doc, etapa)
        return
    with SesionAris(cfg, CHROME_PROFILE) as sesion:
        for i, reg in enumerate(candidatos, 1):
            sid = reg['id_solicitud']
            xml = Path(por_id[sid]['xml_corregido_ruta']).resolve()
            if not xml.is_relative_to((DATA / 'corregidos').resolve()):
                raise ValueError('XML fuera de la carpeta corregidos')
            if hashlib.sha256(xml.read_bytes()).hexdigest() != reg.get('sha256_xml'):
                raise ValueError('XML diferente al checkpoint GPT')
            entrada = {'pasada': etiqueta, 'estado': 'preparando',
                       'fecha_utc': _fecha()}
            reg['intentos_aris'].append(entrada)
            reg['estado_final'] = 'en_curso'
            _guardar_reporte(ruta, doc)

            def iniciado():
                entrada['estado'] = 'aris_iniciado'
                entrada['lanzado_utc'] = _fecha()
                reg['estado_final'] = 'aris_iniciado'
                _guardar_reporte(ruta, doc)

            try:
                resultado = sesion.importar(xml, al_iniciar=iniciado)
                entrada['estado'] = resultado['estado']
                entrada['mensaje'] = resultado.get('mensaje')
                reg['estado_final'] = resultado['estado']
                reg['motivo'] = resultado.get('mensaje')
                reg['etapa_fallo'] = (
                    None if resultado['estado'] == CIERRE_OK else 'aris'
                )
                LOG.info('ARIS %s/%s solicitud=%s estado=%s',
                         i, len(candidatos), sid, resultado['estado'])
            except KeyboardInterrupt:
                reg['estado_final'] = 'requiere_revision'
                reg['etapa_fallo'] = 'aris'
                reg['motivo'] = 'Interrupción; verificar en ARIS antes de repetir'
                entrada['estado'] = 'requiere_revision'
                _guardar_reporte(ruta, doc)
                raise
            except Exception as exc:
                # Importación posiblemente iniciada: NO reintentar por defecto.
                reg['estado_final'] = 'requiere_revision'
                reg['etapa_fallo'] = 'aris'
                reg['motivo'] = type(exc).__name__
                entrada['estado'] = 'requiere_revision'
                LOG.exception('ARIS: resultado incierto para %s; sin reimportación', sid)
                if entrada.get('lanzado_utc'):
                    # Nunca avanzar mientras el informe anterior podría seguir ejecutándose.
                    raise RuntimeError(
                        'Importación ARIS iniciada sin resultado confirmable; '
                        'detener lote y revisar manualmente antes de continuar'
                    ) from exc
            finally:
                _guardar_reporte(ruta, doc)
    _marcar_etapa(ruta, doc, etapa)


def ejecutar_pipeline(cfg, limite=4, ids=None, archivo=None):
    """Para prueba controlada: --limite 4 [--ids ...] (sin paralelizar ARIS)."""
    if not isinstance(limite, int) or limite < 1:
        raise ValueError('El límite debe ser positivo')
    if cfg.get('modo_chatgpt') != 'ejecutar' or cfg.get('modo_aris') != 'ejecutar':
        raise ValueError('Pipeline exige modo_chatgpt=ejecutar y modo_aris=ejecutar')
    if not cfg.get('aris_grupo_informes'):
        raise ValueError('Especificar aris_grupo_informes antes de ejecutar')
    if archivo is None:
        nombre = datetime.now(timezone.utc).strftime('registros_%Y%m%dT%H%M%S_%fZ.json')
        archivo = descargar_xmls(
            cfg, DATA / 'exportaciones' / nombre, CHROME_PROFILE
        )
    origen = Path(archivo).resolve()
    elegidos = _seleccionar(origen, limite, ids)
    lote = _preparar_lote(origen, elegidos)
    reporte = DATA / 'reportes' / ('pipeline_' + lote.stem + '.json')
    doc = _reporte_inicial(reporte, elegidos)
    _guardar_reporte(reporte, doc)
    inciertos = [
        r['id_solicitud'] for r in doc['registros']
        if any(x.get('lanzado_utc') and x['estado'] in (
            'aris_iniciado', 'requiere_revision', 'preparando'
        ) for x in r['intentos_aris'])
    ]
    if inciertos:
        raise RuntimeError(
            'Importaciones ARIS de resultado incierto en checkpoint: '
            + ', '.join(inciertos) +
            '. Verificar manualmente antes de reanudar; no reimportar.'
        )
    seleccion = [r['id_solicitud'] for r in elegidos]

    print('Se procesarán', len(seleccion), 'XML(s).')
    print('ANTES de continuar, comprueba que NO hayan sido importados en ARIS.')
    print('Se usarán la cuenta y carpeta configuradas; informe:', cfg.get('aris_report_name'))
    print('Carpeta:', cfg.get('aris_grupo_informes'))
    if input(f'Escribe EJECUTAR {len(seleccion)} para autorizar el lote: ').strip() != (
        f'EJECUTAR {len(seleccion)}'
    ):
        LOG.warning('Lote no autorizado; no se enviaron XML ni informes')
        return reporte

    procesados = DATA / 'exportaciones' / ('procesados_' + lote.stem + '.json')
    try:
        _pasada_gpt(lote, cfg, reporte, doc, 'gpt_inicial',
                    'inicial', seleccion)
        actuales = _leer(procesados)['registros']
        fallos = [r['id_solicitud'] for r in actuales
                  if r.get('estado') in REINTENTABLES_GPT]
        _pasada_gpt(lote, cfg, reporte, doc, 'gpt_reintento',
                    'gpt_reintento_1', fallos, reintentar=True)
        por_id = {r['id_solicitud']: r for r in _leer(procesados)['registros']}
        _pasada_aris(cfg, reporte, doc, 'aris_inicial',
                     'original', set(seleccion), por_id)
        rechazados = {
            r['id_solicitud'] for r in doc['registros']
            if any(x['pasada'] == 'original' and x['estado'] == 'xml_rechazado'
                   for x in r['intentos_aris'])
        }
        if rechazados:
            _pasada_gpt(lote, cfg, reporte, doc, 'gpt_revision_aris',
                        'aris_revision_1', rechazados, reintentar=True)
            actuales = _leer(procesados)['registros']
            fallos = {
                r['id_solicitud'] for r in actuales if
                r['id_solicitud'] in rechazados
                and r.get('estado') in REINTENTABLES_GPT
            }
            _pasada_gpt(lote, cfg, reporte, doc, 'gpt_revision_aris_reintento',
                        'aris_revision_2', fallos, reintentar=True)
            por_id = {r['id_solicitud']: r for r in _leer(procesados)['registros']}
            # Segunda importación: no existe un tercer intento.
            _pasada_aris(cfg, reporte, doc, 'aris_final',
                         'final', rechazados, por_id)
        else:
            LOG.info('No hubo XML explícitamente rechazados por ARIS')
        doc['estado'] = 'finalizado'
        doc['fin_utc'] = _fecha()
    except KeyboardInterrupt:
        doc['estado'] = 'interrumpido'
        LOG.warning('Interrupción; conserva checkpoint. Sin reenvíos automáticos')
    except Exception:
        doc['estado'] = 'requiere_revision'
        LOG.exception('Pipeline interrumpido; revisar estado antes de continuar')
        raise
    finally:
        _guardar_reporte(reporte, doc)
        LOG.info('Reporte final/parcial: %s; resumen=%s', reporte, doc['resumen'])
        print('Reporte:', reporte)
        print('Resumen:', doc['resumen'])
    return reporte

