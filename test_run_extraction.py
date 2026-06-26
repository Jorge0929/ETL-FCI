from extractor import run_extraction
from unittest.mock import patch, MagicMock

def test_run_extraction_continua_si_un_modulo_falla():
    modulos_de_prueba = {
        "Modulo_OK_1": ["Name"],
        "Modulo_FALLA": ["Name"],
        "Modulo_OK_2": ["Name"],
    }

    with patch("extractor.ZohoAuth"), \
        patch("extractor.MODULES_COLSUBSIDIO", modulos_de_prueba), \
        patch("extractor.extract_module") as mock_extract, \
        patch("extractor.open"), \
        patch("extractor.os.makedirs"), \
        patch("extractor.json.dump"):          # ← no escribe nada en disco
        mock_extract.side_effect = [
            [{"id": 1}],
            Exception("módulo roto"),
            [{"id": 2}],
        ]
        run_extraction(projects=["colsubsidio"])
        assert mock_extract.call_count == 3
    print("✓ test_run_extraction_continua_si_un_modulo_falla pasó")

if __name__ == "__main__":
    test_run_extraction_continua_si_un_modulo_falla()
    print("\n✓ Todos los tests pasaron")