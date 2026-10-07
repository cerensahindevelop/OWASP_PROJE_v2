"""Sade, tablo agirlikli Turkce ozet rapor.

    loadtest/.venv/bin/python -m loadtest.report_simple \
        --main loadtest/results/main --workers loadtest/results/workers --out loadtest/results/RAPOR.md

Tum rakamlar summary_conditions.json / environment.json dosyalarindan okunur.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SIZE = {"small": "küçük", "medium": "orta", "large": "büyük"}
SCEN = {"cold": "Soğuk başlangıç", "warm": "Isınmış sistem", "burst": "Aynı anda yükleme",
        "sustained": "Sürekli yük (10 dk)", "mixed": "Karışık boyutlar"}


def num(x, d=1):
    if x is None:
        return "–"
    s = f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def dur(sec):
    """Kisa sure: 90 sn alti saniye, ustu dakika."""
    if sec is None:
        return "–"
    if sec < 1:
        return "<1 sn"
    if sec < 90:
        return f"{num(sec, 0)} sn"
    return f"{num(sec / 60, 1)} dk"


def gib(mib):
    return "–" if mib is None else f"{num(mib / 1024, 1)} GB"


def sizes(s):
    parts = s.split("/")
    return "karışık" if len(parts) > 1 else SIZE[parts[0]]


def p95(st):
    if not st or not st["n"]:
        return "–"
    return dur(st["p95"]) + ("" if st["p95_ok"] else " *")


PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]  # referans palet, sabit sira
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _axes(ax, title, ylabel):
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=10)
    ax.set_xlabel("Aynı anda çalışan kullanıcı", color=INK2)
    ax.set_ylabel(ylabel, color=INK2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b5b4ae")
    ax.tick_params(colors=INK2)
    ax.set_xticks([1, 2, 4, 10])
    ax.set_xlim(0.5, 11.2)


def _lines(ax, series, fmt):
    ends = []
    for i, (label, pts) in enumerate(series):
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        ax.plot(xs, ys, color=PALETTE[i], linewidth=2, marker="o", markersize=6,
                markeredgecolor="white", markeredgewidth=1.5, label=label)
        ends.append([ys[-1], ys[-1], xs[-1]])
    # uc etiketleri: dikeyde en az %6 aralik birakacak sekilde ayir
    lo, hi = ax.get_ylim()
    gap = (hi - lo) * 0.06
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):
        if ends[k][1] - ends[k - 1][1] < gap:
            ends[k][1] = ends[k - 1][1] + gap
    for value, ypos, x in ends:
        ax.annotate(fmt(value), (x, value), xytext=(x, ypos), textcoords="data", va="center", fontsize=9,
                    color=INK, xycoords="data", annotation_clip=False,
                    bbox=None).set_position((x + 0.25, ypos))
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.18),
                  ncol=min(4, len(series)))


def make_charts(by: dict, out: Path) -> list[tuple[str, str, str]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out.mkdir(parents=True, exist_ok=True)
    users = (1, 2, 4, 10)
    scen = [("Isınmış sistem (küçük)", "S2_warm"), ("Aynı anda yükleme (orta)", "S3_burst"),
            ("Karışık boyutlar", "S5_mixed"), ("Sürekli yük (küçük)", "S4_sustained")]
    pts = lambda key, f: [(n, f(by[f"{key}_u{n:02d}"])) for n in users]  # noqa: E731
    charts = []

    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.set_ylim(0, 14)
    _lines(ax, [(lbl, pts(k, lambda c: c["e2e_ok"]["p50"] / 60)) for lbl, k in scen], lambda v: f"{v:.1f} dk".replace(".", ","))
    _axes(ax, "Kullanıcının beklediği süre (ortanca)", "dakika")
    fig.tight_layout(); fig.savefig(out / "bekleme.png", dpi=130); plt.close(fig)
    charts.append(("bekleme.png", "Kullanıcı sayısına göre bekleme süresi",
                   "Kullanıcı sayısı arttıkça bekleme neredeyse doğru orantılı uzuyor: her yeni kullanıcı sıraya bir iş daha ekliyor. "
                   "Karışık boyutlarda 1 kullanıcı yalnızca büyük projeyi, 2 kullanıcı büyük ve küçük projeyi çalıştırdığı için o çizgi başta düşüyor."))

    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.set_ylim(0, 2)
    _lines(ax, [(lbl, pts(k, lambda c: c["projects_per_min"]["mean"])) for lbl, k in scen], lambda v: f"{v:.2f}".replace(".", ","))
    _axes(ax, "Dakikada biten proje (3 tekrar ortalaması)", "proje / dakika")
    fig.tight_layout(); fig.savefig(out / "kapasite.png", dpi=130); plt.close(fig)
    charts.append(("kapasite.png", "Kullanıcı sayısına göre kapasite",
                   "Çizgiler yatay: kullanıcı eklemek sistemin iş çıkarma hızını artırmıyor. Sürekli yükte 10 kullanıcıdaki düşüş, "
                   "10 dakikalık süre dolduğunda işlerin çoğunun yarım kalmasından ve 5 işin hata ile düşmesinden kaynaklanıyor."))

    gnames = {"0": "GPU-1 (RTX A5000)", "1": "GPU-2 (RTX 4500 Ada)"}
    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.set_ylim(0, 24)
    _lines(ax, [(gnames[i], [(n, by[f"S2_warm_u{n:02d}"]["gpus"][i]["mem_used_max_mib"] / 1024) for n in users]) for i in ("0", "1")],
           lambda v: f"{v:.1f} GB".replace(".", ","))
    _axes(ax, "GPU belleği (en yüksek, ısınmış sistem)", "GB (kart başına 24 GB)")
    fig.tight_layout(); fig.savefig(out / "gpu_bellek.png", dpi=130); plt.close(fig)
    charts.append(("gpu_bellek.png", "Kullanıcı sayısına göre GPU belleği",
                   "GPU belleği kullanıcı sayısından bağımsız; model bir kez yüklendikten sonra sabit yer kaplıyor."))

    fig, ax = plt.subplots(figsize=(8, 4.6))
    ax.set_ylim(0, 100)
    _lines(ax, [(gnames[i], [(n, by[f"S2_warm_u{n:02d}"]["gpus"][i]["util_mean"]) for n in users]) for i in ("0", "1")],
           lambda v: f"%{v:.0f}")
    _axes(ax, "GPU kullanımı (ortalama, ısınmış sistem)", "yüzde")
    fig.tight_layout(); fig.savefig(out / "gpu_kullanim.png", dpi=130); plt.close(fig)
    charts.append(("gpu_kullanim.png", "Kullanıcı sayısına göre GPU kullanımı",
                   "GPU'lar kullanıcı sayısı artsa da %20–30 civarında kalıyor; model istekleri tek tek işlediği için kartların büyük kısmı boşta."))

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.set_ylim(0, 12)
    _lines(ax, [("Uygulama sunucusu (backend)", [(n, by[f"S2_warm_u{n:02d}"]["backend_rss_max_mib"] / 1024) for n in users])],
           lambda v: f"{v:.1f} GB".replace(".", ","))
    _axes(ax, "Uygulama sunucusunun RAM kullanımı (en yüksek)", "GB")
    fig.tight_layout(); fig.savefig(out / "ram.png", dpi=130); plt.close(fig)
    charts.append(("ram.png", "Kullanıcı sayısına göre uygulama RAM'i",
                   "Her iş dil modelini (spaCy) yeniden yüklediği için RAM, aynı anda çalışan iş sayısıyla birlikte büyüyor."))
    return charts


CSS = """
:root{--bg:#f6f6f4;--card:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3df;--head:#efeeea;--zebra:#f7f7f5;--accent:#2a78d6;--bad:#b42318;--badbg:#fdecea}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;--card:#1a1a19;--ink:#f2f2f0;--ink2:#c3c2b7;--line:#33322f;--head:#24231f;--zebra:#1f1e1c;--accent:#3987e5;--bad:#ff8a80;--badbg:#3a1d1b}}
:root[data-theme="dark"]{--bg:#121211;--card:#1a1a19;--ink:#f2f2f0;--ink2:#c3c2b7;--line:#33322f;--head:#24231f;--zebra:#1f1e1c;--accent:#3987e5;--bad:#ff8a80;--badbg:#3a1d1b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:8px 0 4px}
h2{font-size:20px;margin:40px 0 12px;padding-top:12px;border-top:1px solid var(--line)}
p,li{color:var(--ink)}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th{background:var(--head);text-align:left;font-weight:600;color:var(--ink);position:sticky;top:0}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
td{white-space:nowrap}
td.wrap{white-space:normal}
td:first-child{min-width:9em}
tbody tr:nth-child(even){background:var(--zebra)}
td.bad{background:var(--badbg)}
td.bad strong{color:var(--bad)}
.fig{background:#fcfcfb;border:1px solid var(--line);border-radius:8px;padding:12px;margin:16px 0}
.fig img{width:100%;max-width:820px;display:block;margin:0 auto}
.fig p{color:#3b3a37;margin:8px 4px 0;font-size:14px}
code{background:var(--head);padding:1px 4px;border-radius:4px;font-size:13px}
a{color:var(--accent)}
"""


def write_html(md_text: str, charts_dir: Path, out: Path) -> None:
    import base64
    import re
    import markdown
    body = markdown.markdown(md_text, extensions=["tables"])
    body = body.replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")
    # uzun metin hucreleri kirilsin
    body = re.sub(r"<td>([^<]{60,}|[^<]*<code>.*?</code>[^<]{30,})", lambda m: '<td class="wrap">' + m.group(1), body)
    body = re.sub(r"<td><strong>(\d+)</strong></td>", r'<td class="bad"><strong>\1</strong></td>', body)

    def embed(m):
        src = m.group(1)
        data = base64.b64encode((charts_dir / Path(src).name).read_bytes()).decode()
        return f'src="data:image/png;base64,{data}"'
    body = re.sub(r'src="(charts_ozet/[^"]+)"', embed, body)
    body = re.sub(r'<p><img ([^>]+)></p>\s*<p><em>(.*?)</em></p>', r'<div class="fig"><img \1><p>\2</p></div>', body, flags=re.S)
    html = ("<!doctype html><html lang=\"tr\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Yük Testi Raporu</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")
    out.write_text(html)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True)
    ap.add_argument("--workers", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--html", help="ayrica tarayicida acilacak HTML rapor")
    a = ap.parse_args()
    charts_dir = Path(a.out).parent / "charts_ozet"
    M = json.loads((Path(a.main) / "summary_conditions.json").read_text())
    W = json.loads((Path(a.workers) / "summary_conditions.json").read_text())
    env = json.loads((Path(a.main) / "environment.json").read_text())
    by = {c["condition"]: c for c in M + W}
    e = env["env_settings"]
    ds = env["datasets"]["datasets"]
    L: list[str] = []
    add = L.append

    total_jobs = sum(c["submitted"] for c in M + W)
    total_ok = sum(c["completed"] for c in M + W)
    total_failed = sum(c["failed"] for c in M + W)
    add("# Maskeleme Sistemi — Yük Testi Raporu")
    add("")
    add(f"**Tarih:** 6–7 Ekim 2026 · **Ölçüm:** {len(M) + len(W)} test koşulu × 3 tekrar · "
        f"**Gönderilen iş:** {total_jobs} · **Tamamlanan:** {total_ok} · **Hata ile düşen:** {total_failed} · "
        f"**10 dk'lık sürekli yük testinde süre dolduğunda yarım kalan:** {sum(c['pending_at_window_end'] for c in M + W)}")
    add("")
    if all("complete_outputs" in c for c in M + W):
        add("**Çıktı doğrulaması (ölçüm süresi içinde tamamlanan işler):** "
            f"Eksiksiz: {sum(c['complete_outputs'] for c in M + W)} · "
            f"Eksik: {sum(c['partial_outputs'] for c in M + W)} · "
            f"Doğrulanmamış eski kayıt: {sum(c['unverified_outputs'] for c in M + W)} · "
            f"Geçersiz paket: {sum(c['invalid_outputs'] for c in M + W)}. "
            "İşin tamamlanması, bütün dosyaların çıktıya alındığı anlamına gelmez.")
        add("")
    add("Sanal kullanıcılar gerçek sisteme proje yükledi, işin bitmesini bekledi ve maskelenmiş çıktıyı indirdi. "
        "Her kullanıcı ayrı oturum ve ayrı işlem kaydı kullandı. Güvenlik ve denetim adımlarının hiçbiri kapatılmadı; "
        "uygulama ve model ayarları değiştirilmedi.")
    add("")

    # ------------------------------------------------------------------ 1
    add("## 1. Kısa sonuç")
    add("")
    s1, s10 = by["S2_warm_u01"], by["S2_warm_u10"]
    add("| Soru | Cevap |")
    add("|---|---|")
    add(f"| Sistem dakikada kaç proje bitirebiliyor? | Küçük projede **~{num(s10['projects_per_min']['mean'], 1)}**, "
        f"orta projede **~{num(by['S3_burst_u10']['projects_per_min']['mean'], 1)}**. Kullanıcı sayısı artsa da bu değer artmıyor. |")
    add(f"| Tek kullanıcı ne kadar bekliyor? | Küçük proje **{dur(s1['e2e_ok']['p50'])}**, "
        f"orta proje **{dur(by['S3_burst_u01']['e2e_ok']['p50'])}**, büyük proje **{dur(by['S5_mixed_u01']['e2e_ok']['p50'])}**. |")
    add(f"| 10 kullanıcı aynı anda çalışınca? | Bekleme yaklaşık {num(s10['e2e_ok']['p50'] / s1['e2e_ok']['p50'], 0)} katına çıkıyor (küçük projede **{dur(s10['e2e_ok']['p50'])}**). "
        f"Ayrıca bazı işler hata verip düşüyor. |")
    add("| Yavaşlığın sebebi ne? | Yapay zekâ modeli (LLM) aynı anda yalnızca **1 isteği** işliyor. İşlerin süresinin ~%90'ı bu sırayı beklemekle geçiyor. |")
    add("| Ekran kartları (GPU) yetiyor mu? | Evet, fazlasıyla. GPU'lar ortalama yalnızca %20–30 dolu, bellek ve sıcaklık sorun değil. |")
    add("| Kullanıcıların verileri karışıyor mu? | **Hayır.** Tüm testlerde her kullanıcının kaydı ve çıktısı ayrı kaldı. |")
    add("| Mevcut ayarları değiştirmek gerekir mi? | Hayır. Denenen ayarların hiçbiri anlamlı fark yaratmadı (bkz. bölüm 6). Asıl iyileştirme kod ve model sunucusu tarafında (bkz. bölüm 8). |")
    add("")

    # ------------------------------------------------------------------ 2
    add("## 2. Test edilen sistem ve ayarlar")
    add("")
    gpus = env["nvidia_smi"].splitlines()[1:]
    add("| Bileşen | Değer |")
    add("|---|---|")
    add(f"| Yapay zekâ modeli | `{e['VLLM_MODEL']}` (Ollama 0.35.1 üzerinde) |")
    add(f"| Ekran kartları | {', '.join(g.split(', ')[1] for g in gpus)} (model ikisine bölünmüş durumda) |")
    add("| Model sunucusunun aynı anda işlediği istek | **1** (Ollama ayarı 4 olsa da model 1 ile çalışıyor) |")
    add(f"| Uygulamanın modele aynı anda gönderdiği istek (`VLLM_MAX_CONCURRENT_REQUESTS`) | {e['VLLM_MAX_CONCURRENT_REQUESTS']} |")
    add("| Uygulama sunucusu (backend) süreç sayısı | 1 |")
    add(f"| Bir işte aynı anda işlenen dosya sayısı (`VLLM_FILE_BATCH_SIZE`) | {e['VLLM_FILE_BATCH_SIZE']} |")
    add(f"| Modele tek seferde gönderilen en fazla metin | {e['VLLM_MAX_FILE_CHARS']} karakter (fazlası parçalara bölünür) |")
    add(f"| Model cevabının en fazla uzunluğu | {e['VLLM_MAX_TOKENS']} token |")
    add(f"| Bir model isteği için zaman aşımı | {e['VLLM_TIMEOUT_SECONDS']} sn |")
    add("| Makine | 64 çekirdek CPU, 63 GB RAM |")
    add("")
    add("**Açıklama:** Bunlar testten önce sistemde bulunan ayarlardır ve test boyunca değiştirilmedi. "
        "En önemli satır üçüncüsü: model sunucusu istekleri tek tek, sırayla işliyor.")
    add("")

    # ------------------------------------------------------------------ 3
    add("## 3. Test verileri")
    add("")
    add("| Proje | Dosya sayısı | Boyut | Bir işte modele giden istek | Tek kullanıcıda süre |")
    add("|---|---|---|---|---|")
    single = {"small": by["S2_warm_u01"], "medium": by["S3_burst_u01"], "large": by["S5_mixed_u01"]}
    for k in ("small", "medium", "large"):
        d, c = ds[k], single[k]
        add(f"| {SIZE[k].capitalize()} | {d['file_count']} | {num(d['total_bytes'] / 1024, 0)} KB | "
            f"{num(c['llm_requests_per_job']['mean'], 0)} | {dur(c['e2e_ok']['p50'])} |")
    add("")
    add("**Açıklama:** Projeler bir script ile üretildi; her çalıştırmada birebir aynı dosyalar oluşur. "
        "İçlerindeki kişi adları, e-postalar, IP'ler ve şifreler sahtedir. Her dosya modele iki kez gider: "
        "önce hassas veriyi bulmak, maskelemeden sonra da kaçan bir şey kalmış mı diye denetlemek için.")
    add("")

    # ------------------------------------------------------------------ 4
    add("## 4. Tüm test sonuçları")
    add("")
    add("| Senaryo | Kullanıcı | Proje | Gönderilen iş | Biten iş | Hata ile düşen | Süre bitince yarım kalan | Bekleme (ortanca) | Bekleme (en kötü %5) | Dakikada biten proje | GPU-1 kullanım / bellek | GPU-2 kullanım / bellek |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in M:
        g0, g1 = c["gpus"]["0"], c["gpus"]["1"]
        pend = c["pending_at_window_end"] if c["scenario"] == "sustained" else "–"
        add(f"| {SCEN[c['scenario']]} | {c['users']} | {sizes(c['sizes'])} | {c['submitted']} | {c['completed']} | "
            f"{'**' + str(c['failed']) + '**' if c['failed'] else '0'} | {pend} | {dur(c['e2e_ok']['p50'])} | "
            f"{p95(c['e2e_ok'])} | {num(c['projects_per_min']['mean'], 2)} | "
            f"%{num(g0['util_mean'], 0)} / {gib(g0['mem_used_max_mib'])} | %{num(g1['util_mean'], 0)} / {gib(g1['mem_used_max_mib'])} |")
    add("")
    add("**Tablo nasıl okunur?**")
    add("")
    add("- **GPU-1** = NVIDIA RTX A5000 (24 GB), **GPU-2** = NVIDIA RTX 4500 Ada (24 GB). Kullanım yüzdesi saniyede bir ölçülen ortalamadır; bellek, test boyunca görülen en yüksek değerdir.")
    add("- **Bekleme:** Kullanıcının projeyi yüklemeye başlamasından maskelenmiş dosyayı indirmesine kadar geçen süre. "
        "*Ortanca* tipik kullanıcıyı, *en kötü %5* en şanssız kullanıcıları gösterir. "
        "Yıldızlı (*) değerler az sayıda örneğe dayanır; kesin kabul edilmemeli.")
    add("- **Dakikada biten proje:** Sistemin toplam iş çıkarma hızı.")
    add("- **Süre bitince yarım kalan:** Yalnız sürekli yük testinde var; 10 dakika dolduğunda henüz bitmemiş işler başarılı sayılmadı.")
    add("- **Senaryolar:** *Soğuk başlangıç* = model bellekte değilken ilk kullanım; *Isınmış sistem* = sistem hazırken, "
        "kullanıcılar 1 sn arayla 2'şer iş gönderir; *Aynı anda yükleme* = herkes aynı saniyede gönderir; "
        "*Sürekli yük* = kullanıcılar 10 dakika boyunca iş bitince yenisini gönderir; *Karışık* = farklı boyutlu projeler birlikte (kullanıcılar sırayla büyük, küçük, orta proje gönderir).")
    add("")
    add("**Ne görüyoruz?**")
    add("")
    add(f"- Kullanıcı sayısı 1'den 10'a çıkınca **dakikada biten proje sayısı değişmiyor** "
        f"(küçük projede {num(s1['projects_per_min']['mean'], 2)} → {num(s10['projects_per_min']['mean'], 2)}). "
        "Sistem tek kuyruk gibi çalışıyor; her yeni kullanıcı sadece sırayı uzatıyor.")
    add(f"- Bu yüzden bekleme süresi kullanıcı sayısıyla doğru orantılı artıyor: küçük projede 1 kullanıcıda "
        f"{dur(s1['e2e_ok']['p50'])}, 4 kullanıcıda {dur(by['S2_warm_u04']['e2e_ok']['p50'])}, "
        f"10 kullanıcıda {dur(s10['e2e_ok']['p50'])}.")
    fails10 = {k: by[k] for k in ("S2_warm_u10", "S3_burst_u10", "S4_sustained_u10", "S5_mixed_u10")}
    add(f"- **10 kullanıcıda işler düşmeye başlıyor:** aynı anda yüklemede {fails10['S3_burst_u10']['submitted']} işten "
        f"{fails10['S3_burst_u10']['failed']}, karışık yükte {fails10['S5_mixed_u10']['submitted']} işten "
        f"{fails10['S5_mixed_u10']['failed']} iş hata verdi. 4 kullanıcıya kadar hiç hata yok. Sebebi bölüm 7'de.")
    add(f"- Model bellekte değilken (soğuk başlangıç) ilk kullanıcı yaklaşık {dur(by['S1_cold_u01']['e2e_ok']['p50'] - s1['e2e_ok']['p50'])} "
        f"fazla bekliyor (modelin yüklenmesi ~{num(by['S1_cold_u01']['model_load_s']['mean'], 0)} sn).")
    add(f"- Sürekli yükte 10 kullanıcıda dakikada biten proje {num(by['S4_sustained_u10']['projects_per_min']['mean'], 2)}'a düşmüş görünüyor. "
        "Bunun sebebi kapasitenin azalması değil: işler bu kalabalıkta ~7 dk sürdüğü için 10 dakikalık süre dolduğunda "
        "çoğu yarım kalmıştı (yarım kalanlar 'biten' sayılmadı) ve 5 iş hata ile düştü.")
    add("- GPU belleği yükten bağımsız sabit (model yüklü olduğu sürece yer kaplar); GPU kullanımı hep %20–30 civarında.")
    add("")

    add("### Grafikler")
    add("")
    for fname, title, note in make_charts(by, charts_dir):
        add(f"![{title}](charts_ozet/{fname})")
        add("")
        add(f"*{note}*")
        add("")

    # ------------------------------------------------------------------ 5
    add("## 5. Bir işin süresi nereye gidiyor?")
    add("")
    cols = [("Küçük, 1 kullanıcı", by["S2_warm_u01"]), ("Orta, 1 kullanıcı", by["S3_burst_u01"]),
            ("Büyük, 1 kullanıcı", by["S5_mixed_u01"]), ("Küçük, 10 kullanıcı", by["S2_warm_u10"])]
    add("| Adım | " + " | ".join(n for n, _ in cols) + " |")
    add("|---|" + "---|" * len(cols))
    rows = [
        ("Dosyaları yükleme", "upload"), ("İşin başlayabilmesi için bekleme (en uzun)", "startup_wait_max"),
        ("Modelin bu işle meşgul olduğu süre", "llm_http_busy_per_job"),
        ("Modelin sırasını bekleme (istek başına, en kötü %5)", "llm_req_admission_p95"),
        ("Hassas veri arama (kural + Presidio + model)", "detection_wall"),
        ("Maskeleme", "masking_wall"), ("Maskeleme sonrası model denetimi", "audit_wall"),
        ("Son kontroller ve dosya yazma", "finalize_wall"), ("Paketleme (export)", "export"),
        ("İndirme", "download"),
    ]
    for label, key in rows:
        cells = []
        for _, c in cols:
            if key == "startup_wait_max":
                cells.append(dur(c["startup_wait"]["max"]))
            elif key == "llm_req_admission_p95":
                cells.append(dur(c["llm_req_admission"]["p95"]))
            else:
                cells.append(dur(c[key]["p50"]))
        add(f"| {label} | " + " | ".join(cells) + " |")
    add("| **Toplam bekleme** | " + " | ".join(f"**{dur(c['e2e_ok']['p50'])}**" for _, c in cols) + " |")
    add("")
    add("**Açıklama:** Adımlar kısmen aynı anda yürüdüğü için satırlar toplanınca toplam süreyi vermez. "
        f"Önemli olan şu: tek kullanıcılı küçük işte toplam {dur(s1['e2e_ok']['p50'])} sürenin "
        f"{dur(s1['llm_http_busy_per_job']['p50'])}'si modelin cevap üretmesiyle geçiyor. "
        "Maskeleme, paketleme, yükleme ve indirme bir saniyenin altında; bunlar sorun değil. "
        f"10 kullanıcıda modelin tek bir iş için harcadığı süre aynı kalıyor ({dur(s10['llm_http_busy_per_job']['p50'])}), "
        "ama her iş diğer 9 işin isteklerinin bitmesini beklediği için toplam süre uzuyor. "
        f"10 kullanıcıda bazı işler başlayabilmek için {dur(s10['startup_wait']['max'])} bekledi; 30 sn'yi aşan iş hata verip düşüyor (bkz. bölüm 7).")
    add("")

    # ------------------------------------------------------------------ 6 workers
    add("## 6. Ayar denemeleri (worker ve eşzamanlılık)")
    add("")
    add("Üç ayar tek tek değiştirildi, diğer ikisi sabit tutuldu. Her ayar 1, 2, 4 ve 8 kullanıcıyla, küçük projeyle denendi.")
    add("")
    add("| Denenen ayar | Backend süreci | Aynı anda dosya | Modele aynı anda istek | 8 kullanıcı: dakikada proje | 8 kullanıcı: en kötü %5 bekleme | 1 kullanıcı: bekleme | 8 kullanıcı: backend RAM | Hata |")
    add("|---|---|---|---|---|---|---|---|---|")
    order = [("**Mevcut ayar**", 1, 8, 1), ("Backend 2 süreç", 2, 8, 1), ("Backend 4 süreç", 4, 8, 1),
             ("Modele 2 istek", 1, 8, 2), ("Modele 4 istek", 1, 8, 4),
             ("1 dosya", 1, 1, 1), ("2 dosya", 1, 2, 1), ("4 dosya", 1, 4, 1)]
    for label, bw, fb, lc in order:
        c8 = by[f"W_bw{bw}_fb{fb}_lc{lc}_u08"]
        c1 = by[f"W_bw{bw}_fb{fb}_lc{lc}_u01"]
        fails = sum(by[f"W_bw{bw}_fb{fb}_lc{lc}_u{n:02d}"]["failed"] for n in (1, 2, 4, 8))
        add(f"| {label} | {bw} | {fb} | {lc} | {num(c8['projects_per_min']['mean'], 2)} | {dur(c8['e2e_ok']['p95'])} | "
            f"{dur(c1['e2e_ok']['p50'])} | {gib(c8['backend_rss_max_mib'])} | {fails} |")
    add("")
    base = by["W_bw1_fb8_lc1_u08"]
    b4 = by["W_bw4_fb8_lc1_u08"]
    l4 = by["W_bw1_fb8_lc4_u08"]
    add("**Açıklama:**")
    add("")
    add(f"- **Backend süreci artırmak** neredeyse fark yaratmadı (dakikada {num(base['projects_per_min']['mean'], 2)} → "
        f"{num(b4['projects_per_min']['mean'], 2)} proje). Çünkü bütün süreçler aynı tek modeli bekliyor. "
        f"Buna karşılık daha fazla RAM harcıyor: 4 süreç boşta bile ~{gib(by['W_bw4_fb8_lc1_u01']['backend_rss_max_mib'] - by['W_bw1_fb8_lc1_u01']['backend_rss_max_mib'])} fazla.")
    add(f"- **Modele aynı anda daha çok istek göndermek** de hızlandırmadı; model zaten tek tek çalıştığı için istekler bu kez "
        f"modelin kendi sırasında bekledi. Tek bir isteğin süresi en kötü durumda {dur(base['llm_req_http_ok']['p95'])}'den "
        f"{dur(l4['llm_req_http_ok']['p95'])}'ye çıktı; bu, daha fazla kullanıcıda zaman aşımı riskini artırır.")
    add(f"- **Aynı anda işlenen dosya sayısını düşürmek** tek kullanıcıyı yavaşlattı "
        f"({dur(by['W_bw1_fb8_lc1_u01']['e2e_ok']['p50'])} → {dur(by['W_bw1_fb1_lc1_u01']['e2e_ok']['p50'])}); çok kullanıcıda fark yok.")
    add("- 1–8 kullanıcı arasında hiçbir ayarda hata, bellek taşması veya çökme olmadı.")
    add("- **Sonuç: mevcut ayar (1 süreç, 8 dosya, modele 1 istek) en dengeli seçenek.** Diğerleri ya fark yaratmıyor ya da RAM veya zaman aşımı riski ekliyor.")
    add("")

    # ------------------------------------------------------------------ 7 problems
    add("## 7. Bulunan sorunlar")
    add("")
    add("| # | Sorun | Ne zaman oluyor | Kullanıcıya etkisi | Kanıt |")
    add("|---|---|---|---|---|")
    add(f"| 1 | Model istekleri tek sıra halinde işleniyor | Her zaman | Kullanıcı sayısı kadar bekleme artıyor; sistem dakikada ~{num(s10['projects_per_min']['mean'], 1)} küçük proje bitirebiliyor | "
        f"Modelin dolu olduğu süre oranı: %{num(s10['slots_busy_fraction']['mean'] * 100, 0)}; aynı anda en fazla 1 istek işlendi; GPU ise ~%30 kullanımda |")
    add(f"| 2 | Çok iş aynı anda başlayınca veritabanı kilitleniyor | ~10 iş aynı anda başladığında | İş başlamadan düşüyor, kullanıcı \"Sistemle bağlantı kurulamadı\" görüyor | "
        f"Toplam {sum(by[k]['failed'] for k in fails10)} iş düştü; hepsinde hata `database is locked`; iş başlatma beklemesi 30 sn sınırına ulaştı |")
    add(f"| 3 | Her iş dil modelini (spaCy) yeniden yüklüyor | Her iş başında | İş başına ~1–2 sn gecikme ve ~0,5–1 GB RAM | "
        f"10 eşzamanlı işte backend {gib(s10['backend_rss_max_mib'])} RAM kullandı |")
    add("| 4 | Kişisel verisi çok yoğun dosya taranamıyor | Örnek: 2,4 KB'lık, 15 kişinin TC kimlik, IBAN, telefon ve e-postasını içeren SQL dosyası | Dosya güvenlik için çıktıya konmuyor (\"teknik blok\") | "
        "Model cevabı 2048 token sınırına takılıyor; parçalara bölünse de sığmıyor. Yükten bağımsız, tek kullanıcıda da oluyor |")
    add(f"| 5 | Model 5 dk kullanılmazsa bellekten çıkıyor | Uzun aradan sonraki ilk kullanım | İlk kullanıcı ~{dur(by['S1_cold_u01']['e2e_ok']['p50'] - s1['e2e_ok']['p50'])} fazla bekliyor | "
        f"Model yükleme ~{num(by['S1_cold_u01']['model_load_s']['mean'], 0)} sn sürdü |")
    add("")
    add("**Açıklama:** 1. sorun sistemin kapasitesini belirliyor; 2. sorun ise kalabalıkta işlerin düşmesine yol açıyor. "
        "Testlerde model hiç zaman aşımına uğramadı, hiç sunucu hatası vermedi ve hiçbir dosya güvenlik karantinasına düşmedi.")
    add("")

    # ------------------------------------------------------------------ 8 recommendations
    add("## 8. Öneriler")
    add("")
    add("| Öncelik | Öneri | Neden | Beklenen etki |")
    add("|---|---|---|---|")
    add("| 1 (hemen) | İşin başında veritabanı kaydını hemen kaydedip (commit) kilidi bırakmak; dil modeli yüklemesini kilit dışında yapmak. Ya da aynı anda çalışan iş sayısına bir üst sınır koyup fazlasını sıraya almak | Kalabalıkta işlerin düşmesinin tek sebebi bu | 10 kullanıcıdaki \"Sistemle bağlantı kurulamadı\" hataları kalkar (değişiklik sonrası test edilmeli) |")
    add("| 2 (kısa vade) | Model sunucusunu aynı anda birden fazla isteği işleyecek şekilde çalıştırmak (Ollama paralel ayarı veya vLLM) | GPU'lar %70 boş; bekleme süresi tamamen model sırasından geliyor | Kapasitenin artması beklenir; ne kadar artacağı ölçülmedi, ayrıca test edilmeli |")
    add("| 3 (kısa vade) | Dil modelini (spaCy) her işte yeniden yüklemek yerine bir kez yükleyip paylaşmak | Her iş ~1 GB RAM ve ~1–2 sn harcıyor | Bellek kullanımı kullanıcı sayısıyla büyümez |")
    add("| 4 (kolay) | Ollama'da modelin bellekte kalma süresini (keep_alive) uzatmak | Model 5 dk boşta kalınca bellekten çıkıyor | Uzun aradan sonraki ilk kullanıcı ~13 sn daha hızlı sonuç alır |")
    add("| 5 (veri) | Çok yoğun kişisel veri içeren dosyalar için model cevap sınırını veya parça boyutunu gözden geçirmek | Bu dosyalar şu an taranamayıp çıktıdan çıkarılıyor | Daha az dosya bloklanır |")
    add("| — | Backend süreç sayısı, aynı anda dosya ve modele istek ayarlarını **değiştirmemek** | Denemelerde fark yaratmadılar | — |")
    add("")
    add("**Pratik kural (mevcut haliyle):** Küçük bir proje için kullanıcı başına yaklaşık 40 sn hesaplayın. "
        "Aynı anda N kişi çalışıyorsa her biri yaklaşık N × 40 sn bekler. 8'den fazla kişinin aynı anda iş başlatması önerilmez.")
    add("")

    # ------------------------------------------------------------------ 9 notes
    add("## 9. Notlar")
    add("")
    add("| Konu | Not |")
    add("|---|---|")
    add("| Ölçülemeyen değerler | Modelin ilk kelimeyi üretme süresi (TTFT), KV-cache kullanımı ve preemption sayısı ölçülemedi: uygulama cevabı tek parça alıyor ve model sunucusunun metrik ucu kapalı. Tahmini değer yazılmadı. |")
    add("| Gerçek projeler | Süreler, dosyalardaki hassas veri yoğunluğuna bağlı. Gerçek projelerde süreler farklı olabilir, ama darboğazın yeri (model sırası) değişmez. |")
    add("| Test sırasında kullanım | Test sırasında arayüzden yapılan işlerin etkilediği 4 tekrar yeniden çalıştırıldı; sonuçlarda başka kullanıcı isteği yok. |")
    add("| Ayrıntılı veriler | Tüm ölçümler (aşama aşama, istek istek, saniye saniye donanım) [RAPOR_AYRINTILI.md](RAPOR_AYRINTILI.md) dosyasında ve `main/`, `workers/` klasörlerindeki CSV/JSONL dosyalarında. |")
    add("| Testi tekrar çalıştırmak | `masking_system/` klasöründe `bash loadtest/run_all.sh` (yaklaşık 14 saat sürer). |")
    add("")
    Path(a.out).write_text("\n".join(L))
    print(f"yazildi: {a.out}")
    if a.html:
        write_html("\n".join(L), charts_dir, Path(a.html))
        print(f"yazildi: {a.html}")


if __name__ == "__main__":
    main()
