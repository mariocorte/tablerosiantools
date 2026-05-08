"""Configuracion de conexiones de base de datos para diag_cedulas.py.

Copiar este archivo como `db_config.py` y completar las credenciales reales.
`db_config.py` esta ignorado por git para no subir secretos.
"""

DATABASES = {
    "PROD": {
        "DESTINO": {
            "host": "",
            "port": 5432,
            "dbname": "",
            "user": "",
            "password": "",
        },
        "ED": {
            "host": "",
            "port": 5432,
            "dbname": "",
            "user": "",
            "password": "",
        },
    },
    "TEST": {
        "DESTINO": {
            "host": "",
            "port": 5432,
            "dbname": "",
            "user": "",
            "password": "",
        },
        "ED": {
            "host": "",
            "port": 5432,
            "dbname": "",
            "user": "",
            "password": "",
        },
    },
}
