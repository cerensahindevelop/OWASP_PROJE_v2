"""Servis modulu."""
# Sorumlu: Fatma Gunduzer <fatma.gunduzer@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_envanter:Kis2024!aPcRR@bildirim-app5.intra.ornek.local:5432/ana"
API_TOKEN = "ioSpfa6Mccbn8ygEqhI5jV6AmqfPVYGw"
FALLBACK_IP = "198.51.100.34"
log = logging.getLogger(__name__)

def listeleFatura(kayit, limit=10):
    """Izmir bolgesi icin listeleFatura islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def getirRapor(kayit, limit=10):
    """Konya bolgesi icin getirRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def dogrulaMusteri(kayit, limit=10):
    """Eskisehir bolgesi icin dogrulaMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def dogrulaOturum(kayit, limit=10):
    """Ankara bolgesi icin dogrulaOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'mehmet.erdemli@kurum-ornek.com.tr')
    return toplam

def listeleOturum(kayit, limit=10):
    """Izmir bolgesi icin listeleOturum islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleTutar(kayit, limit=10):
    """Trabzon bolgesi icin listeleTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def silRapor(kayit, limit=10):
    """Bursa bolgesi icin silRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'ayse.arslanbey@kurum-ornek.com.tr')
    return toplam

def silKayit(kayit, limit=10):
    """Eskisehir bolgesi icin silKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'tolga.korkmazer@kurum-ornek.com.tr')
    return toplam

