"""HTTP hata türünü koruyan, istek/yanıt içeriği sızdırmayan tanı bilgisi."""
from __future__ import annotations

import httpx


def http_error_detail(
    exc: Exception, *, layer: str, elapsed_seconds: float, timeout_seconds: float
) -> str:
    # str(exc), URL, header ve yanıt gövdesi bilerek dahil edilmez: bunlar
    # kimlik bilgisi veya taranan dosyadan içerik taşıyabilir. ReadTimeout
    # gibi istisnaların metni ayrıca çoğu zaman tamamen boştur.
    reasons = (
        (httpx.ReadTimeout, "Yanıt verisi bekleme süresi aşıldı."),
        (httpx.ConnectTimeout, "Bağlantı kurma süresi aşıldı."),
        (httpx.WriteTimeout, "İstek verisi gönderme süresi aşıldı."),
        (httpx.PoolTimeout, "İstemci bağlantı havuzunda bekleme süresi aşıldı."),
        (httpx.TimeoutException, "HTTP işlemi zaman aşımına uğradı."),
        (httpx.ConnectError, "Sunucuya bağlantı kurulamadı."),
        (httpx.RemoteProtocolError, "Sunucu veya aradaki ağ bileşeni geçerli HTTP yanıtını tamamlamadı."),
        (httpx.NetworkError, "Veri aktarımı sırasında bağlantı hatası oluştu."),
        (httpx.HTTPStatusError, "HTTP sunucusu hata durum kodu döndürdü."),
    )
    reason = next((text for kind, text in reasons if isinstance(exc, kind)), "İstek veya yanıt işlenemedi.")
    status = f"; HTTP={exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else ""
    return (
        f"katman={layer}; hata={type(exc).__name__}{status}; "
        f"gecen_saniye={elapsed_seconds:.2f}; timeout_ayari_saniye={timeout_seconds:g}. {reason}"
    )
