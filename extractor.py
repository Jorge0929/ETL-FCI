import time
import random
import logging
import requests
import json 
import os

from datetime import datetime

# Importar la clase ZohoAuth de mi archivo auth
from auth import ZohoAuth

#Importat modulos
from config import MODULES_COLSUBSIDIO, MODULES_CUIDARTE

#Configurar el logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',)

#Obtener el nombre de mi archivo
logger = logging.getLogger(__name__)  

#Funcion que aumenta el tiempo de espera que cada falla una llamada a la API
def request_with_backoff(fn, max_retries=5):
    """
    Ejecuta fn() con reintentos usando backoff exponencial y jitter.

    Args:
        fn: Callable sin argumentos. Ejemplo:
            request_with_backoff(lambda: requests.get(url, headers=h))
        max_retries: Número máximo de intentos (default: 5).
            Con 5 intentos la espera máxima es ~17 segundos.

    Returns:
        El resultado de fn() cuando tiene éxito.

    Raises:
        La excepción original de fn() si se agotan todos los reintentos.
    """

    #Intentar varias veces
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
            logger.warning(f"Intento {intento+1}/{max_retries} falló: {e}. Reintentando en {wait_time:.2f}s...")
            time.sleep(wait_time)

def fetch_page(auth, module_name, fields, page, per_page=200, since=None, _retried= False):
    """
    Hace UN request a la API de Zoho para una página específica.
    Maneja errores de forma diferenciada según el código HTTP.

    Args:
        auth: instancia de ZohoAuth
        module_name: nombre del módulo en Zoho (ej: "Registro_empresas")
        fields: lista de campos a solicitar
        page: número de página a extraer
        per_page: registros por página (Zoho max: 200)
        since: datetime string ISO para extracción incremental (o None)
        _retried: uso interno — evita loop infinito en renovación de token 401.No pasar desde afuera.

    Returns:
        tuple: (lista_de_registros, hay_mas_paginas, next_page_token)
    """
    if len(fields) > 50:
        raise ValueError(
            f"{module_name} tiene {len(fields)} campos — Zoho permite máximo 50"
        )

    # URL base de Zoho CRM API v8:
    base_url = f"https://www.zohoapis.com/crm/v8/{module_name}"

    parametros = {
    "fields": ",".join(fields),
    "page": page,
    "per_page": per_page
    }

    headers = auth.get_header()

    if since is not None:
        parametros["sort_by"] = "Modified_Time"
        parametros["sort_order"] = "asc"
        headers["If-Modified-Since"] = since

    #Hacer el request con mis parametros y header 
    response = requests.get(base_url, headers=headers, params=parametros)

    #Si la respuesta es correcta obtener los registros
    if response.status_code == 200:
        res_json = response.json()
        registros = res_json.get("data", [])
        info = res_json.get("info", {})
        mas_registros = info.get("more_records", False)
        next_page_token = info.get("next_page_token")
        return registros, mas_registros, next_page_token

    #Si no hay registros
    elif response.status_code == 204:
        return [], False, None

    #Si no hay registros desde la fecha escogida
    elif response.status_code == 304:
        return [], False, None

    # 400/403 — errores no recuperables, no reintenta. 
    # 400: bug en el request (campos, módulo inválido)
    # 403: sin permisos en Zoho — contactar administrador
    elif response.status_code in (400,403,404):
        res_json = response.json()
        codigo_error = res_json.get("code", "UNKNOWN")
        mensaje_error = res_json.get("message", "Sin mensaje")
        logger.error(
            f"{response.status_code} en {module_name} |"
            f"código: {codigo_error} | mensaje: {mensaje_error}"
        )
        raise Exception(f"{response.status_code} {codigo_error} en {module_name} - {mensaje_error}")

    # Token vencido o inválido — renueva una vez y reintenta
    elif response.status_code == 401:
        if _retried:
            raise Exception(f"401 persistente en {module_name} — verificá credenciales en .env")
        auth.get_access_token()
        return fetch_page(auth, module_name, fields, page, per_page, since, _retried=True)

    # Rate limit excedido — espera Retry-After (o 60s por defecto) y deja que request_with_backoff reintente
    elif response.status_code == 429:
        retry_time = response.headers.get("Retry-After")
        if retry_time:
            time.sleep(int(retry_time))
        else: 
            time.sleep(60)
        raise Exception(f"429 Rate limit en {module_name}. Esperó {retry_time or 60}s")

    # 5xx y otros errores del servidor — raise para que request_with_backoff reintente con backoff
    else:
        logger.warning(f"{response.status_code} en {module_name} — reintentando con backoff")
        raise Exception(f"HTTP {response.status_code} en {module_name}")

def extract_module(auth, module_name, fields, since=None):
    """
    Extrae TODOS los registros de un módulo de Zoho con paginación.

    Args:
        auth: instancia de ZohoAuth
        module_name: nombre del módulo
        fields: lista de campos
        since: si se pasa, solo extrae registros modificados después de esta fecha

    Returns:
        list: todos los registros del módulo
    """

    #Verificar que la carpeta exista 
    os.makedirs("checkpoints", exist_ok=True)
    checkpoint_file = f"checkpoints/{module_name}.json"

    #Validar si existe un checkpoint antes
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "r") as f:
            estado = json.load(f)
        #Tomar la pagina y los registros del checkpoint
        page = estado["page"]
        all_records = estado["records"]
        logger.info(f"{module_name}: reanudando desde página {page} con {len(all_records)} registros previos")
    else:
        all_records = []
        page = 1
        #Loggear el inicio de la extracción
        logger.info(f"Iniciando extracción de {module_name}...")
        if since is not None:
            logger.info(f"Modo Incremental desde {since}")
        else:
            logger.info("Modo Full refresh")
    #Asignar la variable y el tiempo de inicio
    has_more = True
    start_time = datetime.now()
    paginas_procesadas=0
    while has_more:
        #Jalar registros
        registros,mas_paginas,_token = request_with_backoff(
            lambda:fetch_page(auth, module_name=module_name, fields=fields,page=page)
        )
        all_records.extend(registros) #Agregar nuevos registros

        #Guardar la pagina y los records de este intento
        estado = {"page": page, "records": all_records}
        with open(checkpoint_file, "w") as f:
            json.dump(estado, f)
        
        logger.info(f"{module_name} página {page} | {len(registros)} registros | {len(all_records)} acumulados")

        paginas_procesadas+=1
        page+=1
        has_more= mas_paginas
    
    duracion = (datetime.now() - start_time).total_seconds()
    logger.info(f"{module_name} completado | {len(all_records)} registros | {paginas_procesadas} páginas | {duracion:.1f}s")

    #Eliminar el checkpoint si la extracción es un exito
    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)
    return all_records

def run_extraction(projects=None,since=None):
    """
    Ejecuta la extracción de los proyectos seleccionados.

    Args:
        projects: lista de proyectos a extraer (ej: ["colsubsidio"]), o None para todos
        since: datetime ISO string para modo incremental, o None para full refresh
    """
    #Listar todos los proyectos de config
    todos_los_proyectos = {
        "colsubsidio": MODULES_COLSUBSIDIO,
        "cuidarte": MODULES_CUIDARTE,
        }
    #Si se escogio el proyecto validar si existe y extraerlo, si no extraer todos los proyectps
    if projects is None:
        projects = todos_los_proyectos
    else:
        for nombre in projects:
            if nombre not in todos_los_proyectos:
                disponibles = list(todos_los_proyectos.keys())
                raise ValueError(
                    f"Proyecto '{nombre}' no existe. Proyectos disponibles: {disponibles}"
                )
        projects = {nombre: todos_los_proyectos[nombre] for nombre in projects}
    
    auth = ZohoAuth() #Inicializar la clase zohoauth
    os.makedirs("output", exist_ok=True) #Crer carpeta de salidas

    #Recorre cada proyecto y cada módulo
    for project_name, modules in projects.items():
        os.makedirs(f"output/{project_name}", exist_ok=True)
        logger.info(f"Extrayendo proyecto {project_name}...")
        for module_name, fields in modules.items():
            try:
                registros = extract_module(auth, module_name, fields, since=since)
                ruta_registros = f"output/{project_name}/{module_name}.json"
                with open(ruta_registros, "w") as f:
                    json.dump(registros, f)
                logger.info(f"{module_name}: {len(registros)} registros extraídos")
            except Exception as e:
                logger.error(f"{module_name} FALLÓ — no se extrajo: {e}")
                continue

#Probar el modulo
if __name__ == "__main__":
    run_extraction(projects=["colsubsidio"])
