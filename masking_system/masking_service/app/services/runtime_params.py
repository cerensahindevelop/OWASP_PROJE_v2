"""Calisma zamani kimlik parametrelerinin (proje/sicil/branch) TEK kaynagi.

Parametrik (`pattern_type="parametric"`) bir kural, kategorisi bu
anahtarlardan biriyse export'ta kullanicinin girdigi degerle eslesir
(rule_engine.build_pattern: `runtime_params.get(rule.category)`). Kategori
burada olmayan bir parametrik kural HICBIR ZAMAN eslesmez - sessiz bir
sizinti. Alembic seed verisi sicil kuralini `personnel_no` kategorisiyle
olusturdugu icin sicil degeri maskelenmiyordu (duzeltme: migrasyon
`f1c3a5e7b9d2`). Bu tur bir uyusmazlik bir daha olusmasin diye:
- export ve yeniden denetim parametre sozlugunu yalnizca
  `build_runtime_params` ile kurar;
- kural yonetimi bilinmeyen kategorili parametrik kurali reddeder;
- testler alembic ile kurulan DB'deki her parametrik kuralin kategorisinin
  burada oldugunu ve her parametrenin aktif bir kurali oldugunu dogrular;
- preflight ayni kontrolu gercek DB'de (salt okunur) yapar.

DB'deki string degerler (`filtre_kurallari.kategori`) bu enum'un degerleridir;
mevcut bir degerin metni DEGISTIRILMEZ.
"""

from __future__ import annotations

from enum import StrEnum


class RuntimeParam(StrEnum):
    PROJECT_NAME = "project_name"
    SICIL_NO = "sicil_no"
    BRANCH_NAME = "branch_name"


def build_runtime_params(project_name: str, sicil_no: str, branch_name: str) -> dict[str, str]:
    return {
        RuntimeParam.PROJECT_NAME: project_name,
        RuntimeParam.SICIL_NO: sicil_no,
        RuntimeParam.BRANCH_NAME: branch_name,
    }
