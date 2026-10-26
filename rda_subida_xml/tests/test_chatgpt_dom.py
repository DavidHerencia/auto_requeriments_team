"""Tests sinteticos: NO incluyen HTML ni XML corporativo de las capturas."""
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules.chatgpt import _leer_resultado, _procesar_uno, SalidaSinXml


class Lista:
    def __init__(self, nodes=None):
        self.nodes = nodes or []

    def count(self):
        return len(self.nodes)

    @property
    def first(self):
        return self.nodes[0]

    def all_text_contents(self):
        return [node.text for node in self.nodes]


class Codigo:
    def __init__(self, text):
        self.text = text


class Cuerpo:
    def __init__(self, xml=None):
        self.xml = xml

    def locator(self, selector):
        if selector == 'pre code' and self.xml is not None:
            return Lista([Codigo(self.xml)])
        return Lista([])

    def inner_text(self):
        return 'XML para copiar' if self.xml is None else self.xml


class Boton:
    def __init__(self):
        self.clicks = 0

    def click(self):
        self.clicks += 1


class Turno:
    def __init__(self, cuerpo, boton=None):
        self.cuerpo = cuerpo
        self.boton = boton

    def locator(self, selector):
        if selector == '[data-markdown-text-style="assistant-message"]':
            return Lista([self.cuerpo])
        if 'button[aria-label="Descargar archivo"]' in selector and self.boton:
            # Como en el HTML real: el boton esta FUERA del markdown pero
            # dentro del contenedor de respuesta.
            return Lista([self.boton])
        return Lista([])


class Pagina:
    def __init__(self, xml):
        self.xml = xml

    def expect_download(self, timeout):
        class Descarga:
            def __init__(self, xml):
                self.xml = xml

            def save_as(self, ruta):
                Path(ruta).write_text(self.xml, encoding='utf-8')

        return nullcontext(SimpleNamespace(value=Descarga(self.xml)))


class TestDom(unittest.TestCase):
    def test_code_con_spans_concatenados_en_textcontent(self):
        xml = '\n<?xml version="1.0"?><definitions><lane id="LanePrincipal" name="Anterior"/></definitions>\n'
        turno = Turno(Cuerpo(xml))
        with tempfile.TemporaryDirectory() as tmp:
            result = _leer_resultado(None, turno, {}, Path(tmp) / 'temporal.xml')
        self.assertTrue(result.startswith('<?xml'))
        self.assertIn('id="LanePrincipal"', result)

    def test_boton_descarga_fuera_de_markdown(self):
        xml = '<?xml version="1.0"?><definitions><lane id="LanePrincipal" name="Anterior"/></definitions>'
        boton = Boton()
        turno = Turno(Cuerpo(), boton)
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / 'temporal.xml'
            result = _leer_resultado(Pagina(xml), turno, {}, destino)
            self.assertFalse(destino.exists())
        self.assertEqual(boton.clicks, 1)
        self.assertEqual(result, xml)

    def test_sin_xml_puede_solicitar_recuperacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SalidaSinXml):
                _leer_resultado(None, Turno(Cuerpo()), {}, Path(tmp) / 'temporal.xml')

    @patch('modules.chatgpt._leer_resultado', return_value='<definitions/>')
    @patch('modules.chatgpt._enviar', return_value=object())
    def test_xml_valido_no_envia_segundo_prompt(self, enviar, leer):
        contenido = _procesar_uno(None, {}, Path('entrada.xml'), Path('temporal.xml'))
        self.assertEqual(contenido, '<definitions/>')
        enviar.assert_called_once()
        leer.assert_called_once()


if __name__ == '__main__':
    unittest.main()
