from unittest.mock import patch, MagicMock
from extractor import fetch_page, run_extraction


# =============================================================================
# HELPERS — reutilizados en todos los tests
# =============================================================================

def make_auth():
    """Crea un auth falso que no toca .env ni Zoho."""
    auth = MagicMock()
    auth.get_header.return_value = {"Authorization": "Zoho-oauthtoken fake_token"}
    return auth

def make_response(status_code, json_body=None, headers=None):
    """Crea una respuesta HTTP falsa con el status_code y body que vos controlás."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_body or {}
    mock.headers = headers or {}
    return mock

FIELDS = ["Name", "Municipio"]
MODULE = "Registro_empresas"


# =============================================================================
# TEST 200 — respuesta normal con registros
# =============================================================================

def test_fetch_page_200_con_registros():
    response = make_response(200, json_body={
        "data": [{"Name": "Empresa A"}, {"Name": "Empresa B"}],
        "info": {"more_records": True, "next_page_token": "abc123"}
    })

    with patch("extractor.requests.get", return_value=response):
        registros, mas_registros, token = fetch_page(make_auth(), MODULE, FIELDS, page=1)

    assert registros == [{"Name": "Empresa A"}, {"Name": "Empresa B"}]
    assert mas_registros == True
    assert token == "abc123"
    print("✓ test_fetch_page_200_con_registros pasó")


def test_fetch_page_200_ultima_pagina():
    """Cuando more_records es False no hay más páginas."""
    response = make_response(200, json_body={
        "data": [{"Name": "Empresa Z"}],
        "info": {"more_records": False, "next_page_token": None}
    })

    with patch("extractor.requests.get", return_value=response):
        registros, mas_registros, token = fetch_page(make_auth(), MODULE, FIELDS, page=1)

    assert mas_registros == False
    assert token is None
    print("✓ test_fetch_page_200_ultima_pagina pasó")


# =============================================================================
# TEST 204 — módulo vacío
# =============================================================================

def test_fetch_page_204_modulo_vacio():
    response = make_response(204)

    with patch("extractor.requests.get", return_value=response):
        registros, mas_registros, token = fetch_page(make_auth(), MODULE, FIELDS, page=1)

    assert registros == []
    assert mas_registros == False
    assert token is None
    print("✓ test_fetch_page_204_modulo_vacio pasó")


# =============================================================================
# TEST 304 — sin cambios desde since
# =============================================================================

def test_fetch_page_304_sin_cambios():
    response = make_response(304)

    with patch("extractor.requests.get", return_value=response):
        registros, mas_registros, token = fetch_page(
            make_auth(), MODULE, FIELDS, page=1,
            since="2025-01-01T00:00:00Z"
        )

    assert registros == []
    assert mas_registros == False
    assert token is None
    print("✓ test_fetch_page_304_sin_cambios pasó")


# =============================================================================
# TEST 401 — token vencido, se renueva y reintenta
# =============================================================================

def test_fetch_page_401_renueva_token_y_reintenta():
    """
    Primera llamada devuelve 401.
    Segunda llamada (después de renovar token) devuelve 200.
    Verifica que get_access_token fue llamado exactamente una vez.
    """
    response_401 = make_response(401)
    response_200 = make_response(200, json_body={
        "data": [{"Name": "Empresa A"}],
        "info": {"more_records": False, "next_page_token": None}
    })

    auth = make_auth()

    # side_effect permite devolver respuestas distintas en cada llamada
    # Primera llamada → 401, segunda llamada → 200
    with patch("extractor.requests.get", side_effect=[response_401, response_200]):
        registros, mas_registros, token = fetch_page(auth, MODULE, FIELDS, page=1)

    assert registros == [{"Name": "Empresa A"}]
    auth.get_access_token.assert_called_once()   # verificá que renovó exactamente una vez
    print("✓ test_fetch_page_401_renueva_token_y_reintenta pasó")


def test_fetch_page_401_persistente_lanza_excepcion():
    """
    Si después de renovar el token sigue dando 401,
    debe lanzar excepción sin reintentar de nuevo.
    """
    response_401 = make_response(401)

    with patch("extractor.requests.get", return_value=response_401):
        try:
            fetch_page(make_auth(), MODULE, FIELDS, page=1)
            assert False, "Debería haber lanzado excepción"
        except Exception as e:
            assert "401" in str(e)
            print("✓ test_fetch_page_401_persistente_lanza_excepcion pasó")

# =============================================================================
# TEST 404 —  Modulo Invalido
# =============================================================================

def test_fetch_page_404_lanza_excepcion_inmediata():
    response = make_response(404, json_body={
        "code": "INVALID_MODULE",
        "message": "The module name given is invalid"
    })

    with patch("extractor.requests.get", return_value=response):
        try:
            fetch_page(make_auth(), MODULE, FIELDS, page=1)
            assert False, "Debería haber lanzado excepción"
        except Exception as e:
            assert "404" in str(e)
            assert "INVALID_MODULE" in str(e)
            print("✓ test_fetch_page_404_lanza_excepcion_inmediata pasó")
# =============================================================================
# TEST 429 — rate limit con y sin Retry-After
# =============================================================================

def test_fetch_page_429_con_retry_after(monkeypatch=None):
    """
    429 con header Retry-After — debe esperar ese tiempo y lanzar excepción
    para que request_with_backoff reintente.
    """
    response_429 = make_response(429, headers={"Retry-After": "30"})

    sleep_calls = []

    def fake_sleep(segundos):
        sleep_calls.append(segundos)

    with patch("extractor.requests.get", return_value=response_429):
        with patch("extractor.time.sleep", side_effect=fake_sleep):
            try:
                fetch_page(make_auth(), MODULE, FIELDS, page=1)
                assert False, "Debería haber lanzado excepción"
            except Exception as e:
                assert "429" in str(e)
                assert sleep_calls == [30]   # esperó exactamente 30s
                print("✓ test_fetch_page_429_con_retry_after pasó")


def test_fetch_page_429_sin_retry_after():
    """
    429 sin header Retry-After — debe esperar 60s por defecto.
    """
    response_429 = make_response(429, headers={})

    sleep_calls = []

    def fake_sleep(segundos):
        sleep_calls.append(segundos)

    with patch("extractor.requests.get", return_value=response_429):
        with patch("extractor.time.sleep", side_effect=fake_sleep):
            try:
                fetch_page(make_auth(), MODULE, FIELDS, page=1)
                assert False, "Debería haber lanzado excepción"
            except Exception as e:
                assert "429" in str(e)
                assert sleep_calls == [60]   # esperó 60s por defecto
                print("✓ test_fetch_page_429_sin_retry_after pasó")


# =============================================================================
# TEST 400 — bug en el request, no reintenta
# =============================================================================

def test_fetch_page_400_lanza_excepcion_inmediata():
    response = make_response(400, json_body={
        "code": "LIMIT_EXCEEDED",
        "message": "Fields limit exceeded"
    })

    with patch("extractor.requests.get", return_value=response):
        try:
            fetch_page(make_auth(), MODULE, FIELDS, page=1)
            assert False, "Debería haber lanzado excepción"
        except Exception as e:
            assert "400" in str(e)
            assert "LIMIT_EXCEEDED" in str(e)
            print("✓ test_fetch_page_400_lanza_excepcion_inmediata pasó")


# =============================================================================
# TEST 403 — sin permisos, no reintenta
# =============================================================================

def test_fetch_page_403_lanza_excepcion_inmediata():
    response = make_response(403, json_body={
        "code": "NO_PERMISSION",
        "message": "Permission denied to read records"
    })

    with patch("extractor.requests.get", return_value=response):
        try:
            fetch_page(make_auth(), MODULE, FIELDS, page=1)
            assert False, "Debería haber lanzado excepción"
        except Exception as e:
            assert "403" in str(e)
            assert "NO_PERMISSION" in str(e)
            print("✓ test_fetch_page_403_lanza_excepcion_inmediata pasó")


# =============================================================================
# TEST 5xx — error del servidor, lanza para que request_with_backoff reintente
# =============================================================================

def test_fetch_page_500_lanza_para_backoff():
    response = make_response(500)

    with patch("extractor.requests.get", return_value=response):
        try:
            fetch_page(make_auth(), MODULE, FIELDS, page=1)
            assert False, "Debería haber lanzado excepción"
        except Exception as e:
            assert "500" in str(e)
            print("✓ test_fetch_page_500_lanza_para_backoff pasó")


# =============================================================================
# TEST — Proyecto inválido en run_extraction
# =============================================================================

def test_run_extraction_proyecto_invalido():
    try:
        run_extraction(projects=["proyecto_inexistente"])
        assert False, "Debería haber lanzado ValueError"
    except ValueError as e:
        assert "no existe" in str(e)
        print("✓ test_run_extraction_proyecto_invalido pasó")

# =============================================================================
# TEST — Límite de 50 campos en fetch_page
# =============================================================================

def test_fetch_page_demasiados_campos():
    fields_61 = [f"Campo_{i}" for i in range(61)]   # 61 campos, supera el límite
    try:
        fetch_page(make_auth(), MODULE, fields_61, page=1)
        assert False, "Debería haber lanzado ValueError"
    except ValueError as e:
        assert "50" in str(e)
        print("✓ test_fetch_page_demasiados_campos pasó")

# =============================================================================
# TEST — Probar el parametro since en el header
# =============================================================================

def test_since_se_propaga_hasta_el_header():
    """
    Verifica que el since pasado a run_extraction llega a fetch_page
    como header If-Modified-Since.
    """
    since_fecha = "2025-06-10T00:00:00Z"

    # Respuesta vacía — solo nos interesa inspeccionar los headers del request
    response = make_response(200, json_body={
        "data": [],
        "info": {"more_records": False, "next_page_token": None}
    })

    with patch("extractor.requests.get", return_value=response) as mock_get:
        fetch_page(make_auth(), MODULE, FIELDS, page=1, since=since_fecha)

    # mock_get.call_args captura los argumentos con que se llamó requests.get
    _, kwargs = mock_get.call_args
    headers_enviados = kwargs["headers"]

    assert "If-Modified-Since" in headers_enviados
    assert headers_enviados["If-Modified-Since"] == since_fecha
    print("✓ test_since_se_propaga_hasta_el_header pasó")
# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    test_fetch_page_200_con_registros()
    test_fetch_page_200_ultima_pagina()
    test_fetch_page_204_modulo_vacio()
    test_fetch_page_304_sin_cambios()
    test_fetch_page_401_renueva_token_y_reintenta()
    test_fetch_page_401_persistente_lanza_excepcion()
    test_fetch_page_404_lanza_excepcion_inmediata() 
    test_fetch_page_429_con_retry_after()
    test_fetch_page_429_sin_retry_after()
    test_fetch_page_400_lanza_excepcion_inmediata()
    test_fetch_page_403_lanza_excepcion_inmediata()
    test_fetch_page_500_lanza_para_backoff()
    test_run_extraction_proyecto_invalido()
    test_fetch_page_demasiados_campos()
    test_since_se_propaga_hasta_el_header()
    print("\n✓ Todos los tests pasaron")