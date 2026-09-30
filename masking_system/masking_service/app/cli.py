"""Command-line management interface for the masking system.

    python -m app.cli kural-ekle --tip tc_kimlik_no --pattern '...' \\
        --placeholder-format 'mask_tc_kimlik_{sayac}'
    python -m app.cli export --kaynak ./proje --hedef ./disa-aktar \\
        --proje Poseidon --sicil EMP-1001 --branch main
    python -m app.cli unmask --kaynak ./maskeli --hedef ./geri-donusturulmus \\
        --proje Poseidon --sicil EMP-1001 --branch main
    python -m app.cli rapor-son-islem --proje Poseidon
    python -m app.cli llm-is-yuku --kaynak ./proje
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

# typer: CLI komutlarini tanimlayan framework - argumanlari parse eder.
import typer

from app.db.session import session_scope
from app.core.config import settings
from app.core.exceptions import MaskingSystemError
from app.services import reporting
from app.services.exclude_admin import load_active_exclude_specs
from app.services.exporter import DEFAULT_MAX_INLINE_SIZE, ExportValidationError, export_project
from app.services.llm_workload import estimate_project
from app.services.rule_admin import RuleValidationError, add_rule, list_rules, set_rule_active
from app.services.unmasker import ContextNotFoundError, unmask_project

# Tum CLI komutlarinin bagli oldugu ana Typer uygulamasi.
app = typer.Typer(help="Maskeleme sistemi yonetim komutlari", no_args_is_help=True)


# --------------------------------------------------------------------------
# Kural yonetimi - "kural = veri": filter_rules tablosuna satir ekler/
# gunceller, kod degisikligi/deploy gerekmez.
# --------------------------------------------------------------------------


# Yeni bir filtre kurali ekler - CLI parametrelerini toplayip add_rule()'a
# aktarir, hatayi kirmizi mesaja cevirir.
@app.command()
def kural_ekle(
    tip: str = typer.Option(..., "--tip", help="Benzersiz kural adi, orn. tc_kimlik_no"),
    placeholder_format: str = typer.Option(
        ..., "--placeholder-format", help="orn. 'mask_tc_kimlik_{sayac}' - '_{sayac}' ile bitmelidir"
    ),
    pattern: Optional[str] = typer.Option(None, "--pattern", help="Regex (pattern-tipi='regex' icin zorunlu)"),
    pattern_tipi: str = typer.Option("regex", "--pattern-tipi", help="'regex', 'parametric' ya da 'llm'"),
    dogrulayici: Optional[str] = typer.Option(
        None, "--dogrulayici",
        help="Regex eslesmesinden sonra checksum kontrolu yapan adlandirilmis fonksiyon "
        "(orn. 'tc_kimlik_no') - sadece pattern-tipi='regex' icin, opsiyonel",
    ),
    kategori: Optional[str] = typer.Option(None, "--kategori", help="Varsayilan: --tip ile ayni"),
    flags: Optional[str] = typer.Option(None, "--flags", help="Regex flag'leri, orn. 'i'"),
    oncelik: Optional[int] = typer.Option(None, "--oncelik", help="Belirtilmezse otomatik atanir"),
    aciklama: Optional[str] = typer.Option(
        None, "--aciklama",
        help="pattern-tipi='llm' icin ZORUNLUDUR - yerel LLM'e verilecek tarama talimati "
        "(orn. 'Kisi adi - metinde gecen gercek bir insanin ad soyadi').",
    ),
    devre_disi: bool = typer.Option(False, "--devre-disi", help="Pasif olarak ekle (test amacli)"),
) -> None:
    """Yeni bir filtre kurali ekler."""
    with session_scope() as db:
        try:
            rule = add_rule(
                db,
                rule_name=tip,
                pattern_type=pattern_tipi,
                regex_pattern=pattern,
                regex_flags=flags,
                validator_name=dogrulayici,
                placeholder_format=placeholder_format,
                category=kategori,
                priority=oncelik,
                description=aciklama,
                is_active=not devre_disi,
            )
            db.commit()
        except RuleValidationError as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.secho(
            f"Kural eklendi: id={rule.id} rule_name={rule.rule_name} "
            f"placeholder={rule.placeholder_prefix}_<N> oncelik={rule.priority} "
            f"aktif={rule.is_active}",
            fg=typer.colors.GREEN,
        )


# Tum kurallari (istege bagli sadece aktifleri) ekrana dokup listeler.
@app.command()
def kural_listele(
    sadece_aktif: bool = typer.Option(False, "--sadece-aktif", help="Sadece aktif kurallari goster")
) -> None:
    """Tum filtre kurallarini listeler."""
    with session_scope() as db:
        rules = list_rules(db, include_inactive=not sadece_aktif)
        if not rules:
            typer.echo("Kayitli kural yok.")
            return
        for r in rules:
            durum = "AKTIF" if r.is_active else "PASIF"
            typer.echo(
                f"[{durum}] {r.rule_name} (tip={r.pattern_type}, kategori={r.category}, "
                f"oncelik={r.priority}, placeholder={r.placeholder_prefix}_<N>)"
            )


# Pasif edilmis bir kurali tekrar aktif eder.
@app.command()
def kural_aktif(tip: str = typer.Argument(..., help="Aktif edilecek kuralin rule_name'i")) -> None:
    """Var olan bir kurali tekrar aktif eder (silinmemis kurallar icin)."""
    with session_scope() as db:
        try:
            rule = set_rule_active(db, tip, True)
            db.commit()
        except RuleValidationError as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.secho(f"'{rule.rule_name}' aktif edildi.", fg=typer.colors.GREEN)


# Bir kurali devre disi birakir (SILMEZ) - gecmis eslemeler bozulmadan kalir.
@app.command()
def kural_pasif(tip: str = typer.Argument(..., help="Pasif edilecek kuralin rule_name'i")) -> None:
    """Bir kurali pasif eder - SILMEZ, gecmis eslemeler bozulmaz."""
    with session_scope() as db:
        try:
            rule = set_rule_active(db, tip, False)
            db.commit()
        except RuleValidationError as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.secho(
            f"'{rule.rule_name}' pasif edildi. Bu kuralla olusturulmus gecmis eslemeler korunuyor, "
            "sadece yeni taramalarda kullanilmayacak.",
            fg=typer.colors.YELLOW,
        )


# --------------------------------------------------------------------------
# Export / unmask
# --------------------------------------------------------------------------


# Proje klasorunu tarayip maskeler, sonucu hedef klasore yazar - asil isi
# export_project() yapar, burasi sadece parametreleri aktarip raporu basar.
@app.command()
def export(
    kaynak: str = typer.Option(..., "--kaynak", help="Kaynak proje klasoru"),
    hedef: str = typer.Option(..., "--hedef", help="Maskelenmis kopyanin yazilacagi klasor"),
    proje: str = typer.Option(..., "--proje"),
    sicil: str = typer.Option(..., "--sicil"),
    branch: str = typer.Option(..., "--branch"),
) -> None:
    """Proje klasorunu maskeleyip hedef klasore kopyalar."""
    with session_scope() as db:
        try:
            # export_project async (LLM cagrilarini dosyalar arasi es zamanli
            # yapabilmek icin) - CLI senkron oldugundan asyncio.run() ile sarmalanir.
            report = asyncio.run(
                export_project(
                    db,
                    source_path=kaynak,
                    project_name=proje,
                    sicil_no=sicil,
                    branch_name=branch,
                    target_path=hedef,
                    initiated_by=sicil,
                )
            )
            db.commit()
        except (ValueError, OSError, MaskingSystemError) as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.echo(report.summary_text())
        if report.status != "completed":
            raise typer.Exit(code=2)


# Export oncesi LLM is yuku tahmini: LLM'e istek gonderilmez, DB'ye yazilmaz.
@app.command("llm-is-yuku")
def llm_is_yuku(
    kaynak: str = typer.Option(..., "--kaynak", help="Kaynak proje klasoru"),
    ilk: int = typer.Option(20, "--ilk", help="Listelenecek en agir dosya sayisi"),
    istek_suresi: float = typer.Option(
        0.0, "--istek-suresi", help="Olculen ortalama LLM istek suresi (sn); verilirse toplam sure tahmin edilir",
    ),
) -> None:
    """Hangi dosyalarin LLM'e kac parca gidecegini ve taramayi yavaslatacak
    dosya bicimlerini (gomulu ikili veri, minified kod) export'tan once gosterir."""
    with session_scope() as db:
        exclude_specs = load_active_exclude_specs(db)
    workloads = estimate_project(
        Path(kaynak), exclude_specs, settings.vllm,
        blob_min_chars=settings.scan.encoded_blob_min_chars, max_inline_size=DEFAULT_MAX_INLINE_SIZE,
        legacy_encodings=settings.encoding.legacy_text_encoding_list,
    )
    total_requests = sum(workload.requests for workload in workloads)
    hidden = sum(workload.stats.hidden_chars for workload in workloads)
    typer.echo(f"LLM'e gidecek metin dosyasi: {len(workloads)}  tahmini istek (tespit+denetim): {total_requests}  "
               f"gizlenecek ikili veri: {hidden:,} karakter")
    if istek_suresi > 0:
        minutes = total_requests * istek_suresi / max(1, settings.vllm.max_concurrent_requests) / 60
        typer.echo(f"Tahmini LLM suresi: ~{minutes:.0f} dk "
                   f"(VLLM_MAX_CONCURRENT_REQUESTS={settings.vllm.max_concurrent_requests})")
    typer.echo(f"{'istek':>6} {'gonderilen':>11} {'gizlenen':>10}  dosya  [uyari]")
    for workload in workloads[:ilk]:
        hints = f"  [{', '.join(workload.hints)}]" if workload.hints else ""
        typer.echo(f"{workload.requests:>6} {workload.stats.sent_chars:>11,} {workload.stats.hidden_chars:>10,}  "
                   f"{workload.path}{hints}")
    if any("taninmayan kodlanmis veri" in workload.hints for workload in workloads):
        typer.secho(
            "UYARI: Bazi dosyalarda gizlenemeyen kodlanmis-veri benzeri satirlar var; bunlar LLM'e gider. "
            "Gerekiyorsa haric tutma kuraliyla ayirin ya da bicimi bildirin.", fg=typer.colors.YELLOW,
        )


# Maskelenmis klasoru proje/sicil/branch kimligine ait eslemelerle geri
# donusturur - yanlis kimlik verilirse hicbir dosyaya dokunmadan hata verir.
@app.command()
def unmask(
    kaynak: str = typer.Option(..., "--kaynak", help="Maskelenmis proje klasoru"),
    hedef: str = typer.Option(..., "--hedef", help="Geri donusturulmus kopyanin yazilacagi klasor"),
    proje: str = typer.Option(..., "--proje"),
    sicil: str = typer.Option(..., "--sicil"),
    branch: str = typer.Option(..., "--branch"),
    job_id: Optional[int] = typer.Option(None, "--job-id", min=1, help="Kaynak maskeleme islem numarasi; paket kaydindan otomatik okunur"),
) -> None:
    """Maskelenmis proje klasorunu proje/sicil/branch uclusune ait
    eslemelerle geri donusturur."""
    with session_scope() as db:
        try:
            report = unmask_project(
                db,
                source_path=kaynak,
                project_name=proje,
                sicil_no=sicil,
                branch_name=branch,
                target_path=hedef,
                initiated_by=sicil,
                job_id=job_id,
            )
            db.commit()
        except ContextNotFoundError as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        except (ValueError, OSError, MaskingSystemError) as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.echo(report.summary_text())
        if report.status != "completed":
            # Ayri exit code: script'ler "tamamlandi ama elle kontrol
            # gerekli" durumunu "tamamen basarili"dan ayirt edebilsin.
            raise typer.Exit(code=2)


# --------------------------------------------------------------------------
# Raporlama / audit sorgulari
# --------------------------------------------------------------------------


@app.command("recover-output")
def recover_output_command(
    hedef: str = typer.Option(..., "--hedef"),
    application_stopped: bool = typer.Option(False, "--application-stopped"),
) -> None:
    """Uygulama durdurulduktan sonra kesilen hedef yazma islemini kurtarir."""
    from pathlib import Path
    from app.services.output_publication import recover_output
    with session_scope() as db:
        try:
            recover_output(db, Path(hedef), application_stopped=application_stopped)
        except ValueError as exc:
            typer.secho(f"HATA: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
    typer.echo("Kurtarma tamamlandi; onceki hedef veya tamamlanmis cikti korundu.")


# Bir projenin en son ne zaman export/unmask edildigini gosterir.
@app.command()
def rapor_son_islem(
    proje: str = typer.Option(..., "--proje"),
    sicil: Optional[str] = typer.Option(None, "--sicil"),
    branch: Optional[str] = typer.Option(None, "--branch"),
    tip: Optional[str] = typer.Option(None, "--tip", help="mask | unmask"),
) -> None:
    """'Bu proje en son ne zaman export/import edildi?' sorusunu cevaplar."""
    with session_scope() as db:
        run = reporting.get_latest_run(
            db, project_name=proje, sicil_no=sicil, branch_name=branch, operation_type=tip
        )
        if run is None:
            typer.secho("Bu kriterlere uyan bir islem kaydi bulunamadi.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        typer.echo(run.format())


# Filtrelere uyan tum export/unmask islemlerinin gecmisini listeler.
@app.command()
def rapor_gecmis(
    proje: Optional[str] = typer.Option(None, "--proje"),
    sicil: Optional[str] = typer.Option(None, "--sicil"),
    branch: Optional[str] = typer.Option(None, "--branch"),
    tip: Optional[str] = typer.Option(None, "--tip", help="mask | unmask"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Filtrelere uyan tum export/unmask islemlerinin gecmisini listeler."""
    with session_scope() as db:
        runs = reporting.list_runs(
            db,
            project_name=proje,
            sicil_no=sicil,
            branch_name=branch,
            operation_type=tip,
            limit=limit,
        )
        if not runs:
            typer.echo("Kayit bulunamadi.")
            return
        for run in runs:
            typer.echo(run.format())


# Bir run_id'ye ait dosya bazli audit log kayitlarini (hangi dosyada ne
# olmus) ekrana dokup gosterir.
@app.command()
def rapor_detay(run_id: int = typer.Option(..., "--run-id")) -> None:
    """Bir run_id'ye ait dosya bazli audit log kayitlarini gosterir."""
    with session_scope() as db:
        entries = reporting.get_run_audit_entries(db, run_id)
        if not entries:
            typer.echo(f"run_id={run_id} icin audit kaydi bulunamadi.")
            return
        for e in entries:
            typer.echo(f"[{e.created_at}] {e.action:9s} {e.file_path}  {e.detail or ''}")


if __name__ == "__main__":
    app()
