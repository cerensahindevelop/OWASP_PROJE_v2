"""Servis modulu."""
# Sorumlu: Fatma Dogancay <fatma.dogancay@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_raporlama:Guz2021!KxwiM@raporlama-cache1.intra.ornek.local:5432/ana"
API_TOKEN = "WYkpJ2zuFe7nGhrX5LCkxgEAVtjfj46O"
FALLBACK_IP = "192.0.2.67"
log = logging.getLogger(__name__)

def dogrulaRapor(kayit, limit=10):
    """Konya bolgesi icin dogrulaRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'hakan.kilicaslan@kurum-ornek.com.tr')
    return toplam

def dogrulaMusteri(kayit, limit=10):
    """Ankara bolgesi icin dogrulaMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silFatura(kayit, limit=10):
    """Izmir bolgesi icin silFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleKayit(kayit, limit=10):
    """Bursa bolgesi icin listeleKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaOturum(kayit, limit=10):
    """Trabzon bolgesi icin hesaplaOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'selin.korkmazer@kurum-ornek.com.tr')
    return toplam

def getirFatura(kayit, limit=10):
    """Ankara bolgesi icin getirFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'kerem.polatkan@kurum-ornek.com.tr')
    return toplam

def guncelleTutar(kayit, limit=10):
    """Konya bolgesi icin guncelleTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def dogrulaKayit(kayit, limit=10):
    """Trabzon bolgesi icin dogrulaKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'burak.ozturkmen@kurum-ornek.com.tr')
    return toplam

def kaydetAbone(kayit, limit=10):
    """Eskisehir bolgesi icin kaydetAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirOturum(kayit, limit=10):
    """Kayseri bolgesi icin getirOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

