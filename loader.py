import os
import json
import logging

from google.cloud import bigquery
from dotenv import load_dotenv
from metadata import ensure_metadata_table, write_run

# Cargar variables de entorno
load_dotenv(".env")

# Misma config de logging que extractor.py
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

PROJECT_ID = "zoho-bq-pipeline-492116"
DATASET_ID = "colsubsidio_ruta_empresas"

# Campos que Zoho devuelve como OBJETO ({"name":..,"id":..,"email":..}) o LISTA.
# Estos aterrizan como columna JSON nativa en raw. .
NESTED_FIELDS = {
    # estándar de Zoho (siempre objetos)
    "Owner", "Created_By", "Modified_By",
    # listas
    "Tag", "Connected_To__s",
    # lookups a otros módulos (vienen como objeto {name, id})
    "Empresa", "Profesional_asignado", "Profesional_asignado1",
    "Agenda", "Agenda_origen", "Agendamiento_grupal_origen",
}


def get_client():
    """
    Crea y devuelve un cliente de BigQuery.
    Busca la credencial vía GOOGLE_APPLICATION_CREDENTIALS (ya está en tu .env).
    """
    return bigquery.Client(project=PROJECT_ID)

# SCHEMAS — explícitos, no autodetect

def build_raw_schema(fields):
    """
    Schema de la tabla RAW.
    - id           → STRING (llave del MERGE, viene de Zoho)
    - anidados     → JSON   (objetos/listas; dbt los lee con JSON_VALUE)
    - resto        → STRING (aterrizaje fiel; el casteo de fechas/números es dbt)
    - Modified_Time→ TIMESTAMP (plumbing: fuente del since incremental)
    - _loaded_at   → TIMESTAMP (plumbing: auditoría, cuándo se cargó)
    """
    cols = [bigquery.SchemaField("id", "STRING")]
    for f in fields:
        if f == "Modified_Time": 
            continue
        tipo = "JSON" if f in NESTED_FIELDS else "STRING"
        cols.append(bigquery.SchemaField(f, tipo))
    cols.append(bigquery.SchemaField("Modified_Time", "TIMESTAMP"))
    cols.append(bigquery.SchemaField("_loaded_at", "TIMESTAMP"))
    return cols


def build_staging_schema(fields):
    """
    Schema de STAGING: TODO STRING. La carga nunca falla por un tipo.
    Los tipos reales se materializan en el MERGE hacia raw.
    """
    cols = [bigquery.SchemaField("id", "STRING")]
    for f in fields:
        if f == "Modified_Time":     
            continue
        cols.append(bigquery.SchemaField(f, "STRING"))
    cols.append(bigquery.SchemaField("Modified_Time", "STRING"))
    return cols

# PREPARACIÓN DE FILAS

def _to_cell(value):
    """
    Convierte cualquier valor de Zoho a algo que entra en una columna STRING:
    - None         → None  (queda NULL en BigQuery)
    - dict / list  → json.dumps (objeto/lista anidada → texto JSON)
    - str          → tal cual
    - número/bool  → str(value)
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return value
    return str(value)


def prepare_rows(records, fields):
    """Convierte los registros crudos de Zoho en filas listas para staging (todo STRING)."""
    rows = []
    for r in records:
        row = {"id": _to_cell(r.get("id"))}
        for f in fields:
            row[f] = _to_cell(r.get(f))
        row["Modified_Time"] = _to_cell(r.get("Modified_Time"))
        rows.append(row)
    return rows

# OPERACIONES BIGQUERY

def ensure_table(client, table_fqn, schema):
    """Crea la tabla con schema explícito si no existe. Si ya existe, no la toca."""
    table = bigquery.Table(table_fqn, schema=schema)
    client.create_table(table, exists_ok=True)


def load_to_staging(client, rows, staging_schema, staging_fqn):
    """Carga las filas a staging reemplazando el contenido anterior (WRITE_TRUNCATE)."""
    job_config = bigquery.LoadJobConfig(
        schema=staging_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
    )
    job = client.load_table_from_json(rows, staging_fqn, job_config=job_config)
    job.result()  # espera a que termine; lanza excepción si falla
    return job.output_rows


def count_insert_update(client, staging_fqn, raw_fqn):
    """
    Cuenta ANTES del MERGE cuántos ids de staging ya existen en raw (updates)
    y cuántos son nuevos (inserts). DISTINCT por si hubiera ids repetidos.
    """
    q = f"""
    SELECT
      (SELECT COUNT(DISTINCT id) FROM `{staging_fqn}`) AS total,
      (SELECT COUNT(DISTINCT s.id) FROM `{staging_fqn}` s
       WHERE EXISTS (SELECT 1 FROM `{raw_fqn}` t WHERE t.id = s.id)) AS updates
    """
    row = list(client.query(q).result())[0]
    total = row["total"]
    updates = row["updates"]
    inserts = total - updates
    return inserts, updates


def _src_expr(name, field_type):
    """Cómo se lee cada columna desde staging (S) al materializar el tipo en raw."""
    if name == "_loaded_at":
        return "CURRENT_TIMESTAMP()"            # generado en la carga, no viene de Zoho
    if field_type == "JSON":
        return f"SAFE.PARSE_JSON(S.`{name}`)"   # texto JSON → JSON nativo (SAFE = no rompe si viene mal)
    if field_type == "TIMESTAMP":               # Modified_Time
        return f"SAFE_CAST(S.`{name}` AS TIMESTAMP)"
    return f"S.`{name}`"                         # STRING: tal cual


def build_merge_sql(raw_fqn, staging_fqn, raw_schema):
    """
    Construye el MERGE idempotente dinámicamente desde el schema raw.
    - Dedup defensivo: se queda con la fila más reciente por id (por si Zoho
      repite un id entre páginas). Evita que el MERGE explote.
    - WHEN MATCHED   → actualiza todas las columnas menos id.
    - WHEN NOT MATCHED → inserta la fila nueva.
    """
    update_set = ",\n    ".join(
        f"T.`{f.name}` = {_src_expr(f.name, f.field_type)}"
        for f in raw_schema if f.name != "id"
    )
    insert_cols = ", ".join(f"`{f.name}`" for f in raw_schema)
    insert_vals = ", ".join(_src_expr(f.name, f.field_type) for f in raw_schema)

    return f"""
MERGE `{raw_fqn}` T
USING (
  SELECT * EXCEPT(_rn) FROM (
    SELECT s.*, ROW_NUMBER() OVER (PARTITION BY id ORDER BY Modified_Time DESC) AS _rn
    FROM `{staging_fqn}` s
  )
  WHERE _rn = 1
) S
ON T.id = S.id
WHEN MATCHED THEN UPDATE SET
    {update_set}
WHEN NOT MATCHED THEN INSERT ({insert_cols})
VALUES ({insert_vals})
"""

# ORQUESTACIÓN POR MÓDULO

def load_module(client, module_name, fields, project_name="colsubsidio"):
    """
    Carga un módulo desde output/{project_name}/{module_name}.json a su tabla raw,
    con upsert idempotente. Loggea insertados vs actualizados.
    """
    table_name = module_name.lower()
    raw_fqn = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    staging_fqn = f"{PROJECT_ID}.{DATASET_ID}._stg_{table_name}"

    # 1. Leer el JSON que dejó el extractor
    path = f"output/{project_name}/{module_name}.json"
    if not os.path.exists(path):
        logger.error(f"{module_name}: no existe {path} — corré el extractor primero")
        return
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        logger.warning(f"{module_name}: 0 registros en {path} — nada que cargar")
        write_run(client, project_name, module_name, "empty", 0, None)
        return

    # 2. Guardrail: sin 'id' no hay MERGE seguro
    if "id" not in records[0]:
        logger.error(
            f"{module_name}: los registros no traen 'id' de Zoho — "
            f"no puedo hacer MERGE seguro. Revisá la extracción."
        )
        return

    # 3. Preparar filas y schemas
    rows = prepare_rows(records, fields)
    raw_schema = build_raw_schema(fields)
    staging_schema = build_staging_schema(fields)

    # 4. Asegurar que raw exista (la necesita el conteo y el MERGE)
    ensure_table(client, raw_fqn, raw_schema)

    # 5. Cargar a staging (reemplaza cada corrida)
    cargados = load_to_staging(client, rows, staging_schema, staging_fqn)
    logger.info(f"{module_name}: {cargados} filas en staging")

    # 6. Contar antes del MERGE
    inserts, updates = count_insert_update(client, staging_fqn, raw_fqn)

    # 7. MERGE idempotente
    merge_sql = build_merge_sql(raw_fqn, staging_fqn, raw_schema)
    client.query(merge_sql).result()

    # 8. Log de salida 
    logger.info(f"{module_name}: {inserts} insertados | {updates} actualizados | tabla {table_name}")

    # 9. Watermark = MAX(Modified_Time) de lo cargado → fuente del since incremental
    fechas = [r.get("Modified_Time") for r in records if r.get("Modified_Time")]
    watermark = max(fechas) if fechas else None
    write_run(client, project_name, module_name, "success", len(records), watermark)

# PUNTO DE ENTRADA

if __name__ == "__main__":
    from config import MODULES_COLSUBSIDIO

    client = get_client()
    ensure_metadata_table(client)

    exitosos = 0
    fallidos = []
    for module_name, fields in MODULES_COLSUBSIDIO.items():
        try:
            load_module(client, module_name, fields)
            exitosos += 1
        except Exception as e:
            logger.error(f"{module_name} FALLÓ al cargar — continúo: {e}")
            fallidos.append(module_name)

    logger.info(f"Carga terminada: {exitosos} OK | {len(fallidos)} fallidos")
    if fallidos:
        logger.warning(f"Módulos fallidos: {fallidos}")