"""Prueba simulada de 4 diagramas: ninguna conexión real ni secretos."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pipeline


class TestPipeline(unittest.TestCase):
    def test_lote_cuatro_xml_con_dos_niveles_de_reintento(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            nombres = ['S1', 'S2', 'S3', 'S4']
            registros = [
                {'id_solicitud': id_, 'id_n5': f'N{i}',
                 'nombre_proceso': f'Proceso {i}', 'xml': '<definitions/>'}
                for i, id_ in enumerate(nombres, 1)
            ]
            llamadas_gpt, llamadas_aris = [], []
            estado = {r['id_solicitud']: dict(r) for r in registros}

            def descargar(cfg, destino, perfil):
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(
                    json.dumps({'version': 1, 'registros': registros}),
                    encoding='utf-8'
                )
                return destino

            def corregir(origen, cfg, base, **kwargs):
                etiqueta = kwargs['intento']
                ids = list(kwargs['solicitudes'])
                llamadas_gpt.append((etiqueta, ids))
                for id_ in ids:
                    row = estado[id_]
                    ok = True
                    if etiqueta == 'inicial' and id_ == 'S2':
                        ok = False
                    if etiqueta == 'aris_revision_1' and id_ == 'S3':
                        ok = False
                    if ok:
                        sub = base / 'corregidos' / etiqueta
                        sub.mkdir(parents=True, exist_ok=True)
                        destino = sub / (id_ + '.xml')
                        destino.write_text('<definitions/>', encoding='utf-8')
                        row['estado'] = 'correccion_finalizada'
                        row['xml_corregido_ruta'] = str(destino)
                    else:
                        row['estado'] = 'correccion_fallida'
                        row['motivo'] = 'Sin XML'
                        row.pop('xml_corregido_ruta', None)
                salida = base / 'exportaciones' / ('procesados_' + origen.stem + '.json')
                salida.parent.mkdir(parents=True, exist_ok=True)
                salida.write_text(
                    json.dumps({'version': 1, 'registros': list(estado.values())}),
                    encoding='utf-8'
                )
                return salida

            class ArisFalso:
                def __init__(self, *args):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def importar(self, xml, al_iniciar=None):
                    id_ = xml.stem
                    al_iniciar()
                    llamadas_aris.append(id_)
                    if id_ == 'S3' and llamadas_aris.count('S3') == 1:
                        return {'estado': 'xml_rechazado', 'importado': False,
                                'mensaje': 'XML rechazado'}
                    return {'estado': 'informe_completo', 'importado': None,
                            'mensaje': 'El informe está completo'}

            cfg = {'modo_chatgpt': 'ejecutar', 'modo_aris': 'ejecutar',
                   'aris_grupo_informes': '03 PROYECTOS',
                   'aris_report_name': 'BBVA - Importar BPMN'}
            with patch.object(pipeline, 'DATA', data), \
                 patch.object(pipeline, 'CHROME_PROFILE', data / 'perfil'), \
                 patch.object(pipeline, 'descargar_xmls', side_effect=descargar), \
                 patch.object(pipeline, 'procesar_registros', side_effect=corregir), \
                 patch.object(pipeline, 'SesionAris', ArisFalso), \
                 patch('builtins.input', return_value='EJECUTAR 4'):
                archivo = pipeline.ejecutar_pipeline(cfg, limite=4)

            reporte = json.loads(archivo.read_text(encoding='utf-8'))
            self.assertEqual(reporte['resumen']['total'], 4)
            self.assertEqual(reporte['resumen']['pasaron'], 4)
            self.assertEqual(reporte['resumen']['no_pasaron'], 0)
            self.assertEqual(llamadas_gpt, [
                ('inicial', nombres),
                ('gpt_reintento_1', ['S2']),
                ('aris_revision_1', ['S3']),
                ('aris_revision_2', ['S3']),
            ])
            self.assertEqual(llamadas_aris, ['S1', 'S2', 'S3', 'S4', 'S3'])
            self.assertNotIn('<definitions/>', archivo.read_text(encoding='utf-8'))

    def test_nombres_duplicados_bloquean_prevuelo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'registros.json'
            registro = {
                'id_solicitud': 'S1', 'id_n5': 'N1',
                'nombre_proceso': 'Prueba/Proceso', 'xml': '<definitions/>'
            }
            otro = {**registro, 'id_solicitud': 'S2',
                    'nombre_proceso': 'Prueba\Proceso'}
            ruta.write_text(json.dumps({
                'registros': [registro, otro]
            }), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'duplicado'):
                pipeline._seleccionar(ruta, 4, None)

    def test_resumen_distingue_pendientes_y_fallos(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'reporte.json'
            doc = {'registros': [
                {'estado_final': 'informe_completo'},
                {'estado_final': 'requiere_revision'},
                {'estado_final': 'pendiente'},
                {'estado_final': 'xml_rechazado'},
            ]}
            pipeline._guardar_reporte(ruta, doc)
            self.assertEqual(doc['resumen'], {
                'total': 4, 'pasaron': 1, 'no_pasaron': 2, 'pendientes': 1
            })


if __name__ == '__main__':
    unittest.main()
