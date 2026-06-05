from extractor import request_with_backoff

def test_reintenta_y_tiene_exito():
    contador = {"intentos": 0} 

    def fn_que_falla_3_veces():
        contador["intentos"] += 1
        if contador["intentos"] < 4:
            raise ValueError(f"Error simulado en intento {contador['intentos']}")
        return "éxito"

    resultado = request_with_backoff(fn_que_falla_3_veces)
    
    assert resultado == "éxito", f"Esperaba 'éxito', obtuve {resultado}"
    assert contador["intentos"] == 4, f"Esperaba 4 intentos, hizo {contador['intentos']}"
    print("✓ test_reintenta_y_tiene_exito pasó")

def test_lanza_excepcion_al_agotar_intentos():
    def fn_que_siempre_falla():
        raise ConnectionError("sin conexión")

    try:
        request_with_backoff(fn_que_siempre_falla, max_retries=3)
        assert False, "Debería haber lanzado excepción"
    except ConnectionError:
        print("✓ test_lanza_excepcion_al_agotar_intentos pasó")

test_lanza_excepcion_al_agotar_intentos()
test_reintenta_y_tiene_exito()