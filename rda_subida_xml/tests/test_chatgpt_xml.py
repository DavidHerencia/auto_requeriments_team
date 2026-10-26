import unittest
from modules.chatgpt import nombre_seguro, corregir_lane, extraer_xml, gpt_url, SalidaSinXml


class TestXml(unittest.TestCase):
    def test_filename(self):
        self.assertEqual(nombre_seguro('ABC/1','Diseño \\ Producto: X'), 'ABC-1 Diseño - Producto- X - Detalle')

    def test_lane(self):
        src = '<?xml version="1.0"?><defs><lane id="LanePrincipal" name="Viejo"/><lane id="otro" name="Queda"/></defs>'
        result = corregir_lane(src, 'N5/1', 'Proceso & pagos')
        self.assertIn('name="N5/1 Proceso &amp; pagos"', result)
        self.assertIn('name="Queda"', result)

    def test_lane_missing(self):
        with self.assertRaises(SalidaSinXml):
            corregir_lane('<xml/>','a','b')

    def test_extract(self):
        self.assertEqual(extraer_xml('```xml\n<xml/>\n```'), '<xml/>')
        with self.assertRaises(SalidaSinXml):
            extraer_xml('No se ha podido cargar esta respuesta.')

    def test_url(self):
        self.assertEqual(gpt_url({'hero_gpt_url':'https://chatgpt.com/g/g-abc-hero/c/uuid'}), 'https://chatgpt.com/g/g-abc-hero')


if __name__ == '__main__':
    unittest.main()
