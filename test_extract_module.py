import os 

from unittest.mock import patch, MagicMock
from extractor import extract_module


def make_auth():
    """Crea un auth falso que no toca .env ni Zoho."""
    auth = MagicMock()
    auth.renew_token.return_value = {"Authorization": "Zoho-oauthtoken fake_token"}
    return auth

FIELDS = ["Name", "Municipio"]
MODULE = "Registro_empresas"

def test_extract_module_pagina_multiple():
    paginas_simuladas = [
        ([{"id": 1}], True, None),
        ([{"id": 2}], True, None),
        ([{"id": 3}], False, None),
    ]

    with patch("extractor.fetch_page", side_effect=paginas_simuladas) as mock_fetch:
        registros = extract_module(make_auth(), MODULE, FIELDS)

    assert len(registros) == 3
    assert registros == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert mock_fetch.call_count == 3
    print("✓ test_extract_module_pagina_multiple pasó")

def test_extract_module_borra_checkpoint_al_terminar():
    paginas_simuladas = [
        ([{"id": 1}], True, None),
        ([{"id": 2}], False, None),
    ]

    checkpoint_file = "checkpoints/Registro_empresas.json"

    with patch("extractor.fetch_page", side_effect=paginas_simuladas):
        extract_module(make_auth(), MODULE, FIELDS)

    # Después de terminar con éxito, el checkpoint NO debe existir
    assert not os.path.exists(checkpoint_file), "El checkpoint debería haberse borrado"
    print("✓ test_extract_module_borra_checkpoint_al_terminar pasó")

if __name__ == "__main__":
    test_extract_module_pagina_multiple()
    test_extract_module_borra_checkpoint_al_terminar()
    print("\n✓ Todos los tests pasaron")
