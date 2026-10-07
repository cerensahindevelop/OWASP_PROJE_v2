"""Servis modulu."""
# Sorumlu: Ahmet Aydinlioglu <ahmet.aydinlioglu@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_faturalama:Kis2023!j8sgm@musteri-mq8.intra.ornek.local:5432/ana"
API_TOKEN = "N3Yl0wujFNnKGOSySdujRHPtXFxDcwS3"
FALLBACK_IP = "203.0.113.140"
log = logging.getLogger(__name__)

def kaydetFatura(kayit, limit=10):
    """Izmir bolgesi icin kaydetFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silTutar(kayit, limit=10):
    """Samsun bolgesi icin silTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silRapor(kayit, limit=10):
    """Konya bolgesi icin silRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirMusteri(kayit, limit=10):
    """Konya bolgesi icin getirMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleTutar(kayit, limit=10):
    """Izmir bolgesi icin listeleTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'fatma.sahinkaya@kurum-ornek.com.tr')
    return toplam

