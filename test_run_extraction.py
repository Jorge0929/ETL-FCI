from extractor import run_extraction
from unittest.mock import patch, MagicMock

def test_run_extraction_continua_si_un_modulo_falla():
    modulos_de_prueba = {
        "Modulo_OK_1": ["Name"],
        "Modulo_FALLA": ["Name"],
        "Modulo_OK_2": ["Name"],
    }

    with patch("extractor.ZohoAuth"):# auth falso
        with patch("extractor.MODULES_COLSUBSIDIO", modulos_de_prueba):#módulos de prueba
            with patch("extractor.extract_module") as mock_extract:
                mock_extract.side_effect = [
                    # TODO: 3 elementos — OK, excepción, OK
                    [{"id": 1}],                          # Modulo_OK_1 → funciona
                    Exception("módulo roto"),             # Modulo_FALLA → lanza excepción
                    [{"id": 2}],
                ]
                run_extraction(projects=["colsubsidio"])

                # TODO: assert de que extract_module se llamó 3 veces (no se cortó en el fallo)
                assert mock_extract.call_count == 3
    print("✓ test_run_extraction_continua_si_un_modulo_falla pasó")

if __name__ == "__main__":
    test_run_extraction_continua_si_un_modulo_falla()
    print("\n✓ Todos los tests pasaron")