"""LLM baglanti tanisi: .env'in okunup okunmadigini, proxy'yi, model adini ve
uygulamanin gonderdigi gercek tespit istegini adim adim kontrol eder.

Run from masking_service:  .venv\\Scripts\\python scripts\\check_llm_connection.py
Sadece sentetik kisa bir metin gonderilir; DB'ye/ciktiya hicbir sey yazilmaz.
"""
import asyncio
import sys
import urllib.request
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config  # noqa: E402

SAMPLE = 'SERVICE_NAME = "atlas-billing-core"  # sahibi: Ayse Yilmaz\n'


def step(title):
    print(f"\n== {title}")


def main() -> int:
    step("1) .env dosyasi")
    env_file = config._ENV_FILE
    print(f"aranan yol : {env_file}")
    print(f"var mi     : {env_file.exists()}")
    if not env_file.exists():
        print("SORUN: .env yok -> VLLM_ENABLED varsayilan false, LLM sessizce kapali calisir.")

    step("2) Yuklenen VLLM ayarlari")
    try:
        s = config.VLLMSettings()
    except Exception as exc:  # noqa: BLE001 - tanida her hatayi gostermek isteriz
        print(f"SORUN: ayarlar yuklenemedi: {exc}")
        return 1
    for key in ("enabled", "host", "model", "timeout_seconds", "max_file_chars",
                "max_tokens", "max_concurrent_requests"):
        print(f"{key:24}: {getattr(s, key)}")
    print(f"{'api_key':24}: {'tanimli' if s.api_key else 'yok'}")
    if not s.enabled:
        print("SORUN: VLLM_ENABLED=false -> LLM katmani kapali.")
        return 1

    step("3) Proxy")
    proxies = urllib.request.getproxies()
    print(f"algilanan proxy: {proxies or 'yok'}")
    if proxies:
        print("NOT: httpx bu proxy'yi kullanir. vLLM sunucusu NO_PROXY/no_proxy "
              "listesinde degilse istek proxy'ye gider.")

    headers = {"Authorization": f"Bearer {s.api_key}"} if s.api_key else None
    base = s.host.rstrip("/")

    step("4) /v1/models (proxy'li ve proxy'siz)")
    served = None
    for trust_env in (True, False):
        label = "proxy AYARLARIYLA" if trust_env else "proxy'siz (dogrudan)"
        try:
            with httpx.Client(trust_env=trust_env, timeout=15) as client:
                r = client.get(f"{base}/v1/models", headers=headers)
            print(f"{label:22}: HTTP {r.status_code}")
            if r.status_code == 200:
                served = [m.get("id") for m in r.json().get("data", [])]
            else:
                print(f"  yanit: {r.text[:300]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label:22}: HATA {type(exc).__name__}: {exc}")
    if served is None:
        print("SORUN: sunucuya ulasilamadi (adres/port/firewall/api key/proxy).")
        return 1
    print(f"sunucudaki modeller: {served}")
    if s.model not in served:
        print(f"SORUN: VLLM_MODEL={s.model!r} sunucuda yok -> 404. Yukaridaki id'lerden birini yazin.")
        return 1

    step("5) Uygulamanin gercek tespit istegi")
    from app.services.llm_recognizer import (  # noqa: E402
        LLMRecognitionError, build_detection_request, call_vllm, parse_and_verify_detections,
    )
    payload = build_detection_request(
        SAMPLE, s.model, s.seed, max_tokens=s.max_tokens, disable_thinking=s.disable_thinking,
        presence_penalty=s.presence_penalty, reasoning_effort=s.reasoning_effort,
    )
    try:
        raw = asyncio.run(call_vllm(s.host, s.timeout_seconds, payload, s.api_key))
    except LLMRecognitionError as exc:
        print(f"SORUN: istek basarisiz: {exc}")
        print("  HTTP 400 ise: max-model-len kucuk ya da response_format/json_schema desteklenmiyor olabilir.")
        return 1
    choice = (raw.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    print(f"finish_reason: {choice.get('finish_reason')!r}")
    print(f"usage        : {raw.get('usage')}")
    print(f"content      : {content[:400]}")
    if "<think>" in content or (choice.get("message") or {}).get("reasoning_content"):
        print("SORUN: model thinking modunda -> token tuketir/JSON bozulur. Thinking kapatilmali.")
    try:
        found = parse_and_verify_detections(raw, SAMPLE, [])
    except LLMRecognitionError as exc:
        print(f"SORUN: yanit uygulama tarafindan kabul edilmedi: {exc}")
        return 1
    print(f"dogrulanan bulgular: {[(d.deger, d.tip) for d in found]}")
    print("\nSONUC: LLM baglantisi ve yanit formati CALISIYOR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
