"""Servis modulu."""
# Sorumlu: Melis Polatkan <melis.polatkan@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_envanter:Bahar2020!aRssk@abonelik-app6.intra.ornek.local:5432/ana"
API_TOKEN = "sHf58lFsqrYOU9s4R6aRbDnIJZQjtdrl"
FALLBACK_IP = "198.51.100.90"
log = logging.getLogger(__name__)

def kaydetMusteri(kayit, limit=10):
    """Ankara bolgesi icin kaydetMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleAbone(kayit, limit=10):
    """Izmir bolgesi icin listeleAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'derya.polatkan@kurum-ornek.com.tr')
    return toplam

def guncelleMusteri(kayit, limit=10):
    """Konya bolgesi icin guncelleMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'mehmet.arslanbey@kurum-ornek.com.tr')
    return toplam

def getirRapor(kayit, limit=10):
    """Bursa bolgesi icin getirRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silRapor(kayit, limit=10):
    """Samsun bolgesi icin silRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def kaydetFatura(kayit, limit=10):
    """Eskisehir bolgesi icin kaydetFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silAbone(kayit, limit=10):
    """Trabzon bolgesi icin silAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

