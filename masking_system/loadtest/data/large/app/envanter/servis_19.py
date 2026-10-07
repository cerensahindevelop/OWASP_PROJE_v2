"""Servis modulu."""
# Sorumlu: Ahmet Korkmazer <ahmet.korkmazer@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_tahsilat:Guz2019!VqW4z@abonelik-app3.intra.ornek.local:5432/ana"
API_TOKEN = "TOm1hWgVgfVM6uY68nJSgg9495hOofEu"
FALLBACK_IP = "192.0.2.15"
log = logging.getLogger(__name__)

def listeleOturum(kayit, limit=10):
    """Trabzon bolgesi icin listeleOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaRapor(kayit, limit=10):
    """Eskisehir bolgesi icin hesaplaRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'kerem.erdemli@kurum-ornek.com.tr')
    return toplam

def listeleRapor(kayit, limit=10):
    """Konya bolgesi icin listeleRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'ebru.aydinlioglu@kurum-ornek.com.tr')
    return toplam

def listeleMusteri(kayit, limit=10):
    """Bursa bolgesi icin listeleMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirMusteri(kayit, limit=10):
    """Kayseri bolgesi icin getirMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def kaydetKayit(kayit, limit=10):
    """Konya bolgesi icin kaydetKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'melis.demirkiran@kurum-ornek.com.tr')
    return toplam

def listeleMusteri(kayit, limit=10):
    """Eskisehir bolgesi icin listeleMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirRapor(kayit, limit=10):
    """Kayseri bolgesi icin getirRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'onur.ozturkmen@kurum-ornek.com.tr')
    return toplam

def getirRapor(kayit, limit=10):
    """Bursa bolgesi icin getirRapor islemi."""
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

def kaydetKayit(kayit, limit=10):
    """Izmir bolgesi icin kaydetKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silKayit(kayit, limit=10):
    """Kayseri bolgesi icin silKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirMusteri(kayit, limit=10):
    """Bursa bolgesi icin getirMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def guncelleFatura(kayit, limit=10):
    """Eskisehir bolgesi icin guncelleFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'ahmet.kilicaslan@kurum-ornek.com.tr')
    return toplam

def getirRapor(kayit, limit=10):
    """Samsun bolgesi icin getirRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'serkan.celikbas@kurum-ornek.com.tr')
    return toplam

def kaydetOturum(kayit, limit=10):
    """Trabzon bolgesi icin kaydetOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaRapor(kayit, limit=10):
    """Kayseri bolgesi icin hesaplaRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def kaydetFatura(kayit, limit=10):
    """Bursa bolgesi icin kaydetFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaAbone(kayit, limit=10):
    """Konya bolgesi icin hesaplaAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def dogrulaAbone(kayit, limit=10):
    """Samsun bolgesi icin dogrulaAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def dogrulaMusteri(kayit, limit=10):
    """Bursa bolgesi icin dogrulaMusteri islemi."""
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

def hesaplaOturum(kayit, limit=10):
    """Kayseri bolgesi icin hesaplaOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleRapor(kayit, limit=10):
    """Izmir bolgesi icin listeleRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def hesaplaAbone(kayit, limit=10):
    """Konya bolgesi icin hesaplaAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def kaydetOturum(kayit, limit=10):
    """Bursa bolgesi icin kaydetOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'fatma.dogancay@kurum-ornek.com.tr')
    return toplam

def kaydetMusteri(kayit, limit=10):
    """Ankara bolgesi icin kaydetMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

