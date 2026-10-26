import unittest
from unittest.mock import MagicMock
from modules.chatgpt import (
    _respuestas_asistente, TURNO, TURNO_LEGACY, TURNO_ROLE
)


class TestDeteccionHero(unittest.TestCase):
    @staticmethod
    def pagina_con_recuentos(actual, legado, rol):
        pagina = MagicMock()
        locs = {
            TURNO: MagicMock(),
            TURNO_LEGACY: MagicMock(),
            TURNO_ROLE: MagicMock()
        }
        locs[TURNO].count.return_value = actual
        locs[TURNO_LEGACY].count.return_value = legado
        locs[TURNO_ROLE].count.return_value = rol
        pagina.locator.side_effect = lambda selector: locs[selector]
        return pagina, locs

    def test_preferir_dom_hero_a_configuracion_antigua(self):
        pagina, locs = self.pagina_con_recuentos(1, 0, 1)
        resultado = _respuestas_asistente(
            pagina, {'gpt_respuesta_selector': TURNO_LEGACY}
        )
        self.assertIs(resultado, locs[TURNO])

    def test_respaldo_prototipo_anterior(self):
        pagina, locs = self.pagina_con_recuentos(0, 1, 0)
        self.assertIs(_respuestas_asistente(pagina, {}), locs[TURNO_LEGACY])

    def test_respaldo_por_rol(self):
        pagina, locs = self.pagina_con_recuentos(0, 0, 1)
        self.assertIs(_respuestas_asistente(pagina, {}), locs[TURNO_ROLE])

    def test_sin_respuesta_aun(self):
        pagina, locs = self.pagina_con_recuentos(0, 0, 0)
        self.assertIs(_respuestas_asistente(pagina, {}), locs[TURNO])


if __name__ == '__main__':
    unittest.main()
