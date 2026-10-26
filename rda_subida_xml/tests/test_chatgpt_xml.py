import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch, call
from modules.chatgpt import (nombre_seguro, corregir_lane, corregir_nombres_bpmn, extraer_xml,
    gpt_url, SalidaSinXml, ErrorPostprocesamiento, RespuestaIncompleta,
    MENSAJE_INCOMPLETO, INICIAL, RECUPERAR, es_respuesta_incompleta, _procesar_uno)


class TestXml(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(nombre_seguro('ABC/1','Diseño \\ Producto: X'), 'ABC-1 Diseño - Producto- X - Detalle')

    def test_lane(self):
        src = '<?xml version="1.0"?><defs><lane id="LanePrincipal" name="Viejo"/><lane id="otro" name="Queda"/></defs>'
        result = corregir_lane(src, 'N5/1', 'Proceso & pagos')
        self.assertIn('name="N5/1 Proceso &amp; pagos"', result)
        self.assertIn('name="Queda"', result)

    def test_lane_missing(self):
        with self.assertRaises(ErrorPostprocesamiento):
            corregir_lane('<xml/>', 'a', 'b')

    def test_lane_principal_con_guion_bajo(self):
        xml = ('<?xml version="1.0"?>'
               '<semantic:definitions xmlns:semantic="urn:bpmn">'
               '<semantic:laneSet>'
               '<semantic:lane id="Lane_Principal" name="Viejo"/>'
               '<semantic:lane id="Lane_Cliente" name="Cliente"/>'
               '</semantic:laneSet></semantic:definitions>')
        result = corregir_lane(xml, 'N5-001', 'Diseño / Hipotecas')
        self.assertIn('name="N5-001 Diseño / Hipotecas"', result)
        self.assertIn('name="Cliente"', result)
        self.assertIn('id="Lane_Principal"', result)

    def test_dos_variantes_no_se_eligen_al_azar(self):
        xml = ('<xml><lane id="LanePrincipal" name="Uno"/>'
               '<lane id="Lane_Principal" name="Dos"/></xml>')
        with self.assertRaises(ErrorPostprocesamiento):
            corregir_lane(xml, 'N5-001', 'Proceso')

    def test_process_y_lane_sin_tocar_definitions(self):
        xml = ('<?xml version="1.0"?>'
               '<semantic:definitions xmlns:semantic="urn:bpmn" name="Viejo defs">'
               '<semantic:process id="ProcA" name="Viejo process">'
               '<semantic:laneSet><semantic:lane id="Lane_Principal" name="Viejo lane"/>'
               '<semantic:lane id="Otra" name="Otro rol"/></semantic:laneSet>'
               '</semantic:process></semantic:definitions>')
        nombre = 'Diseño / Cliente & "Banca" > Control'
        output = corregir_nombres_bpmn(xml, 'N5/1', nombre)
        raiz = ET.fromstring(output)
        process = next(x for x in raiz.iter() if x.tag.endswith('}process'))
        lane = next(x for x in raiz.iter() if x.attrib.get('id') == 'Lane_Principal')
        self.assertEqual(process.attrib['name'], 'N5/1 ' + nombre)
        self.assertEqual(lane.attrib['name'], 'N5/1 ' + nombre)
        self.assertEqual(raiz.attrib['name'], 'Viejo defs')
        self.assertEqual(next(x for x in raiz.iter() if x.attrib.get('id') == 'Otra').attrib['name'], 'Otro rol')
        self.assertIn('xmlns:semantic="urn:bpmn"', output)

    def test_multiples_process_rechazados(self):
        xml = ('<definitions><process id="P1" name="A"><lane id="LanePrincipal" name="A"/>'
               '</process><process id="P2" name="B"/></definitions>')
        with self.assertRaises(ErrorPostprocesamiento):
            corregir_nombres_bpmn(xml, 'N5', 'Proceso')

    def test_incompleto_explicito(self):
        self.assertTrue(es_respuesta_incompleta('  "Incompleto – no se puede generar porque falta demasiada info." '))
        self.assertFalse(es_respuesta_incompleta('El proceso esta incompleto pero puedo corregirlo'))
        self.assertIn(MENSAJE_INCOMPLETO, INICIAL)
        self.assertIn(MENSAJE_INCOMPLETO, RECUPERAR)
        self.assertTrue(RECUPERAR.endswith(
            'Si no tienes otra opcion disponible, si, dame el XML para copiar y yo lo pego.'
        ))

    @patch('modules.chatgpt._leer_resultado')
    @patch('modules.chatgpt._enviar')
    def test_segundo_prompt_se_envia_sin_adjunto_si_el_primero_no_contiene_xml(self, enviar, leer):
        leer.side_effect = [SalidaSinXml('sin XML'), '<xml/>']
        enviar.side_effect = ['turno1', 'turno2']
        archivo = Path('archivo.xml')
        result = _procesar_uno('pagina', {}, archivo, Path('temporal.xml'))
        self.assertEqual(result, '<xml/>')
        self.assertEqual(enviar.call_count, 2)
        self.assertEqual(enviar.call_args_list[0], call('pagina', {}, INICIAL, archivo))
        self.assertEqual(enviar.call_args_list[1], call('pagina', {}, RECUPERAR))
        self.assertEqual(leer.call_count, 2)

    @patch('modules.chatgpt._leer_resultado', side_effect=RespuestaIncompleta(MENSAJE_INCOMPLETO))
    @patch('modules.chatgpt._enviar', return_value='turno1')
    def test_incompleto_no_envia_segundo_prompt(self, enviar, leer):
        with self.assertRaises(RespuestaIncompleta):
            _procesar_uno('pagina', {}, Path('original.xml'), Path('temporal.xml'))
        enviar.assert_called_once()
        leer.assert_called_once()

    def test_extract(self):
        self.assertEqual(extraer_xml('```xml\n<xml/>\n```'), '<xml/>')
        with self.assertRaises(SalidaSinXml):
            extraer_xml('No se ha podido cargar esta respuesta.')

    def test_url(self):
        self.assertEqual(gpt_url({'hero_gpt_url':'https://chatgpt.com/g/g-abc-hero/c/uuid'}), 'https://chatgpt.com/g/g-abc-hero')


if __name__ == '__main__':
    unittest.main()
