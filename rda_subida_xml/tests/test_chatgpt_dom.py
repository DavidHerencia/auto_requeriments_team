import unittest
from pathlib import Path
from modules.chatgpt import _leer_resultado


class FakeLocator:
    def __init__(self, count, code=None):
        self._count = count
        self._code = code

    def count(self):
        return self._count

    @property
    def first(self):
        return self

    def locator(self, selector):
        if selector == 'pre code':
            return FakeLocator(1, code=self._code)
        return FakeLocator(0)

    def all_text_contents(self):
        return [self._code]


class FakeTurn:
    def locator(self, selector):
        if selector == '[data-markdown-style="assistant-message"]':
            return FakeLocator(
                1,
                '<?xml version="1.0"?><definitions><lane id="LanePrincipal" name="Viejo"/></definitions>'
            )
        return FakeLocator(0)


class TestDom(unittest.TestCase):
    def test_xml_desde_pre_code_sin_portapapeles(self):
        contenido = _leer_resultado(None, FakeTurn(), {}, Path('no-utilizado'))
        self.assertTrue(contenido.startswith('<?xml'))
        self.assertIn('id="LanePrincipal"', contenido)


if __name__ == '__main__':
    unittest.main()
