#Maybe in future use request-outhlib
import os 
import requests
import time
from dotenv import load_dotenv
import json

#Lee las variables de entorno y las carga en os
load_dotenv(".env")

class ZohoAuth:
    def __init__(self):
        #Obtener variables de entorno
        self.zoho_client = os.getenv("ZOHO_CLIENT_ID")
        self.zoho_secret = os.getenv("ZOHO_CLIENT_SECRET")
        self.refresh_token = os.getenv("ZOHO_REFRESH_TOKEN")
        
        #Crear acces token
        self.access_token = None
        #Crear variable para guardar el tiempo del token
        self.token_creation_time = 0
        #url
        self.token_url = 'https://accounts.zoho.com/oauth/v2/token'

    def get_access_token(self):
        #datos para enviar en el body
        body = {
            'refresh_token': self.refresh_token,
            'client_id': self.zoho_client,
            'client_secret': self.zoho_secret,
            'grant_type': 'refresh_token'
        }

        #Hacer la petición del access token
        response = requests.post(self.token_url, data=body)
        
        #levantar error si hay un codigo http diferente a 200
        response.raise_for_status()

        data=response.json()
        #validar que se tengan los permisos correctos
        if 'error' in data:
            raise ValueError(f"Zoho rechazó el token: {data['error']}")
        
        #Obtener el access toke y tiempo 
        self.access_token = data.get('access_token', None)
        self.token_creation_time= time.time()

        return self.access_token


    def renew_token(self):
        #Obtener el tiempo actual
        current_time = time.time()

        #Si el token se vencio o no se ha creado, generarlo
        if self.access_token == None or (current_time - self.token_creation_time) > 3000:
            self.get_access_token()
        
        return {
            "Authorization": f"Zoho-oauthtoken {self.access_token}"
        }