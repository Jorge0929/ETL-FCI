select
    *
from {{ source('zoho_raw', 'registro_empresas') }}