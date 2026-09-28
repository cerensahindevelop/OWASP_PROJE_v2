# SQLAlchemy'nin kolon tipleri, kisitlamalari (constraint) ve ORM
# yardimcilari - bu dosyadaki tum tablo/model tanimlarinin temelini olusturur.
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    Float,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
# Base sinifi, tipli kolon tanimlari (Mapped/mapped_column) ve iliskiler
# (relationship) icin ORM temel bilesenleri.
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# Tum SQLAlchemy modellerinin turedigi temel (base) sinif.
class Base(DeclarativeBase):
    pass


# Veritabaninda tutulan, veri-odakli (data-driven) filtre kurali satiri.
# Yeni bir kural = yeni bir satir; kod degisikligi/deploy gerektirmez.
#
# DB'deki fiziksel tablo/kolon adlari Turkce'dir (name= ile eslendi); Python
# tarafindaki ozellik adlari (orn. rule_name) bilincli olarak Ingilizce
# birakildi - servis katmani, testler ve CLI hicbir degisiklik gerektirmesin
# diye. mapped_column(name=...) ile Python <-> DB adi eslemesi yapilir.
#
# Her tablo/kolona SQL seviyesinde COMMENT da eklenmistir (comment=...) -
# boylece psql/DBeaver/pgAdmin gibi bir araçla DB'yi dogrudan acan biri de,
# Python kaynak koduna bakmadan, her tablo/kolonun ne ise yaradigini gorur.
class FilterRule(Base):
    """Data-driven detection rules. New rule = new row, no deploy needed.

    pattern_type distinguishes the three natures of a rule:
      - 'regex':      static pattern (IP, e-mail, secret) - same regex applies
                       to every project, matched directly against file content.
      - 'parametric': the concrete value is NOT known in advance (project
                       name, sicil no, branch name). regex_pattern is
                       NULL; at scan time the caller supplies the actual
                       value for `category` via runtime params, and the
                       engine builds a literal whole-word match from it.
      - 'llm':        no fixed format exists to write a regex for (a person's
                       name, a home address). regex_pattern/validator_name are
                       NULL; `description` is instead the instruction sent to
                       the local LLM (app/services/llm_recognizer.py) telling
                       it what to look for. Every value the LLM reports is
                       re-verified as a literal substring of the scanned text
                       before being trusted - the LLM never invents a match.
    """

    __tablename__ = "filtre_kurallari"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    rule_name: Mapped[str] = mapped_column(
        "kural_adi", String(100), unique=True, nullable=False,
        comment="Kuralin benzersiz adi (orn. email_address, generic_secret_assignment).",
    )
    category: Mapped[str] = mapped_column(
        "kategori", String(50), nullable=False,
        comment="Parametrik kurallarda calisma-zamani parametre anahtari (orn. project_name); "
        "regex kurallarda sadece gruplama/etiket amaclidir.",
    )
    source_layer: Mapped[str] = mapped_column(
        "kaynak_katman", String(50), nullable=False, default="katman1",
        comment="'katman1', 'katman2_presidio' ya da 'llm'. Ayni filtre_kurallari "
        "tablosunun hangi detector tarafindan okunacagini belirler.",
    )
    pattern_type: Mapped[str] = mapped_column(
        "desen_tipi", String(20), nullable=False,
        comment="'regex' (sabit desen), 'parametric' (deger calisma zamaninda saglanir), "
        "'llm' (yerel LLM ile taranan veri) ya da 'presidio' (Katman 2 Presidio pattern kuralı).",
    )
    regex_pattern: Mapped[str | None] = mapped_column(
        "regex_deseni", Text, nullable=True,
        comment="pattern_type='regex' icin aranacak regex; parametrik/llm kurallarda NULL. "
        "desen_sifreli_mi=True ise bu kolon duz metin regex DEGIL, Fernet ile sifrelenmis "
        "regex metnidir (bkz. app/core/crypto.py, app/repository/filter_rule_repository.py).",
    )
    is_pattern_encrypted: Mapped[bool] = mapped_column(
        "desen_sifreli_mi", Boolean, nullable=False, default=False,
        comment="True ise regex_deseni duz metin degil, Fernet ile sifrelenmis regex metnidir - "
        "kurumsal terim sozlugu gibi, kendisi hassas olan kural kaynaklari icin kullanilir "
        "(bkz. app/services/term_upload.py). Okuyan taraf (filter_rule_repository.py) "
        "list_active_detection_rules()'ta bu bayragi gorup decrypt_value() cagirir.",
    )
    corporate_term_encrypted: Mapped[str | None] = mapped_column(
        "kurumsal_terim_sifreli", Text, nullable=True,
        comment="Yalnizca kurumsal_terim_ onekli sozluk kayitlarinda, kullanicinin ekledigi "
        "orijinal kurumsal ifadenin Fernet ile sifrelenmis hali; listeleme/silme arayuzu "
        "regex desenini tersine cevirmeye gerek kalmadan bu alani kullanir.",
    )
    corporate_term_deleted_at: Mapped[object | None] = mapped_column(
        "kurumsal_terim_silinme_tarihi", DateTime(timezone=True), nullable=True,
        comment="NULL ise kurumsal terim kaydi gorunur; doluysa kullanici tarafindan soft-delete "
        "edilmistir. Kural satiri ve eski mapping FK'lari restore gecmisini korumak icin tutulur.",
    )
    regex_flags: Mapped[str | None] = mapped_column(
        "regex_bayraklari", String(10), nullable=True,
        comment="Regex bayraklari (orn. 'i' = buyuk/kucuk harf duyarsiz).",
    )
    validator_name: Mapped[str | None] = mapped_column(
        "dogrulayici_adi", String(50), nullable=True,
        comment="Regex eslesmesinden sonra ek dogrulama (checksum vb.) yapan, "
        "app/services/validators.py icindeki adlandirilmis fonksiyon; sadece 'regex' "
        "tipi kurallarda kullanilir (orn. 'tc_kimlik_no').",
    )
    placeholder_prefix: Mapped[str] = mapped_column(
        "yer_tutucu_on_eki", String(50), nullable=False,
        comment="Eslesen degerin yerine yazilacak placeholder'in on eki (orn. mask_email -> mask_email_1).",
    )
    entity_type: Mapped[str | None] = mapped_column(
        "entity_tipi", String(100), nullable=True,
        comment="Katman 2 Presidio icin DetectionResult.tip/entity_type degeri (orn. IC_SERVIS_ADI).",
    )
    confidence_score: Mapped[float] = mapped_column(
        "guven_skoru", Float, nullable=False, default=0.85,
        comment="Katman 2 Presidio PatternRecognizer skoru; DetectionResult guven seviyesine cevrilir.",
    )
    is_allow_list: Mapped[bool] = mapped_column(
        "allow_list_mi", Boolean, nullable=False, default=False,
        comment="True ise bu regex hassas veri uretmez; ayni span'a dusen Katman 2 bulgularini bastirir.",
    )
    priority: Mapped[int] = mapped_column(
        "oncelik", Integer, nullable=False, default=100,
        comment="Kurallarin uygulanma sirasi; dusuk sayi once calisir, cakismalari kazanir.",
    )
    is_active: Mapped[bool] = mapped_column(
        "aktif_mi", Boolean, nullable=False, default=True,
        comment="Pasif kurallar yeni taramalarda kullanilmaz ama gecmis eslemeleri bozmaz.",
    )
    description: Mapped[str | None] = mapped_column(
        "aciklama", Text, nullable=True,
        comment="Kuralin ne yaptigina dair serbest metin aciklama; pattern_type='llm' "
        "kurallarda bu alan ZORUNLUDUR ve gercekten LLM'e giden promptun bir parcasi olur "
        "(bkz. app/services/llm_recognizer.py _augment_prompt, app/services/mapping_service.py build_orchestrator).",
    )

    __table_args__ = (
        CheckConstraint(
            "desen_tipi IN ('regex', 'parametric', 'llm', 'presidio')", name="ck_filter_rules_pattern_type"
        ),
        CheckConstraint(
            "(desen_tipi = 'regex' AND regex_deseni IS NOT NULL) OR "
            "(desen_tipi = 'parametric' AND regex_deseni IS NULL AND dogrulayici_adi IS NULL) OR "
            "(desen_tipi = 'llm' AND regex_deseni IS NULL AND dogrulayici_adi IS NULL AND aciklama IS NOT NULL) OR "
            "(desen_tipi = 'presidio' AND regex_deseni IS NOT NULL AND entity_tipi IS NOT NULL)",
            name="ck_filter_rules_regex_required_for_static",
        ),
        CheckConstraint(
            "kaynak_katman IN ('katman1', 'katman2_presidio', 'llm')", name="ck_filter_rules_source_layer"
        ),
        {
            "comment": "Katman 1, Katman 2 Presidio ve LLM kurallarini tek yerde tutan "
            "birlesik kural listesi. Yeni kural = yeni satir, kod degisikligi gerekmez."
        },
    )


# Veri-odakli (data-driven) haric tutma kurali: bu deseninize (glob) uyan
# dosya/klasorler icerik hic taranmadan (ve kopyalanmadan) atlanir. .env,
# .git, *.pem gibi "iceriginde ne oldugu onemli degil, bu tur dosyalar zaten
# disari cikmamali" durumlari icin - regex tabanli filter_rules'a
# guvenmenin yetersiz kaldigi yerde (bkz. .env dosyasinin generic_secret
# kuralini atlatmasi) ikinci bir savunma katmani saglar.
class ExcludePattern(Base):
    __tablename__ = "haric_tutma_desenleri"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    pattern_name: Mapped[str] = mapped_column(
        "desen_adi", String(100), unique=True, nullable=False, comment="Deseninin benzersiz adi."
    )
    glob_pattern: Mapped[str] = mapped_column(
        "glob_deseni", String(200), nullable=False,
        comment="fnmatch uyumlu glob deseni (orn. '*.pem').",
    )
    applies_to: Mapped[str] = mapped_column(
        "uygulanir", String(20), nullable=False,
        comment="'file', 'directory' ya da 'both' - desen dosyaya mi klasore mi uygulanir.",
    )
    is_active: Mapped[bool] = mapped_column(
        "aktif_mi", Boolean, nullable=False, default=True,
        comment="Pasif desenler yeni taramalarda kullanilmaz.",
    )

    __table_args__ = (
        CheckConstraint(
            "uygulanir IN ('file', 'directory', 'both')", name="ck_exclude_patterns_applies_to"
        ),
        {
            "comment": "Icerigi hic taranmadan, sadece dosya/klasor adina gore (glob deseni) "
            "tamamen disarida birakilacak dosya turleri (orn. .env, .git, *.pem)."
        },
    )


# Proje/sicil/branch uclusunu temsil eden kimlik kaydi; her eslemenin ve
# placeholder sayacinin hangi "context"e ait oldugunu belirler.
class MaskingContext(Base):
    """Identity triple (project + sicil + branch) that scopes every
    masking job and its legacy mappings."""

    __tablename__ = "maskeleme_baglamlari"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    project_name: Mapped[str] = mapped_column(
        "proje_adi", String(200), nullable=False, comment="Maskelenen projenin adi."
    )
    sicil_no: Mapped[str] = mapped_column(
        "personel_no", String(50), nullable=False, comment="Islemi baslatan personelin numarasi."
    )
    branch_name: Mapped[str] = mapped_column(
        "branch_adi", String(200), nullable=False, comment="Ilgili git branch adi."
    )

    __table_args__ = (
        UniqueConstraint(
            "proje_adi", "personel_no", "branch_adi", name="uq_masking_context_identity"
        ),
        {
            "comment": "Proje + sicil + branch uclusunu temsil eden kimlik kaydi; her "
            "eslesmenin ve placeholder sayacinin hangi 'baglama' ait oldugunu belirler."
        },
    )


# Orijinal deger <-> placeholder eslemesinin kalici, sorgulanabilir kaydi.
# Sistemin "hafizasi" burasi - reversible (geri donusturulebilir) olmanin temeli.
class ValueMapping(Base):
    """The permanent, queryable original <-> placeholder mapping record."""

    __tablename__ = "deger_eslemeleri"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    run_id: Mapped[int | None] = mapped_column(
        "calisma_id", ForeignKey("maskeleme_calismalari.id", ondelete="CASCADE"), nullable=True,
        comment="Placeholder'in ait oldugu maskeleme isi; NULL eski baglam eslemeleridir.",
    )
    context_id: Mapped[int] = mapped_column(
        "baglam_id", ForeignKey("maskeleme_baglamlari.id"), nullable=False,
        comment="Ait oldugu maskeleme_baglamlari kaydi.",
    )
    rule_id: Mapped[int | None] = mapped_column(
        "kural_id", ForeignKey("filtre_kurallari.id"), nullable=True,
        comment="Eslesmeyi ureten kural.",
    )
    original_value_encrypted: Mapped[str] = mapped_column(
        "orijinal_deger_sifreli", Text, nullable=False,
        comment="Orijinal hassas degerin sifreli hali.",
    )
    original_value_plain: Mapped[str] = mapped_column(
        "orijinal_deger_duz_metin", Text, nullable=False,
        comment="BILEREK DUZ METIN: orijinal hassas degerin sifrelenmemis hali - 'neyin nasil "
        "maskelendigi' bu DB'ye dogrudan erisen biri tarafindan (rapor-eslemeler CLI komutu ya "
        "da dogrudan SQL ile) ANINDA gorulebilsin diye eklendi (proje sahibinin bilerek verdigi "
        "karar). GUVENLIK UYARISI: bu, orijinal_deger_sifreli'nin sagladigi 'DB dosyasina erisen "
        "SECURITY_ENCRYPTION_KEY olmadan sirlari okuyamaz' garantisini TAMAMEN ORTADAN KALDIRIR - "
        "bu tabloya erisen HERKES (DataGrip, calinmis bir DB dosyasi/backup, flash kopyasi) tum "
        "maskelenmis sirlari (API key, parola, TC kimlik no vb.) dogrudan okuyabilir.",
    )
    original_value_hash: Mapped[str] = mapped_column(
        "orijinal_deger_hash", String(64), nullable=False,
        comment="Ayni degerin tekrar maskelenip maskelenmedigini tespit etmek icin kullanilan hash.",
    )
    placeholder_value: Mapped[str] = mapped_column(
        "yer_tutucu_degeri", String(150), nullable=False,
        comment="Metinde orijinal degerin yerine yazilan placeholder (orn. mask_email_1).",
    )

    __table_args__ = (
        UniqueConstraint("calisma_id", "orijinal_deger_hash", name="uq_mapping_job_original"),
        UniqueConstraint("calisma_id", "yer_tutucu_degeri", name="uq_mapping_job_placeholder"),
        Index("uq_mapping_legacy_original", "baglam_id", "orijinal_deger_hash", unique=True,
              sqlite_where=text("calisma_id IS NULL")),
        Index("uq_mapping_legacy_placeholder", "baglam_id", "yer_tutucu_degeri", unique=True,
              sqlite_where=text("calisma_id IS NULL")),
        {
            "comment": "Orijinal deger <-> placeholder eslemesinin kalici kaydi. Sistemin "
            "'hafizasi' - geri donusturulebilir (unmask) olmanin temeli."
        },
    )


# Her export (mask) ya da geri donusum (unmask) calismasinin kaydi -
# ne zaman baslamis/bitmis, hangi durumda (basarili/uyarili/basarisiz).
class MaskingRun(Base):
    __tablename__ = "maskeleme_calismalari"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    mapping_version: Mapped[int] = mapped_column(
        "esleme_surumu", Integer, nullable=False, default=1, server_default="1",
        comment="1: eski baglam eslemeleri; 2: bu isleme ozel esleme ve sayaclar.",
    )
    context_id: Mapped[int] = mapped_column(
        "baglam_id", ForeignKey("maskeleme_baglamlari.id"), nullable=False,
        comment="Ait oldugu maskeleme_baglamlari kaydi.",
    )
    operation_type: Mapped[str] = mapped_column(
        "islem_tipi", String(20), nullable=False, comment="'mask' ya da 'unmask'."
    )
    source_path: Mapped[str] = mapped_column(
        "kaynak_yol", Text, nullable=False, comment="Okunan (hic degistirilmeyen) kaynak klasor yolu."
    )
    target_path: Mapped[str | None] = mapped_column(
        "hedef_yol", Text, nullable=True, comment="Sonucun yazildigi hedef klasor yolu."
    )
    initiated_by: Mapped[str] = mapped_column(
        "baslatan", String(50), nullable=False, comment="Calismayi baslatan personelin numarasi."
    )
    status: Mapped[str] = mapped_column(
        "durum", String(30), nullable=False, default="in_progress",
        comment="in_progress, completed, completed_with_warnings veya failed. "
        "Cikti yayimlama oncesi mapping'ler kalici kaydedilir; yayimlama hatasi "
        "failed olarak tutulur. Bu sinirdan onceki hatalar rollback edilir.",
    )
    started_at: Mapped[object] = mapped_column(
        "baslangic_tarihi", DateTime(timezone=True), server_default=func.now(),
        comment="Calismanin baslama zamani.",
    )
    completed_at: Mapped[object | None] = mapped_column(
        "bitis_tarihi", DateTime(timezone=True), nullable=True, comment="Calismanin bitis zamani."
    )
    files_scanned: Mapped[int | None] = mapped_column(
        "dosya_sayisi", Integer, nullable=True,
        comment="Bu calismada taranan toplam dosya sayisi (rapor tamamlaninca doldurulur).",
    )
    match_count: Mapped[int | None] = mapped_column(
        "bulgu_sayisi", Integer, nullable=True,
        comment="mask icin toplam eslesme, unmask icin toplam bulunan placeholder sayisi.",
    )

    __table_args__ = (
        CheckConstraint(
            "islem_tipi IN ('mask', 'unmask')", name="ck_masking_runs_operation_type"
        ),
        CheckConstraint(
            "durum IN ('in_progress', 'completed', 'completed_with_warnings', 'failed')",
            name="ck_masking_runs_status",
        ),
        {
            "comment": "Her export (maskeleme) ya da unmask (geri donusum) calismasinin kaydi - "
            "ne zaman baslamis/bitmis, hangi durumda."
        },
    )


# Bir run icindeki her dosya/kural bazli olayin (eslesti, degistirildi,
# atlandi, hata oldu) detayli izini tutan audit kaydi.
class AuditLog(Base):
    __tablename__ = "denetim_kaydi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    run_id: Mapped[int] = mapped_column(
        "calisma_id", ForeignKey("maskeleme_calismalari.id"), nullable=False,
        comment="Ait oldugu maskeleme_calismalari kaydi.",
    )
    file_path: Mapped[str] = mapped_column(
        "dosya_yolu", Text, nullable=False, comment="Olayin gerceklestigi dosyanin goreli yolu."
    )
    action: Mapped[str] = mapped_column(
        "eylem", String(20), nullable=False,
        comment="'matched', 'replaced', 'skipped', 'error', 'cakisma' (OverlapResolver'da "
        "kaybeden bulgu), 'sinir_ihlali' (TokenBoundaryValidator'da reddedilen bulgu) ya da "
        "'sozdizimi_hatasi' (post-export dogrulamasini gecemeyen, basarisiz_dosyalar/'a tasinan dosya).",
    )
    detail: Mapped[str | None] = mapped_column(
        "detay", Text, nullable=True, comment="Olayla ilgili serbest metin detay."
    )
    created_at: Mapped[object] = mapped_column(
        "olusturulma_tarihi", DateTime(timezone=True), server_default=func.now(),
        comment="Olayin zamani.",
    )

    __table_args__ = (
        CheckConstraint(
            "eylem IN ('matched', 'replaced', 'skipped', 'error', 'cakisma', 'sinir_ihlali', "
            "'sozdizimi_hatasi', 'round_trip_hatasi')",
            name="ck_audit_log_action",
        ),
        {
            "comment": "Bir calisma icindeki her dosya/kural bazli olayin (eslesti, "
            "degistirildi, atlandi, hata oldu) detayli izi."
        },
    )


# Yalnizca LLM'in dusuk/orta guvenli bulgularinin insan onayi bekledigi kuyruk.
class ReviewQueue(Base):
    """Human review queue for lower-confidence LLM findings.

    entity_type is intentionally free text, not an enum: the LLM may produce
    organization-specific categories such as IC_SERVIS_ADI, URUN_KOD_ADI or
    KURUM_JARGONU that were not known when the system was deployed.

    KASITLI OLARAK SIFRELENMEMIS: found_value/surrounding_context (ValueMapping'in
    aksine) Fernet ile sifrelenmez. Bu tablonun amaci "kurumun kendi DB'sinden,
    neyin supheli/maskelenmis bulundugunu inceleyebilmesi" - bir inceleyici bu
    satirlari psql/DBeaver ile dogrudan okuyabilmeli ki onaylama/reddetme
    karari verebilsin. Sifrelenseydi bu inceleme akisi (review_service.py) her
    kayit icin ayrica decrypt_value() cagirmak zorunda kalirdi - kazanc yok,
    cunku bu veri zaten KURUMUN KENDI veritabaninda duruyor (disari cikan tek
    sey ValueMapping'deki placeholder-esleme kayitlaridir, onlar sifreli kalir).
    """

    __tablename__ = "gozden_gecirme_kuyrugu"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    run_id: Mapped[int | None] = mapped_column(
        "calisma_id", ForeignKey("maskeleme_calismalari.id"), nullable=True,
        comment="Ilgili maskeleme calismasi; run disi analizlerde NULL olabilir.",
    )
    file_path: Mapped[str] = mapped_column(
        "dosya_yolu", Text, nullable=False, comment="Bulguyu iceren dosyanin goreli yolu."
    )
    line_number: Mapped[int | None] = mapped_column(
        "satir_no", Integer, nullable=True,
        comment="Bulgunun dosya icindeki 1-tabanli satir numarasi; hesaplanamadiysa NULL.",
    )
    found_value: Mapped[str | None] = mapped_column(
        "bulunan_deger", Text, nullable=True, comment="Modelin supheli buldugu birebir metin degeri."
    )
    entity_type: Mapped[str] = mapped_column(
        "varlik_tipi", String(100), nullable=False,
        comment="Serbest/dinamik tip adi; enum ile sinirlandirilmaz.",
    )
    confidence_level: Mapped[str] = mapped_column(
        "guven_seviyesi", String(20), nullable=False, comment="'yuksek', 'orta' ya da 'dusuk'."
    )
    reason: Mapped[str | None] = mapped_column(
        "gerekce", Text, nullable=True, comment="Modelin neden suphelendigine dair kisa aciklama."
    )
    surrounding_context: Mapped[str | None] = mapped_column(
        "baglam", Text, nullable=True, comment="Insan incelemesi icin bulgunun yakin cevresi."
    )
    status: Mapped[str] = mapped_column(
        "durum", String(20), nullable=False, default="pending",
        comment="'pending', 'approved', 'rejected' ya da 'ignored'.",
    )
    created_at: Mapped[object] = mapped_column(
        "olusturulma_tarihi", DateTime(timezone=True), server_default=func.now(),
        comment="Kaydin olusturulma zamani.",
    )

    __table_args__ = (
        CheckConstraint(
            "guven_seviyesi IN ('yuksek', 'orta', 'dusuk')", name="ck_review_queue_confidence"
        ),
        CheckConstraint(
            "durum IN ('pending', 'approved', 'rejected', 'ignored')", name="ck_review_queue_status"
        ),
        {
            "comment": "Yalnizca LLM'in dusuk/orta guvenli bulgulari icin insan onayi bekleyen kuyruk."
        },
    )


class LearnedDecision(Base):
    """A context-scoped user decision reused by later detection runs."""

    __tablename__ = "ogrenilen_bulgu_kararlari"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    context_id: Mapped[int] = mapped_column(
        "baglam_id", ForeignKey("maskeleme_baglamlari.id"), nullable=False
    )
    decision_type: Mapped[str] = mapped_column("karar_tipi", String(20), nullable=False)
    value_encrypted: Mapped[str] = mapped_column("deger_sifreli", Text, nullable=False)
    value_hash: Mapped[str] = mapped_column("deger_hash", String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column("varlik_tipi", String(100), nullable=False)
    scope_key: Mapped[str] = mapped_column(
        "kapsam_anahtari", String(260), nullable=False,
        comment="sensitive icin '*' veya suppression icin ust klasor+uzanti kapsami",
    )
    source_review_id: Mapped[int | None] = mapped_column(
        "kaynak_inceleme_id", ForeignKey("gozden_gecirme_kuyrugu.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column("aktif_mi", Boolean, nullable=False, default=True)
    created_at: Mapped[object] = mapped_column(
        "olusturulma_tarihi", DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("karar_tipi IN ('sensitive', 'suppression')", name="ck_learned_decision_type"),
        UniqueConstraint(
            "baglam_id", "karar_tipi", "deger_hash", "varlik_tipi", "kapsam_anahtari",
            name="uq_learned_decision_scope",
        ),
    )


# Maskeleme sonrasi calisan adversarial denetimin ikincil risk kaydi;
# karar verilene kadar ilgili dosya hedef klasore kopyalanmaz.
class AuditWarning(Base):
    """Maskeleme TAMAMLANDIKTAN SONRA calisan, tespit eden LLM'den (Katman 3)
    tamamen bagimsiz bir adversarial/red-team denetiminin ikincil risk
    kayitlari - review_queue'dan KASITLI olarak ayri bir tablo.

    Buradaki bulgu 'kacirilmis bir tespit' degil, 'yapilan maskelemenin
    YETERSIZ kaldigi' cok daha ciddi bir durumu temsil eder: placeholder'lar
    doğru yerlestirilmis olabilir ama etraflarindaki acik metin (yorum,
    komsu alan, placeholder sayaci vb.) hala orijinal veriye dair bir ipucu
    tasiyor olabilir. Bu yuzden ilgili dosya, bu kayit karara baglanana
    kadar (confirmed/dismissed) hedef klasore hic kopyalanmaz - maskelenmis
    icerik karar verilene kadar sadece bu tabloda (masked_content) tutulur.
    """

    __tablename__ = "denetim_uyarilari"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    run_id: Mapped[int] = mapped_column(
        "calisma_id", ForeignKey("maskeleme_calismalari.id"), nullable=False,
        comment="Bu ikincil riskin tespit edildigi maskeleme calismasi.",
    )
    file_path: Mapped[str] = mapped_column(
        "dosya_yolu", Text, nullable=False, comment="Riskli bulunan dosyanin goreli yolu."
    )
    masked_content: Mapped[str] = mapped_column(
        "maskelenmis_icerik", Text, nullable=False,
        comment="Karar verilene kadar hedef klasore YAZILMAYAN, maskelenmis dosya icerigi - "
        "onay/red sonrasi (dismissed durumunda) buradan hedef klasore serbest birakilir.",
    )
    encoding: Mapped[str | None] = mapped_column(
        "kodlama", String(50), nullable=True,
        comment="Serbest birakilirken kullanilacak dosya kodlamasi (orn. utf-8); bilinmiyorsa NULL -> utf-8 varsayilir.",
    )
    reasoning: Mapped[str] = mapped_column(
        "gerekce", Text, nullable=False,
        comment="Denetim LLM'inin ikincil risk gerekcesi; denetim basarisiz olduysa hata aciklamasi.",
    )
    audit_failed: Mapped[bool] = mapped_column(
        "denetim_basarisiz_mi", Boolean, nullable=False, default=False,
        comment="True ise bu kayit bir RISK TESPITI degil, denetim LLM cagrisinin basarisiz/zaman "
        "asimina ugramis olmasidir - dogrulama yapilamadigi icin guvenlik geregi yine de karantinaya alinmistir.",
    )
    status: Mapped[str] = mapped_column(
        "durum", String(20), nullable=False, default="pending",
        comment="'pending', 'confirmed' (risk gercek, dosya karantinada kalir) ya da "
        "'dismissed' (yanlis alarm karari sonrasi final dogrulamayi gecip output'a yazildi).",
    )
    created_at: Mapped[object] = mapped_column(
        "olusturulma_tarihi", DateTime(timezone=True), server_default=func.now(),
        comment="Kaydin olusturulma zamani.",
    )

    __table_args__ = (
        CheckConstraint(
            "durum IN ('pending', 'confirmed', 'dismissed')", name="ck_audit_warnings_status"
        ),
        {
            "comment": "Maskeleme sonrasi, tespit eden LLM'den bagimsiz adversarial denetimden "
            "gecen dosyalarin ikincil risk kayitlari - review_queue'dan ayri ve daha kritik; "
            "karar verilene kadar ilgili dosya hedef klasore kopyalanmaz."
        },
    )


# Dosya uzantisina gore Katman 2 Presidio'nun HANGI (yerlesik/hazir)
# kategorilerini calistiracagini kisitlayan veri-odakli tablo. Bir uzanti
# icin HIC satir yoksa o uzanti KISITLANMAMIŞ sayilir (Presidio'nun tum
# kategorileri calisir) - orn. .md/.txt/.rst kasitli olarak bu tabloda hic
# yer almaz. Bu kisitlama SADECE Presidio'nun yerlesik (PERSON, ORGANIZATION,
# DATE_TIME vb.) kategorilerine uygulanir; filtre_kurallari'ndaki ozel/
# kurum-ici Presidio pattern kurallarimiz (orn. IC_DOMAIN_ADI) bu
# kisitlamadan BAGIMSIZ olarak, dosya turunden bagimsiz her zaman calisir.
class FileCategoryRestriction(Base):
    __tablename__ = "dosya_tipi_kategori_kisitlamasi"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    file_extension: Mapped[str] = mapped_column(
        "dosya_uzantisi", String(20), nullable=False,
        comment="Nokta olmadan, kucuk harf dosya uzantisi (orn. 'py', 'yaml').",
    )
    allowed_category: Mapped[str] = mapped_column(
        "izinli_kategori", String(100), nullable=False,
        comment="Bu uzanti icin izin verilen Presidio yerlesik entity_type'i (orn. 'EMAIL_ADDRESS').",
    )
    is_active: Mapped[bool] = mapped_column(
        "aktif_mi", Boolean, nullable=False, default=True,
        comment="Pasif satirlar yeni taramalarda kullanilmaz.",
    )

    __table_args__ = (
        UniqueConstraint("dosya_uzantisi", "izinli_kategori", name="uq_file_category_restriction"),
        {
            "comment": "Dosya uzantisina gore Katman 2 Presidio'nun hangi yerlesik kategorileri "
            "calistiracagini kisitlayan veri-odakli tablo. Uzanti icin satir yoksa kisitlama yok."
        },
    )


# Her placeholder oneki icin global, atomik olarak artan sayac kaydi.
class JobPlaceholderCounter(Base):
    """Each masking job allocates its own atomic counter per placeholder prefix."""

    __tablename__ = "islem_yer_tutucu_sayaclari"

    run_id: Mapped[int] = mapped_column(
        "calisma_id", ForeignKey("maskeleme_calismalari.id", ondelete="CASCADE"), primary_key=True,
    )
    prefix: Mapped[str] = mapped_column("on_ek", String(50), primary_key=True)
    value: Mapped[int] = mapped_column("sayac", Integer, nullable=False, default=0)


class PlaceholderCounter(Base):
    """Legacy counter, used only for pre-job mappings.

    Placeholder metninin (<ENTITY_TIPI>_<SIRA_NO>) TUM context'ler
    arasinda GLOBAL olarak benzersiz olmasini saglayan atomik sayac -
    onek (yer_tutucu_on_eki) basina TEK satir, atomik UPSERT
    (INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING) ile artirilir
    (bkz. mapping_service._next_counter). Boylece iki farkli context ASLA
    ayni placeholder metnini uretemez - kullanici 'Geri Al' adiminda
    yanlis kimlik girse bile placeholder'lar TESADUFEN baska bir projenin
    verisiyle coz(ul)emez (bkz. app/services/unmask_diagnostics.py)."""

    __tablename__ = "yer_tutucu_sayaclari"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, comment="Birincil anahtar.")
    prefix: Mapped[str] = mapped_column(
        "on_ek", String(50), nullable=False, unique=True,
        comment="Placeholder oneki (orn. 'mask_email') - filtre_kurallari.yer_tutucu_on_eki ile ayni degerler.",
    )
    value: Mapped[int] = mapped_column(
        "sayac", Integer, nullable=False, default=0,
        comment="Bu onek icin en son atanan global sayac degeri.",
    )
