import logging
from datetime import datetime, timezone

from google.cloud import bigquery

logger = logging.getLogger(__name__)

# NOTA: estos constantes están duplicados en loader.py. A futuro conviene
# centralizarlos en config.py para que no se desincronicen.
PROJECT_ID = "zoho-bq-pipeline-492116"
DATASET_ID = "colsubsidio_ruta_empresas"
METADATA_TABLE = f"{PROJECT_ID}.{DATASET_ID}.pipeline_metadata"


def ensure_metadata_table(client):
    """
    Crea pipeline_metadata si no existe. Una fila por módulo por corrida (historial).
    - status:    success | empty | failed
    - watermark: MAX(Modified_Time) de lo cargado en esa corrida; NULL si vacío/falló.
                 De acá sale el since incremental: MAX(watermark) por módulo.
    """
    schema = [
        bigquery.SchemaField("project_name", "STRING"),
        bigquery.SchemaField("module_name", "STRING"),
        bigquery.SchemaField("run_at", "TIMESTAMP"),
        bigquery.SchemaField("status", "STRING"),
        bigquery.SchemaField("records_loaded", "INTEGER"),
        bigquery.SchemaField("watermark", "TIMESTAMP"),
    ]
    table = bigquery.Table(METADATA_TABLE, schema=schema)
    client.create_table(table, exists_ok=True)


def get_watermark(client, project_name, module_name):
    """
    Devuelve el since para un módulo: el MAX(watermark) de TODAS sus corridas.
    - None → el módulo nunca se cargó con datos → full refresh.
    - Los runs vacíos tienen watermark NULL y MAX(...) los ignora automáticamente,
      así que la marca no retrocede cuando un módulo viene sin datos.

    Se usa parámetro (@project / @module), nunca f-string con el valor, para
    evitar inyección y problemas con comillas.
    """
    q = f"""
    SELECT MAX(watermark) AS since
    FROM `{METADATA_TABLE}`
    WHERE project_name = @project AND module_name = @module
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("project", "STRING", project_name),
        bigquery.ScalarQueryParameter("module", "STRING", module_name),
    ])
    row = list(client.query(q, job_config=job_config).result())[0]
    since = row["since"]
    if since is None:
        return None
    # Zoho espera un string ISO 8601 en el header If-Modified-Since
    return since.isoformat()


def write_run(client, project_name, module_name, status, records_loaded, watermark):
    """
    Agrega una fila de auditoría por corrida.

    Args:
        status: "success" | "empty" | "failed"
        records_loaded: int
        watermark: string ISO 8601 (MAX Modified_Time) o None

    Usa insert_rows_json (streaming) — sin job, inmediato. Como solo agregamos
    filas y nunca las editamos, las limitaciones del streaming buffer no aplican.
    """
    row = {
        "project_name": project_name,
        "module_name": module_name,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "records_loaded": records_loaded,
        "watermark": watermark,
    }
    errors = client.insert_rows_json(METADATA_TABLE, [row])
    if errors:
        logger.error(f"pipeline_metadata: error insertando fila de {module_name}: {errors}")
    else:
        logger.info(f"pipeline_metadata: {module_name} | {status} | watermark={watermark}")
