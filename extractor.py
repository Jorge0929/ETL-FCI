import time
import random
import logging
from datetime import datetime

# Importar la clase ZohoAuth de mi archivo auth
from auth import ZohoAuth


#Funcion que aumenta el tiempo de espera que cada falla una llamada a la API
def request_with_backoff(fn, max_retries=5):
    #Intentar 5 veces
    for intento in range(max_retries):
        #Si funciona retornar el resultado
        try:
            result = fn()
            return result
        #Si hay algun error aumentar tiempo de espera, si es el intento 5 lanzar un error 
        except Exception as e: 
            if intento == max_retries-1:
                raise 
            wait_time = (2 ** intento)+random.uniform(0,1)
            print(f"Intento {intento+1}/{max_retries} falló: {e}. Reintentando en {wait_time}s")
            time.sleep(wait_time)