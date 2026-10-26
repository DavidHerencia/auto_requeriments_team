"""Contratos ARIS sin navegador ni datos corporativos."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.aris import clasificar_mensaje, validar_xml_aris, _arise_url
import workflow


class TestAris(unittest.TestCase):
    def test_resultados_terminales(self):
        self.assertEqual(clasificar_mensaje('El informe está completo.'), 'informe_completo')
        self.assertEqual(clasificar_mensaje('El fichero seleccionado no es válido. Pruebe de nuevo con otro fichero.'), 'xml_rechazado')
        self.assertEqual(clasificar_mensaje('Generando informes...'), 'requiere_revision')

    def test_configuracion_url_carpeta(self):
        self.assertEqual(
            _arise_url({'aris_url':'https://aris.example.org/#default/repository/a.group.ABC'}),
            'https://aris.example.org/#default/repository/a.group.ABC'
        )
        with self.assertRaises(ValueError):
            _arise_url({'aris_url':'http://aris.example.org/'})

    def test_xml_local_no_txt(self):
        with tempfile.TemporaryDirectory() as carpeta:
            root = Path(carpeta)
            archivo = root/'sample.xml'
            archivo.write_text('<semantic:definitions xmlns:semantic="urn:bpmn"/>', encoding='utf-8')
            self.assertEqual(validar_xml_aris(archivo), archivo.resolve())
            with self.assertRaises(ValueError):
                validar_xml_aris(root/'sample.txt')

    def test_checkpoint_importado_se_omite_al_reanudar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            corregidos = data / 'corregidos'
            corregidos.mkdir(parents=True)
            xml = corregidos / 'ejemplo - Detalle.xml'
            xml.write_text('<definitions/>', encoding='utf-8')
            origen = data / 'exportaciones' / 'procesados_prueba.json'
            origen.parent.mkdir(parents=True)
            origen.write_text(json.dumps({
                'version': 1, 'registros': [{
                    'id_solicitud':'S1',
                    'id_archivo_n5':'N5 Ejemplo - Detalle',
                    'estado':'correccion_finalizada',
                    'xml_corregido_ruta':str(xml),
                }, {
                    'id_solicitud':'S2',
                    'estado':'incompleto'
                }]
            }, ensure_ascii=False), encoding='utf-8')

            def importar(ruta, cfg, perfil, al_iniciar):
                al_iniciar()
                return {'estado':'informe_completo', 'importado':None}

            with patch.object(workflow, 'DATA', data), \
                 patch.object(workflow, 'CHROME_PROFILE', data/'chrome_profile_sheets'), \
                 patch.object(workflow, 'procesar_aris', side_effect=importar) as llamador:
                destino = workflow.ejecutar_lote_aris(origen, {'modo_aris':'ejecutar'})
                estado = json.loads(destino.read_text(encoding='utf-8'))['registros'][0]
                self.assertEqual(estado['estado_aris'], 'informe_completo')
                self.assertEqual(estado['intentos_importacion'], 1)
                self.assertEqual(llamador.call_count, 1)
                # El segundo llamado no debe reimportar el mismo registro.
                workflow.ejecutar_lote_aris(origen, {'modo_aris':'ejecutar'})
                self.assertEqual(llamador.call_count, 1)

    def test_rechazo_no_dispara_reenvio_automatico(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            corregidos = data/'corregidos'
            corregidos.mkdir(parents=True)
            xml = corregidos/'prueba.xml'
            xml.write_text('<definitions/>', encoding='utf-8')
            paquete = data/'exportaciones'/'procesados_sample.json'
            paquete.parent.mkdir()
            paquete.write_text(json.dumps({'registros':[{
                'id_solicitud':'S9','estado':'correccion_finalizada',
                'xml_corregido_ruta':str(xml)
            }]}), encoding='utf-8')
            def importar(*args, **kwargs):
                kwargs['al_iniciar']()
                return {'estado':'xml_rechazado','importado':False}

            with patch.object(workflow, 'DATA', data), \
                 patch.object(workflow, 'procesar_aris', side_effect=importar) as mock:
                ruta = workflow.ejecutar_lote_aris(paquete, {})
                estado = json.loads(ruta.read_text(encoding='utf-8'))['registros'][0]
                self.assertEqual(estado['estado_aris'], 'xml_rechazado')
                self.assertEqual(estado['intentos_importacion'], 1)
                workflow.ejecutar_lote_aris(paquete, {})
                self.assertEqual(mock.call_count, 1)

    def test_no_importa_xml_fuera_de_carpeta(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            data.mkdir()
            externo = Path(tmp)/'privado.xml'
            externo.write_text('<definitions/>', encoding='utf-8')
            with patch.object(workflow, 'DATA', data):
                with self.assertRaises(ValueError):
                    workflow._xml_del_registro({'xml_corregido_ruta':str(externo)})


if __name__ == '__main__':
    unittest.main()
