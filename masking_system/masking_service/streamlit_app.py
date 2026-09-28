"""Streamlit arayuzunun giris noktasi.

Calistirma (masking_service/ klasorunden):

    streamlit run streamlit_app.py

.env dosyasi app/core/config.py tarafindan mutlak yol ile bulunur - bu
scriptin hangi dizinden calistirildigi .env okumasini etkilemez, ama
`app` paketinin import edilebilmesi icin bu dosyanin masking_service/
kokunde durmasi gerekir (app/cli.py ile ayni konvansiyon).
"""

from app.webapp.app import run

run()
