"""Orquestador liviano: inyectar un XML o procesar JSON corregido por GPT."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from modules.files import preparar_xml
from modules.aris import procesar_aris
from modules.reports import guardar_reporte
from modules.chatgpt import escribir_json
from config import DATA, CHROME_PROFILE

LOG = logging.getLogger(__name__)


def ejecutar_desde_aris(xml: Path, config: dict):
    """Entrada local antigua: permite inspección o 1 importación real de XML."""
    trabajo = {'source_id': 'local_prueba', 'etapa': 'preparacion',
               'estado': 'pendiente', 'fecha': datetime.now(timezone.utc).isoformat()}
    try:
        # Para inspección usar el archivo original. En ejecución copia validada
        # para dejar trazabilidad sin sobrescribir y no modificar el XML fuente.
        if config.get('modo_aris', 'inspeccionar') == 'ejecutar':
            preparado = preparar_xml(xml, DATA / 'xml')
        else:
            preparado = xml
        trabajo.update(etapa='aris', estado='preparado', archivo=preparado.name)
        def iniciado():
            trabajo['estado'] = 'aris_iniciado'
            trabajo['inicio_importacion'] = datetime.now(timezone.utc).isoformat()
            guardar_reporte(trabajo, DATA / 'reportes')
        resultado = procesar_aris(preparado, config, CHROME_PROFILE, al_iniciar=iniciado)
        trabajo.update(etapa='aris', estado=resultado['estado'], importado=resultado['importado'])
        if resultado.get('mensaje'):
            trabajo['mensaje'] = resultado['mensaje']
    except Exception as exc:
        trabajo.update(estado='requiere_revision', error=type(exc).__name__)
        LOG.exception('ARIS: no se pudo confirmar el resultado. No repetir sin revisar.')
    finally:
        ruta = guardar_reporte(trabajo, DATA / 'reportes')
        LOG.info('Estado ARIS=%s reporte=%s', trabajo['estado'], ruta)
    return trabajo


def _hash_xml(ruta: Path):
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _xml_del_registro(registro: dict):
    """Aceptar SOLO XML local en data/corregidos, jamás una ruta arbitraria."""
    ruta_str = registro.get('xml_corregido_ruta', '')
    if not ruta_str:
        raise ValueError('Falta xml_corregido_ruta')
    ruta = Path(ruta_str)
    if not ruta.is_absolute():
        ruta = (DATA.parent / ruta)
    ruta = ruta.resolve()
    carpeta = (DATA / 'corregidos').resolve()
    if not ruta.is_relative_to(carpeta):
        raise ValueError('El XML debe estar dentro de data/corregidos')
    if ruta.suffix.lower() != '.xml' or not ruta.is_file():
        raise ValueError('XML corregido inexistente')
    return ruta


def ejecutar_lote_aris(origen: Path, cfg: dict, limite=1, reintentar_solicitud=None):
    """Checkpoint separado. Procesa solo registros cuya corrección está finalizada."""
    if limite not in (1, 2):
        raise ValueError('Primera prueba: --limite 1 o 2')
    paquete = json.loads(origen.read_text(encoding='utf-8'))
    registros = paquete.get('registros')
    if not isinstance(registros, list):
        raise ValueError('El JSON debe contener registros[]')

    aptos = [r for r in registros if r.get('estado') == 'correccion_finalizada']
    ids = [r.get('id_solicitud') for r in aptos]
    if any(not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('ID de solicitud vacío o duplicado en registros corregidos')

    destino = DATA / 'exportaciones' / ('aris_' + origen.stem + '.json')
    if destino.exists():
        anteriores = json.loads(destino.read_text(encoding='utf-8'))['registros']
        if [r['id_solicitud'] for r in anteriores] != ids:
            raise ValueError('El checkpoint ARIS no corresponde a estos registros')
        trabajos = anteriores
    else:
        trabajos = []
        archivos = set()
        for r in aptos:
            ruta = _xml_del_registro(r)
            if ruta in archivos:
                raise ValueError('Varios registros apuntan al mismo archivo XML corregido')
            archivos.add(ruta)
            trabajos.append({
                'id_solicitud': r['id_solicitud'],
                'id_archivo_n5': r.get('id_archivo_n5', ruta.stem),
                'xml_corregido_ruta': str(ruta),
                'sha256_xml': _hash_xml(ruta),
                'estado_aris': 'pendiente',
                'intentos_importacion': 0,
                'revisiones_gpt_aris': 0
            })
        escribir_json(destino, trabajos)

    if reintentar_solicitud:
        if cfg.get('modo_aris') != 'ejecutar':
            raise ValueError('El reintento explícito requiere modo_aris=ejecutar')
        candidatos = [r for r in trabajos
                      if r['id_solicitud'] == reintentar_solicitud]
        if len(candidatos) != 1:
            raise ValueError('No existe un registro corregido con esa solicitud')
        elegido = candidatos[0]
        previo = elegido.get('estado_aris', 'desconocido')
        if previo == 'informe_completo':
            raise ValueError('No reimportar un informe ya completado')
        if previo not in ('requiere_revision', 'aris_iniciado', 'xml_rechazado',
                          'preparacion_fallida', 'pendiente'):
            raise ValueError(f'Estado no elegible para reintentar: {previo}')
        print(f'Solicitud={reintentar_solicitud} estado={previo}; '
              f'intentos previos={elegido.get("intentos_importacion", 0)}.')
        print('ANTES: comprobar en ARIS/P00 Importación XML que el modelo '
              'no se haya importado. De otro modo podrían generarse duplicados.')
        respuesta = input(f'Escribe NO_IMPORTADO {reintentar_solicitud} '
                          'para habilitar SOLO este registro: ')
        if respuesta != f'NO_IMPORTADO {reintentar_solicitud}':
            LOG.warning('Reintento no autorizado: checkpoint intacto')
            return destino
        marca = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        respaldo = destino.with_name(destino.stem + '_backup_' + marca + '.json')
        respaldo.write_bytes(destino.read_bytes())
        elegido.setdefault('historial_reintentos_aris', []).append({
            'fecha_utc': datetime.now(timezone.utc).isoformat(),
            'estado_previo': previo,
            'intentos_anteriores': elegido.get('intentos_importacion', 0),
            'confirmacion_manual': 'NO_IMPORTADO'
        })
        elegido.update(estado_aris='pendiente',
                       motivo='Reintento manual confirmado: no importado en ARIS')
        escribir_json(destino, trabajos)
        LOG.warning('Reintento autorizado SOLO para %s; respaldo=%s',
                    reintentar_solicitud, respaldo)

    # preparacion_fallida se asigna SOLO antes del lanzamiento del informe.
    # Es seguro repetir la selección; nunca reintentar aris_iniciado ni inciertos.
    elegibles = [
        r for r in trabajos
        if r.get('estado_aris') in ('pendiente', 'preparacion_fallida')
    ]
    if reintentar_solicitud:
        elegibles = [r for r in elegibles
                     if r['id_solicitud'] == reintentar_solicitud]
    pendientes = elegibles[:limite]
    LOG.info('ARIS: elegidos %s de %s preparados/pendientes (corregidos=%s)',
             len(pendientes), len(elegibles), len(trabajos))
    for index, trabajo in enumerate(pendientes, 1):
        # Revalidar antes del intento para detectar modificaciones externas.
        xml = _xml_del_registro(trabajo)
        if _hash_xml(xml) != trabajo['sha256_xml']:
            trabajo.update(estado_aris='requiere_revision',
                           motivo='El XML cambió respecto al checkpoint')
            escribir_json(destino, trabajos)
            continue

        LOG.info('ARIS %s/%s solicitud=%s; archivo=%s', index, len(pendientes),
                 trabajo['id_solicitud'], xml.name)
        iniciado = False

        def marca_inicio():
            nonlocal iniciado
            trabajo['estado_aris'] = 'aris_iniciado'
            trabajo['intentos_importacion'] += 1
            trabajo['inicio_utc'] = datetime.now(timezone.utc).isoformat()
            escribir_json(destino, trabajos)
            iniciado = True

        try:
            resultado = procesar_aris(xml, cfg, CHROME_PROFILE, al_iniciar=marca_inicio)
            estado = resultado['estado']
            if estado == 'informe_completo':
                trabajo.update(estado_aris='informe_completo',
                               motivo='Informe finalizado; verificar presencia del modelo en ARIS')
            elif estado == 'xml_rechazado':
                trabajo.update(estado_aris='xml_rechazado',
                               motivo='ARIS rechazó el XML; disponible una revisión con GPT')
            elif estado == 'cancelado':
                trabajo.update(estado_aris='pendiente', motivo='Cancelado antes de lanzar informe')
            elif estado == 'inspeccionado':
                trabajo.update(estado_aris='pendiente', motivo='Solo inspección; sin importar')
            elif estado == 'preparado_para_lanzar':
                trabajo.update(
                    estado_aris='pendiente',
                    motivo='Informe seleccionado por clic; sin lanzar ni importar'
                )
            else:
                trabajo.update(estado_aris='requiere_revision', motivo='Resultado ARIS incierto')
        except KeyboardInterrupt:
            trabajo.update(estado_aris='requiere_revision' if iniciado else 'pendiente',
                           motivo='Interrupción manual')
            LOG.warning('Interrumpido: se guarda estado sin duplicar importación')
            escribir_json(destino, trabajos)
            break
        except Exception as exc:
            trabajo.update(estado_aris='requiere_revision' if iniciado else 'preparacion_fallida',
                           motivo=type(exc).__name__)
            LOG.exception('ARIS: excepción; revisar antes de cualquier reintento')
        finally:
            escribir_json(destino, trabajos)
            LOG.info('ARIS solicitud=%s estado=%s', trabajo['id_solicitud'], trabajo['estado_aris'])
    LOG.info('Checkpoint ARIS: %s', destino)
    return destino
