"""Uretimde gozlemlenen 3 gercek hata icin regresyon testleri:
  1. aws_secret_access_key gibi anahtar kelimenin bilesik tanimlayicinin
     ORTASINDA kaldigi durumlar (generic_secret_assignment genellemesi).
  2. PEM formatli ozel anahtar bloklarinin (private_key_block) hic
     yakalanmiyor olmasi.
  3. URL-gomulu basic-auth kimlik bilgilerinin (http_basic_auth_credentials)
     yanlislikla EMAIL_ADDRESS olarak etiketlenmesi.

Ayrica proaktif olarak eklenen Google/GitHub/Slack/Stripe/JWT kurallarini
ve mevcut yanlis-pozitif korumalarinin (resecret_value/secretary) hala
gectigini dogrular.
"""

from __future__ import annotations

import ast
import asyncio
import re

import pytest
from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.services.mapping_service import MaskingRunContext, get_or_create_context, mask_text

_IDENTITY_PREFIX = "pytest-prodbugs"


def _cleanup_identity(project_name: str) -> None:
    with SessionLocal() as db:
        row = db.execute(
            sqltext(
                "SELECT id FROM maskeleme_baglamlari WHERE proje_adi=:p AND personel_no=:pn AND branch_adi=:b"
            ),
            {"p": project_name, "pn": "P-TEST-0001", "b": "pytest-branch"},
        ).first()
        if row is None:
            return
        context_id = row[0]
        run_ids = [
            r[0]
            for r in db.execute(
                sqltext("SELECT id FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id}
            ).all()
        ]
        for run_id in run_ids:
            db.execute(sqltext("DELETE FROM denetim_kaydi WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM gozden_gecirme_kuyrugu WHERE calisma_id=:r"), {"r": run_id})
            db.execute(sqltext("DELETE FROM denetim_uyarilari WHERE calisma_id=:r"), {"r": run_id})
        db.execute(sqltext("DELETE FROM maskeleme_calismalari WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM deger_eslemeleri WHERE baglam_id=:c"), {"c": context_id})
        db.execute(sqltext("DELETE FROM maskeleme_baglamlari WHERE id=:c"), {"c": context_id})
        db.commit()


def _mask(content: str, project_name: str, file_path: str = "sample.py") -> tuple[str, list]:
    identity = (project_name, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        context = get_or_create_context(db, *identity)
        run_ctx = MaskingRunContext(
            context=context,
            runtime_params={"project_name": identity[0], "sicil_no": identity[1], "branch_name": identity[2]},
        )
        masked_text, mappings = mask_text(db, run_ctx, content, file_path=file_path)
        db.commit()
    return masked_text, mappings


@pytest.fixture
def cleanup():
    created: list[str] = []
    yield created
    for project_name in created:
        _cleanup_identity(project_name)


def test_aws_secret_access_key_now_masked(cleanup):
    project = f"{_IDENTITY_PREFIX}-aws"
    cleanup.append(project)

    content = (
        'aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"\n'
        'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
    )
    masked, mappings = _mask(content, project)

    ast.parse(masked)
    assert "AKIAIOSFODNN7EXAMPLE" not in masked
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in masked, "aws_secret_access_key hala maskelenmiyor"
    assert len(mappings) == 2


def test_generic_secret_assignment_does_not_swallow_function_call(cleanup):
    # Gercek export'ta gozlemlenen hata: generic_secret_assignment'in eski
    # deger deseni ([^\s'"]{12,}) parantez/virgul gibi kod karakterlerini
    # de "deger" saniyordu - "secret_key = conf.get(section, key, ...)"
    # gibi bir satirin acilis parantezini yutup kapanisini disarida
    # birakiyor, Python sozdizimini bozuyordu.
    project = f"{_IDENTITY_PREFIX}-secretcall"
    cleanup.append(project)

    content = (
        "def get_signing_key(section, key):\n"
        "    sentinel = object()\n"
        "    secret_key = conf.get(section, key, fallback=sentinel)\n"
        "    return secret_key\n"
    )
    masked, mappings = _mask(content, project)

    ast.parse(masked)  # onceden burada SyntaxError firlardi
    assert masked == content, "kod ifadesi bir 'deger' sanilip yanlislikla maskelenmemeli"
    assert mappings == []


def test_generic_secret_assignment_still_masks_real_quoted_value(cleanup):
    # Regex daraltilirken gercek (tirnakli) deger yakalama yetenegi
    # bozulmamali.
    project = f"{_IDENTITY_PREFIX}-secretquoted"
    cleanup.append(project)

    content = 'db_password = "S3cr3tPassw0rdVal!123"\n'
    masked, mappings = _mask(content, project)

    ast.parse(masked)
    assert "S3cr3tPassw0rdVal!123" not in masked
    assert 'db_password = "' in masked, "identifier/atama/tirnaklar korunmali, sadece ic deger degismeli"
    assert len(mappings) == 1


def test_generic_secret_assignment_still_masks_unquoted_env_style_value(cleanup):
    project = f"{_IDENTITY_PREFIX}-secretenv"
    cleanup.append(project)

    content = "SECRET_KEY=abcDEF1234567890\n"
    masked, mappings = _mask(content, project)

    assert "abcDEF1234567890" not in masked
    assert len(mappings) == 1


def test_generic_secret_assignment_preserves_hyphenated_key_and_colon(cleanup):
    # Gercek export'ta gozlemlenen hata: "client-secret:" gibi tire (-) ile
    # birlesik bir anahtarin oneki ("client-") regex'in onek grubuna dahil
    # olmuyordu ("_" bekliyordu) ve "deger" grubu olmadigi donemde TUM
    # eslesme ("secret: <deger>") tek placeholder'a donusturuluyordu - sonuc
    # "client-mask_secret_N" oluyor, iki nokta ust uste kayboluyor ve YAML
    # sozdizimi bozuluyordu. "deger" grubu eklendikten sonra anahtar adi ve
    # ayirac (":") aynen kalmali, sadece deger placeholder'a donusmeli.
    project = f"{_IDENTITY_PREFIX}-secrethyphen"
    cleanup.append(project)

    content = (
        "client-secret: SYNTHETIC-CLIENT-SECRET-9842\n"
        "  api-key: SYNTHETIC-API-KEY-554433\n"
    )
    masked, mappings = _mask(content, project, file_path="sample.yaml")

    assert "SYNTHETIC-CLIENT-SECRET-9842" not in masked
    assert "SYNTHETIC-API-KEY-554433" not in masked
    assert "client-secret: " in masked, "anahtar adi ve ayirac korunmali"
    assert "  api-key: " in masked, "anahtar adi ve ayirac korunmali"
    assert len(mappings) == 2


def test_uuid_masked_as_single_atomic_span(cleanup):
    project = f"{_IDENTITY_PREFIX}-uuid"
    cleanup.append(project)

    content = 'resource_id = "550e8400-e29b-41d4-a716-446655440000"\n'
    masked, mappings = _mask(content, project)

    ast.parse(masked)
    assert "550e8400-e29b-41d4-a716-446655440000" not in masked
    assert len(mappings) == 1
    assert masked.count("-") == 0, "UUID tek parca maskelenmeli (placeholder'da tire olmamali), alt-parcalara bolunmemeli"


def test_url_masked_as_single_atomic_span_in_code_file(cleanup):
    # URL, Presidio'nun PatternRecognizer-tabanli (saf regex, NLP'ye
    # bagimli olmayan) UrlRecognizer'i ile kod/config dosyalari icin de
    # (dosya_tipi_kategori_kisitlamasi) acildi - "buyuk entity'ler tek
    # parca olarak maskelenmeli" hedefi geregi.
    project = f"{_IDENTITY_PREFIX}-url"
    cleanup.append(project)

    content = 'webhook_url = "https://internal.example.com/hooks/deploy?token=abc"\n'
    masked, mappings = _mask(content, project)

    ast.parse(masked)
    assert "https://internal.example.com/hooks/deploy?token=abc" not in masked
    assert len(mappings) == 1
    assert 'webhook_url = "mask_url_' in masked, "URL tek parca maskelenmeli (bolunmemeli)"


def test_structured_date_masked_as_single_atomic_span_in_code_file(cleanup):
    project = f"{_IDENTITY_PREFIX}-date"
    cleanup.append(project)

    content = 'deployed_on = "2024-01-15"\n'
    masked, mappings = _mask(content, project)

    ast.parse(masked)
    assert "2024-01-15" not in masked
    assert len(mappings) == 1
    assert 'deployed_on = "mask_date_time_' in masked


def test_private_key_block_masked_as_single_placeholder(cleanup):
    project = f"{_IDENTITY_PREFIX}-pk"
    cleanup.append(project)

    content = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUx\n"
        "OQAAACAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaAAAAJ\n"
        "-----END OPENSSH PRIVATE KEY-----\n"
    )
    masked, mappings = _mask(content, project)

    assert len(mappings) == 1
    assert masked.strip().startswith("mask_private_key_")
    assert "-----BEGIN" not in masked
    assert "-----END" not in masked
    assert "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ" not in masked


def test_pem_certificate_and_public_key_not_masked(cleanup):
    # private_key_block SADECE "...PRIVATE KEY..." bloklarini yakalamali -
    # sertifikalar/genel anahtarlar paylasilmasi gereken, hassas OLMAYAN
    # verilerdir.
    project = f"{_IDENTITY_PREFIX}-pubcert"
    cleanup.append(project)

    content = (
        "-----BEGIN CERTIFICATE-----\n"
        "MIIDXTCCAkWgAwIBAgIJAJC1HiIAZAiIMA0GCSqGSIb3DQEBCwUAMEUxCzAJBgNV\n"
        "-----END CERTIFICATE-----\n"
    )
    masked, mappings = _mask(content, project)

    assert mappings == []
    assert masked == content


def test_basic_auth_url_masked_as_credential_not_email(cleanup):
    project = f"{_IDENTITY_PREFIX}-basicauth"
    cleanup.append(project)

    content = 'endpoint = "https://admin:S3cr3tPass123@internal-service.example.com/basic_auth"\n'
    masked, mappings = _mask(content, project)

    ast.parse(masked)
    assert "mask_email" not in masked, "basic-auth kimlik bilgisi hala yanlislikla EMAIL olarak etiketleniyor"
    assert "S3cr3tPass123" not in masked
    assert "admin:" not in masked
    assert "https://" not in masked
    # TokenBoundaryValidator (Bolum 2) tasarimi geregi, eslesme bir string
    # literal ile kesisirse HER ZAMAN tam tirnak icerigine normallestirilir
    # (kismi degistirme yasak) - bu yuzden URL'in tamami tek placeholder'a
    # doner, sadece "user:pass@host" kismi degil. Sayac artik GLOBAL oldugu
    # icin (bkz. PlaceholderCounter) tam sayi degeri sabit degildir - sadece
    # sekli dogrulanir.
    assert re.fullmatch(r'endpoint = "mask_basic_auth_\d+"\n', masked)
    assert len(mappings) == 1


def test_basic_auth_without_password_not_falsely_matched(cleanup):
    # Sifresiz "user@host" (gercek bir e-posta olabilir) yanlislikla
    # basic-auth olarak yakalanmamali - bu kural SADECE ":" + "@" ikisi
    # birden URL authority'sinde bulunuyorsa devreye girer.
    project = f"{_IDENTITY_PREFIX}-noauth"
    cleanup.append(project)

    content = 'contact = "https://admin@example.com/path"\n'
    masked, mappings = _mask(content, project)

    assert "mask_basic_auth" not in masked


@pytest.mark.parametrize(
    "value,label",
    [
        ("AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actuX", "mask_google_api_key"),
        ("ghp_" + "A1b2C3d4" * 5, "mask_github_token"),
        ("xoxb-FAKE-TEST-TOKEN-NOT-REAL-VALUE", "mask_slack_token"),
        ("sk_live_" + "FAKETESTNOTREALVALUE1234", "mask_stripe_key"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ_ExampleSig123", "mask_jwt_token"),
    ],
)
def test_proactively_added_secret_formats_are_masked(cleanup, value, label):
    project = f"{_IDENTITY_PREFIX}-{label.lower()}"
    cleanup.append(project)

    content = f"TOKEN = '{value}'\n"
    masked, mappings = _mask(content, project)

    assert value not in masked, f"{label} formati hala maskelenmiyor"
    assert label in masked
    assert len(mappings) == 1


def test_parametric_value_as_substring_of_larger_secret_round_trips(cleanup, tmp_path):
    # Uretimde gozlemlenen hata: proje adi ("test") bir dosyadaki DAHA BUYUK
    # bir tirnakli degerin (orn. DB_PASSWORD = "test-2024-super-secret")
    # SADECE bir ON-EKI olarak geciyordu. TokenBoundaryValidator'in "kismi
    # string-literal degistirme YASAK" kurali (bkz. token_boundary_validator.py
    # _validate_one) bu eslesmeyi HAKLI OLARAK tum tirnak icerigini
    # kapsayacak sekilde genisletiyordu (match.start/end VE match.deger
    # birlikte guncelleniyor) - ama mapping_service.apply_detections,
    # pattern_type='parametric' oldugu icin genisletilmis match.original_value'yu
    # GORMEZDEN GELIP HER ZAMAN kisa runtime parametresini ("test")
    # sakliyordu. Sonuc: metinden TUM "test-2024-super-secret" kesilip
    # placeholder'a donuyor, ama geri donusum icin SADECE "test" saklaniyordu
    # - reverse_text() geri kalan "-2024-super-secret" kismini asla
    # kurtaramiyordu. Round-trip guard bunu yakalayip dosyayi TUMUYLE
    # disa aktarimdan haric tutuyordu (gercek olayda: bir Java dosyasi
    # sessizce export ZIP'inden kayboldu).
    project = "test"
    cleanup.append(project)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    original_content = 'String DB_PASSWORD = "test-2024-super-secret";\n'
    (source_dir / "Config.java").write_text(original_content, encoding="utf-8")
    target_dir = tmp_path / "target"

    import app.services.exporter as exporter_module

    identity = (project, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()

    exported = target_dir / "Config.java"
    assert exported.exists(), (
        "dosya round-trip dogrulamasi basarisiz oldugu icin sessizce disa aktarimdan "
        "haric tutuldu (basarisiz_dosyalar'a tasindi) - tam da uretimde gozlemlenen hata"
    )
    masked_content = exported.read_text(encoding="utf-8")
    assert "test-2024-super-secret" not in masked_content

    target_unmask_dir = tmp_path / "unmasked"
    from app.services.unmasker import unmask_project

    with SessionLocal() as db:
        unmask_report = unmask_project(
            db, source_path=str(target_dir), project_name=identity[0], sicil_no=identity[1],
            branch_name=identity[2], target_path=str(target_unmask_dir), initiated_by=identity[1],
        )
        db.commit()

    assert unmask_report.status == "completed"
    restored = (target_unmask_dir / "Config.java").read_text(encoding="utf-8")
    assert restored == original_content, "gizli degerin bir kismi geri donusumde kayboldu"


def test_large_file_ascii_head_with_turkish_tail_exports_without_encoding_crash(cleanup, tmp_path):
    # Kok neden: peek_classify() sadece dosyanin ilk PEEK_SIZE(=8192) baytini
    # orneklemeye bakarak encoding tahmin ediyordu, ama tum dosya bu tahminle
    # decode ediliyordu. Ornek pencere (ilk 8KB) saf ASCII, fakat 8KB'den
    # SONRAKI bir bolumde (orn. Turkce bir yorum satirinda) ASCII-disi
    # karakter varsa: tam-dosya decode "ascii" ile BASARISIZ olup "utf-8"
    # fallback'ine duserdi - ancak `encoding` degiskeni guncellenmedigi icin
    # sonraki write_text() cagrisi HALA yanlis "ascii" ile yazmaya calisirdi
    # -> "'ascii' codec can't encode characters ... ordinal not in range(128)".
    # Duzeltme: decode icin gercekten basarili olan candidate_encoding, artik
    # `encoding` degiskenine geri yaziliyor (hem exporter.py hem unmasker.py).
    project = f"{_IDENTITY_PREFIX}-encoding"
    cleanup.append(project)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    ascii_head = "# yorum satiri sadece ascii icerir\n" * 400  # > 8192 bayt
    assert len(ascii_head.encode("ascii")) > 8192
    turkish_tail = 'şifre = "deger"  # açıklama: şifre burada saklanıyor\n'
    (source_dir / "buyuk_dosya.py").write_text(ascii_head + turkish_tail, encoding="utf-8")
    target_dir = tmp_path / "target"

    import app.services.exporter as exporter_module

    identity = (project, "P-TEST-0001", "pytest-branch")
    with SessionLocal() as db:
        # export_project artik async - bkz. exporter.py modul dokstring'i.
        report = asyncio.run(
            exporter_module.export_project(
                db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
                branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
            )
        )
        db.commit()

    assert report.status == "completed"
    exported = (target_dir / "buyuk_dosya.py")
    assert exported.exists(), "buyuk, ascii-basli/turkce-sonlu dosya hedef klasore yazilamadi"
    assert exported.read_text(encoding="utf-8") == ascii_head + turkish_tail


def test_unmask_with_unicode_in_original_value_does_not_crash(cleanup, tmp_path):
    # Ikinci, ilgili ama FARKLI bir kok neden: "Geri Al" (unmask) adiminda
    # girdi dosyasi (placeholder iceren, hep ASCII) dogru sekilde "ascii"
    # olarak tespit edilir - bu kez tespit hatali degildir. Ancak
    # reverse_text() placeholder'lari DB'den cozulen GERCEK degerlerle
    # DEGISTIRIR - ve o gercek deger (orn. baska bir yerden yapistirilmis
    # bir sifre) U+202F (NARROW NO-BREAK SPACE) gibi ASCII-disi bir karakter
    # icerebilir. write_text_preserving_encoding() eklenmeden once, cikti
    # HALA girdinin "ascii" encoding'iyle yazilmaya calisilir ve
    # UnicodeEncodeError ile cokerdi - degismis/donusturulmus icerik,
    # kaynagin encoding'ine artik sigmayabilir.
    from app.core.crypto import encrypt_value, hash_value
    from app.db.models import ValueMapping
    from app.services.unmasker import unmask_project

    project = f"{_IDENTITY_PREFIX}-unmask-unicode"
    cleanup.append(project)
    identity = (project, "P-TEST-0001", "pytest-branch")

    original_secret = "S3cr3t Passw0rd"  # icinde dar kesilmez bosluk var
    with SessionLocal() as db:
        context = get_or_create_context(db, *identity)
        db.add(
            ValueMapping(
                context_id=context.id,
                rule_id=None,
                original_value_encrypted=encrypt_value(original_secret),
                original_value_plain=original_secret,
                original_value_hash=hash_value(context.id, original_secret),
                placeholder_value="mask_password_1",
            )
        )
        db.commit()

    source_dir = tmp_path / "masked_source"
    source_dir.mkdir()
    (source_dir / "config.py").write_text('password = "mask_password_1"\n', encoding="ascii")
    target_dir = tmp_path / "unmasked_target"

    with SessionLocal() as db:
        report = unmask_project(
            db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
            branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
        )
        db.commit()

    assert report.status == "completed_with_warnings"  # No source-integrity manifest in this legacy fixture.
    output = (target_dir / "config.py").read_text(encoding="utf-8")
    assert output == f'password = "{original_secret}"\n'


def test_global_counter_prevents_cross_context_placeholder_collision(cleanup):
    # Kok neden duzeltmesinin asil kaniti: sayac artik context basina degil,
    # placeholder oneki basina GLOBAL (bkz. PlaceholderCounter, atomik
    # UPSERT). Gercek mask_text() pipeline'i uzerinden IKI FARKLI context
    # ayni turden bir sirri maskeleriz - onceden (context-bazli sayacla) bu
    # ikisi TESADUFEN ayni placeholder metnini ("mask_secret_1") uretirdi;
    # artik BUNU YAPMALARI MATEMATIKSEL OLARAK IMKANSIZ.
    project_a = f"{_IDENTITY_PREFIX}-global-ctr-a"
    project_b = f"{_IDENTITY_PREFIX}-global-ctr-b"
    cleanup.append(project_a)
    cleanup.append(project_b)

    masked_a, mappings_a = _mask('token = "AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actuA"\n', project_a)
    masked_b, mappings_b = _mask('token = "AIzaSyBUPHAjZl3n8Eza66ka6JbNv9qeg_actuB"\n', project_b)

    placeholder_a = re.search(r"mask_google_api_key_\d+", masked_a).group()
    placeholder_b = re.search(r"mask_google_api_key_\d+", masked_b).group()
    assert placeholder_a != placeholder_b, "iki farkli context AYNI placeholder metnini uretti - global sayac calismiyor"
    assert len(mappings_a) == 1
    assert len(mappings_b) == 1


def test_unmask_wrong_identity_suggests_the_matching_context(cleanup, tmp_path):
    # Gercek uretim vakasi: kullanici "gitleaks" kimligiyle maskelenmis bir
    # klasoru, yanlislikla "secrettest" kimligiyle geri almaya calisti.
    # Placeholder formati (<ENTITY_TIPI>_<SIRA_NO>) TASARIM GEREGI sadece
    # KENDI context'i icinde benzersizdir - iki FARKLI context'in KUCUK
    # sayili sayaclari (_TEST_1, _TEST_2 gibi) TESADUFEN ortusebilir. Bu da
    # "az sayida cozuldu, COGU cozulemedi" seklinde kafa karistirici bir
    # sonuc dogurur - kullanici bunu "sistem hatasi" sanabilir. Duzeltme:
    # IdentityMismatchAdvisor, cozulemeyen placeholder kumesini DIGER
    # context'lerle karsilastirip en yuksek ortusmeye sahip olani bir ONERI
    # olarak sunar (degeri ASLA cozmez/tahmin etmez - guvenlik degismezi
    # korunur).
    import uuid

    from app.core.crypto import encrypt_value, hash_value
    from app.db.models import ValueMapping
    from app.services.unmasker import unmask_project

    # Paylasilan gelistirme veritabaninda BASKA (gercek) context'lerin de
    # kucuk sayili SECRET_TEST_N placeholder'lari olabilir (bu senaryonun
    # ta kendisi!) - testin kendi izolasyonunu bozmamasi icin rastgele,
    # bu calistirmaya OZGU bir on-ek kullanilir.
    unique = uuid.uuid4().hex[:10].upper()
    prefix = f"UNIQ{unique}_TEST"

    wrong_project = f"{_IDENTITY_PREFIX}-mismatch-wrong"
    right_project = f"{_IDENTITY_PREFIX}-mismatch-right"
    cleanup.append(wrong_project)
    cleanup.append(right_project)
    # _cleanup_identity() sabit ("P-TEST-0001", "pytest-branch") kimligini
    # arar - iki context'i de bu ayni ikili ile olusturuyoruz (project_name
    # zaten aralarinda tek basina yeterli ayirt edicidir), boylece testten
    # sonra ikisi de duzgunce temizlenir.
    wrong_identity = (wrong_project, "P-TEST-0001", "pytest-branch")
    right_identity = (right_project, "P-TEST-0001", "pytest-branch")

    with SessionLocal() as db:
        wrong_context = get_or_create_context(db, *wrong_identity)
        right_context = get_or_create_context(db, *right_identity)

        # "right" context: dosyanin GERCEKTEN ait oldugu, 25 farkli sirri
        # maskelemis olan kimlik.
        for i in range(1, 26):
            db.add(
                ValueMapping(
                    context_id=right_context.id, rule_id=None,
                    original_value_encrypted=encrypt_value(f"right-secret-{i}"),
                    original_value_plain=f"right-secret-{i}",
                    original_value_hash=hash_value(right_context.id, f"right-secret-{i}"),
                    placeholder_value=f"{prefix}_{i}",
                )
            )
        # "wrong" context: TAMAMEN ILGISIZ, kendi bagimsiz sayaciyla
        # olusturulmus, sadece ILK 3 sayida TESADUFEN ayni metne sahip
        # (prefix_1..3) kucuk bir kimlik.
        for i in range(1, 4):
            db.add(
                ValueMapping(
                    context_id=wrong_context.id, rule_id=None,
                    original_value_encrypted=encrypt_value(f"wrong-secret-{i}"),
                    original_value_plain=f"wrong-secret-{i}",
                    original_value_hash=hash_value(wrong_context.id, f"wrong-secret-{i}"),
                    placeholder_value=f"{prefix}_{i}",
                )
            )
        db.commit()

    source_dir = tmp_path / "masked_source"
    source_dir.mkdir()
    content = "\n".join(f'secret_{i} = "{prefix}_{i}"' for i in range(1, 26)) + "\n"
    (source_dir / "secrets.py").write_text(content, encoding="ascii")
    target_dir = tmp_path / "unmasked_target"

    with SessionLocal() as db:
        report = unmask_project(
            db, source_path=str(source_dir), project_name=wrong_identity[0],
            sicil_no=wrong_identity[1], branch_name=wrong_identity[2],
            target_path=str(target_dir), initiated_by=wrong_identity[1],
        )
        db.commit()

    assert report.total_placeholders_found == 25
    assert report.total_placeholders_resolved == 0, "projeler arasi cakisan token'lar tahmin edilmemeli"
    assert report.total_placeholders_unresolved == 25

    suggestion = report.identity_mismatch_suggestion
    assert suggestion is not None, "22/25 cozulemedigine ragmen kimlik uyusmazligi onerisi uretilmedi"
    assert suggestion.project_name == right_identity[0]
    assert suggestion.sicil_no == right_identity[1]
    assert suggestion.branch_name == right_identity[2]
    assert suggestion.matched_count == 25
    assert suggestion.unresolved_count == 25


def test_unmask_small_scale_false_positive_does_not_trigger_mismatch_noise(cleanup, tmp_path):
    # Kucuk olcekli, TEK bir tesadufi PREFIX_TEST_N eslesmesi (asgari ornek
    # esiginin altinda) bir "kimlik uyusmazligi" onerisi TETIKLEMEMELI -
    # aksi halde her kucuk dosyada gurultulu/yanlis alarm uretilir.
    from app.services.unmasker import unmask_project

    project = f"{_IDENTITY_PREFIX}-no-mismatch-noise"
    cleanup.append(project)
    identity = (project, "P-TEST-0001", "pytest-branch")

    with SessionLocal() as db:
        get_or_create_context(db, *identity)
        db.commit()

    source_dir = tmp_path / "masked_source"
    source_dir.mkdir()
    # Bu context'te HIC eslesme yok - ama bu metin sadece 1 "placeholder
    # bicimli" token iceriyor, asgari ornek boyutunun (20) cok altinda.
    (source_dir / "consts.py").write_text('LIMIT = "mask_rate_1"\n', encoding="ascii")
    target_dir = tmp_path / "unmasked_target"

    with SessionLocal() as db:
        report = unmask_project(
            db, source_path=str(source_dir), project_name=identity[0], sicil_no=identity[1],
            branch_name=identity[2], target_path=str(target_dir), initiated_by=identity[1],
        )
        db.commit()

    assert report.total_placeholders_unresolved == 1
    assert report.identity_mismatch_suggestion is None


def test_existing_false_positive_guards_still_hold(cleanup):
    # Bu duzeltmeler, ONCEKI bolumlerde (Bolum 4) dogrulanan yanlis-pozitif
    # korumalarini (alt-cizgisiz "resecret_value"/"secretary" gibi kelimeler)
    # BOZMAMALI.
    project = f"{_IDENTITY_PREFIX}-falsepos"
    cleanup.append(project)

    content = (
        'resecret_value = "shouldnotmatch1234567890"\n'
        'secretary = "shouldnotmatch1234567890ab"\n'
    )
    masked, mappings = _mask(content, project)

    assert masked == content
    assert mappings == []
