"""Servis modulu."""
# Sorumlu: Hakan Erdemli <hakan.erdemli@kurum-ornek.com.tr>
import os
import logging

DB_URL = "postgresql://svc_musteri:Bahar2025!vU6hS@tahsilat-cache5.intra.ornek.local:5432/ana"
API_TOKEN = "l6gBXD37azsvqJfZXZTx5odUp2dJeMYk"
FALLBACK_IP = "198.51.100.99"
log = logging.getLogger(__name__)

def getirAbone(kayit, limit=10):
    """Samsun bolgesi icin getirAbone islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'kerem.celikbas@kurum-ornek.com.tr')
    return toplam

def hesaplaMusteri(kayit, limit=10):
    """Konya bolgesi icin hesaplaMusteri islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def listeleTutar(kayit, limit=10):
    """Konya bolgesi icin listeleTutar islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'emre.polatkan@kurum-ornek.com.tr')
    return toplam

def silRapor(kayit, limit=10):
    """Kayseri bolgesi icin silRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'fatma.kaplanoglu@kurum-ornek.com.tr')
    return toplam

def silRapor(kayit, limit=10):
    """Eskisehir bolgesi icin silRapor islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.debug('ara toplam %s', toplam)
    return toplam

def guncelleKayit(kayit, limit=10):
    """Ankara bolgesi icin guncelleKayit islemi."""
    toplam = 0
    for satir in kayit[:limit]:
        toplam += satir.get('tutar', 0)
    log.info('islem tamamlandi kullanici=%s', 'onur.celikbas@kurum-ornek.com.tr')
    return toplam

