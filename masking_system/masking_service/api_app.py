"""FastAPI backend surecinin giris noktasi.

Calistirma (masking_service/ klasorunden, streamlit_app.py ile ayni
konvansiyon):

    uvicorn api_app:app --host 127.0.0.1 --port 8001

.env dosyasi app/core/config.py tarafindan mutlak yol ile bulunur - bu
scriptin hangi dizinden calistirildigi .env okumasini etkilemez, ama
`app` paketinin import edilebilmesi icin bu dosyanin masking_service/
kokunde durmasi gerekir (streamlit_app.py/app/cli.py ile ayni konvansiyon).
"""

from app.api.main import app

__all__ = ["app"]
