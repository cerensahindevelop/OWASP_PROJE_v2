"""Servis modulu."""
# Sorumlu: Ceren Arslanbey <ceren.arslanbey@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_kimlik:Yaz2022!P8K1j@musteri-mq4.intra.ornek.local:5432/ana"
API_TOKEN = "AAY2qtjSpwx9XbAbAPp3VrKAN4JJs927"
FALLBACK_IP = "192.0.2.31"
log = logging.getLogger(__name__)

def listeleKayit(kayit, limit=10):
    """Konya bolgesi icin listeleKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleTutar(kayit, limit=10):
    """Kayseri bolgesi icin listeleTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def guncelleTutar(kayit, limit=10):
    """Izmir bolgesi icin guncelleTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'fatma.kilicaslan@kurum-ornek.com.tr')
    return toplam

def guncelleRapor(kayit, limit=10):
    """Eskisehir bolgesi icin guncelleRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaKayit(kayit, limit=10):
    """Eskisehir bolgesi icin hesaplaKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def dogrulaMusteri(kayit, limit=10):
    """Konya bolgesi icin dogrulaMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'ahmet.demirkiran@kurum-ornek.com.tr')
    return toplam

def dogrulaKayit(kayit, limit=10):
    """Samsun bolgesi icin dogrulaKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def kaydetMusteri(kayit, limit=10):
    """Ankara bolgesi icin kaydetMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaAbone(kayit, limit=10):
    """Kayseri bolgesi icin hesaplaAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirKayit(kayit, limit=10):
    """Izmir bolgesi icin getirKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleKayit(kayit, limit=10):
    """Kayseri bolgesi icin listeleKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirKayit(kayit, limit=10):
    """Trabzon bolgesi icin getirKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'derya.sahinkaya@kurum-ornek.com.tr')
    return toplam

def hesaplaRapor(kayit, limit=10):
    """Konya bolgesi icin hesaplaRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

