"""Streamlit'in, ayri surecte calisan FastAPI backend'ine (bkz. app/api/*,
masking_service/api_app.py) HTTP uzerinden konustugu ince istemci katmani.

Bu modul, webapp/*.py sayfalarinin ONCEDEN app.services.* fonksiyonlarini
dogrudan cagirdigi her noktaya karsilik gelen bir fonksiyon tasir - sayfa
kodunun geri kalani (rapor/sonuc render mantigi) DEGISMEDEN kalabilsin diye,
JSON yanitlar `_to_namespace` ile eski dataclass/ORM nesneleriyle AYNI
attribute-erisimli sekle (orn. `report.files_scanned`) cevrilir.

Hata cevirisi TEK noktadan gecer: backend zaten `{message, detail}` govdesiyle
Turkce, kullaniciya-hazir bir mesaj uretiyor (bkz. app/api/errors.py -
friendly_error() sonucu) - burasi bunu ikinci kez CEVIRMEZ, sadece ApiError
olarak tasir; app/webapp/common.py::show_error() bunu dogrudan gosterir.
"""

from __future__ import annotations

from datetime import datetime
from time import monotonic
from types import SimpleNamespace
from typing import Any

import httpx
import streamlit as st

from app.core.config import settings
from app.core.http_diagnostics import http_error_detail

_DATETIME_KEYS = {"started_at", "completed_at", "created_at"}
# Bunlar nesne DEGIL, veri haritasidir (dict[str, int]) - rapor render
# kodu `.items()` ile donuyor (orn. export_page.py::_render_result), o
# yuzden SimpleNamespace'e cevrilmeden AYNEN korunur.
_RAW_DICT_KEYS = {"matches_by_rule", "rule_breakdown", "unresolved_by_placeholder"}


class ApiError(Exception):
    """Backend'den donen bir hata yaniti (ya da backend'e hic ulasilamamasi) -
    `message`/`detail` zaten Turkce/kullaniciya-hazirdir (bkz. modul docstring'i)."""

    def __init__(self, message: str, detail: str | None, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code


def _to_namespace(value: Any) -> Any:
    """JSON'dan gelen dict/list agacini, eski dataclass/ORM nesneleriyle ayni
    `.alan_adi` erisimine sahip bir yapiya cevirir - sayfa render kodunun
    hicbir yeri (orn. `report.files_scanned`, `outcome.relative_path`)
    degismek zorunda kalmaz."""
    if isinstance(value, dict):
        ns = SimpleNamespace()
        for key, val in value.items():
            if key in _RAW_DICT_KEYS:
                pass  # dict[str, int] oldugu gibi kalir
            elif key in _DATETIME_KEYS and isinstance(val, str):
                val = datetime.fromisoformat(val)
            else:
                val = _to_namespace(val)
            setattr(ns, key, val)
        return ns
    if isinstance(value, list):
        return [_to_namespace(v) for v in value]
    return value


@st.cache_resource
def _get_client() -> httpx.Client:
    return httpx.Client(base_url=settings.web.api_base_url, timeout=settings.web.api_request_timeout_seconds)


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    started = monotonic()
    try:
        resp = _get_client().request(method, path, **kwargs)
    except httpx.RequestError as exc:
        if isinstance(exc, httpx.TimeoutException):
            message = "Backend isteği zaman aşımına uğradı."
        elif isinstance(exc, httpx.ConnectError):
            message = "Backend API'sine bağlanılamadı. Servisin (uvicorn api_app:app) çalıştığından emin olun."
        else:
            message = "Backend ile veri aktarımı tamamlanamadı."
        raise ApiError(
            message,
            http_error_detail(
                exc, layer="arayuz_backend", elapsed_seconds=monotonic() - started,
                timeout_seconds=settings.web.api_request_timeout_seconds,
            ),
            0,
        ) from exc

    if resp.status_code >= 400:
        try:
            body = resp.json()
            message = body.get("message") or f"HTTP {resp.status_code}"
            detail = body.get("detail")
        except ValueError:
            message = resp.text or f"HTTP {resp.status_code}"
            detail = None
        raise ApiError(message, detail, resp.status_code)
    return resp


def _get_json(path: str, **kwargs: Any) -> Any:
    return _to_namespace(_request("GET", path, **kwargs).json())


def _post_json(path: str, **kwargs: Any) -> Any:
    return _to_namespace(_request("POST", path, **kwargs).json())


def _patch_json(path: str, **kwargs: Any) -> Any:
    return _to_namespace(_request("PATCH", path, **kwargs).json())


def _delete_json(path: str, **kwargs: Any) -> Any:
    return _to_namespace(_request("DELETE", path, **kwargs).json())


# --------------------------------------------------------------------------
# Export (mask)
# --------------------------------------------------------------------------


def export_by_path(
    *, source_path: str, target_path: str, project_name: str, sicil_no: str, branch_name: str, initiated_by: str
):
    return _post_json(
        "/export",
        json={
            "source_path": source_path,
            "target_path": target_path,
            "project_name": project_name,
            "sicil_no": sicil_no,
            "branch_name": branch_name,
            "initiated_by": initiated_by,
        },
    )


def export_upload(
    uploaded_files: list,
    *,
    is_directory_upload: bool,
    project_name: str,
    sicil_no: str,
    branch_name: str,
    initiated_by: str,
):
    files = [("files", (uf.name, uf.getvalue(), "application/octet-stream")) for uf in uploaded_files]
    data = {
        "project_name": project_name,
        "sicil_no": sicil_no,
        "branch_name": branch_name,
        "initiated_by": initiated_by,
        "is_directory_upload": "true" if is_directory_upload else "false",
    }
    return _post_json("/export/upload", data=data, files=files)


def start_export_job_by_path(
    *, source_path: str, target_path: str, project_name: str, sicil_no: str, branch_name: str, initiated_by: str
) -> str:
    return _post_json(
        "/export/jobs",
        json={
            "source_path": source_path,
            "target_path": target_path,
            "project_name": project_name,
            "sicil_no": sicil_no,
            "branch_name": branch_name,
            "initiated_by": initiated_by,
        },
    ).job_id


def start_export_job_upload(
    uploaded_files: list,
    *,
    is_directory_upload: bool,
    project_name: str,
    sicil_no: str,
    branch_name: str,
    initiated_by: str,
) -> str:
    files = [("files", (uf.name, uf.getvalue(), "application/octet-stream")) for uf in uploaded_files]
    data = {
        "project_name": project_name,
        "sicil_no": sicil_no,
        "branch_name": branch_name,
        "initiated_by": initiated_by,
        "is_directory_upload": "true" if is_directory_upload else "false",
    }
    return _post_json("/export/upload/jobs", data=data, files=files).job_id


def get_export_job(job_id: str):
    return _get_json(f"/export/jobs/{job_id}")


def download_export_output(output_token: str) -> bytes:
    """Yukleme-modu export ciktisini POST yanitindaki opak token ile indirir.

    Run-id endpoint'i gecmis/path-modu sorgulari icin kalir. Yeni olusan bir
    upload'in hemen indirilmesinde token kullanmak, POST transaction'inin
    response sonrasinda commit edilmesiyle olusan gecici 404 yarisini onler.
    """
    return _request("GET", f"/export/outputs/{output_token}/download").content


def download_run_output(run_id: int) -> bytes:
    """Kalici run kaydindan cikti indirir (geriye donuk/gecmis-islem akisi)."""
    return _request("GET", f"/runs/{run_id}/download").content


# --------------------------------------------------------------------------
# Unmask
# --------------------------------------------------------------------------


def unmask_by_path(
    *, source_path: str, target_path: str, sicil_no: str, initiated_by: str,
    project_name: str | None = None, branch_name: str | None = None, job_id: int | None = None,
):
    return _post_json(
        "/unmask",
        json={
            "source_path": source_path,
            "target_path": target_path,
            "project_name": project_name,
            "sicil_no": sicil_no,
            "branch_name": branch_name,
            "initiated_by": initiated_by,
            "job_id": job_id,
        },
    )


def unmask_upload(
    uploaded_files: list,
    *,
    is_directory_upload: bool,
    sicil_no: str,
    initiated_by: str,
    project_name: str | None = None,
    branch_name: str | None = None,
    job_id: int | None = None,
):
    files = [("files", (uf.name, uf.getvalue(), "application/octet-stream")) for uf in uploaded_files]
    data = {
        "sicil_no": sicil_no,
        "initiated_by": initiated_by,
        "is_directory_upload": "true" if is_directory_upload else "false",
    }
    # Proje/branch yalnizca islem kaydi olmayan eski paketler icin gonderilir.
    if project_name and branch_name:
        data.update(project_name=project_name, branch_name=branch_name)
    if job_id is not None:
        data["job_id"] = str(job_id)
    return _post_json("/unmask/upload", data=data, files=files)


# --------------------------------------------------------------------------
# Onay kuyrugu / denetim uyarilari
# --------------------------------------------------------------------------


def _sicil_scope(sicil_no: str, project_name: str | None, branch_name: str | None) -> dict:
    params = {"sicil_no": sicil_no, "project_name": project_name, "branch_name": branch_name}
    return {key: value for key, value in params.items() if value}


def list_pending_reviews(*, sicil_no: str, project_name: str | None = None, branch_name: str | None = None) -> list:
    return _get_json("/reviews", params=_sicil_scope(sicil_no, project_name, branch_name))


def list_reviews_for_run(run_id: int) -> list:
    return _get_json(f"/reviews/by-run/{run_id}")


def approve_review(review_id: int):
    return _post_json(f"/reviews/{review_id}/approve")


def reject_review(review_id: int):
    return _post_json(f"/reviews/{review_id}/reject")


def list_pending_audit_warnings(*, sicil_no: str, project_name: str | None = None, branch_name: str | None = None) -> list:
    return _get_json("/audit-warnings", params=_sicil_scope(sicil_no, project_name, branch_name))


def revalidate_pending_audit_warnings(*, sicil_no: str, project_name: str | None = None, branch_name: str | None = None):
    return _post_json("/audit-warnings/revalidate-pending", json=_sicil_scope(sicil_no, project_name, branch_name))


# Sicilin daha once calistigi proje/branch ciftleri, en son kullanilan once.
def list_project_branches(sicil_no: str) -> list[tuple[str, str]]:
    rows = _get_json("/identities/project-branches", params={"sicil_no": sicil_no})
    return [(row.project_name, row.branch_name) for row in rows]


def list_audit_warnings_for_run(run_id: int) -> list:
    return _get_json(f"/audit-warnings/by-run/{run_id}")


def confirm_audit_warning(warning_id: int):
    return _post_json(f"/audit-warnings/{warning_id}/confirm")


def dismiss_audit_warning(warning_id: int):
    return _post_json(f"/audit-warnings/{warning_id}/dismiss")


# --------------------------------------------------------------------------
# Gecmis / audit
# --------------------------------------------------------------------------


def list_runs(*, sicil_no: str, project_name: str | None = None, branch_name: str | None = None, limit: int = 200) -> list:
    return _get_json("/runs", params={**_sicil_scope(sicil_no, project_name, branch_name), "limit": limit})


def get_run_audit_entries(run_id: int) -> list:
    return _get_json(f"/runs/{run_id}/audit")


# --------------------------------------------------------------------------
# Kurumsal terim sozlugu
# --------------------------------------------------------------------------


def preview_term_upload(filename: str, content: bytes, category: str):
    return _post_json(
        "/term-upload/preview",
        data={"category": category},
        files={"file": (filename, content, "application/octet-stream")},
    )


def commit_term_upload(filename: str, content: bytes, category: str):
    return _post_json(
        "/term-upload/commit",
        data={"category": category},
        files={"file": (filename, content, "application/octet-stream")},
    )


def list_corporate_terms(*, include_deleted: bool = False) -> list:
    return _get_json("/term-upload/terms", params={"include_deleted": include_deleted})


def create_corporate_term(*, term: str, title: str, confirmed_sensitive: bool):
    return _post_json(
        "/term-upload/terms",
        json={
            "term": term,
            "title": title,
            "confirmed_sensitive": confirmed_sensitive,
        },
    )


def activate_corporate_term(term_id: int, *, confirmed_sensitive: bool):
    return _patch_json(
        f"/term-upload/terms/{term_id}/activate",
        json={"confirmed_sensitive": confirmed_sensitive},
    )


def delete_corporate_term(term_id: int):
    return _delete_json(f"/term-upload/terms/{term_id}")


def mask_review_file(review_id: int):
    return _post_json(f"/reviews/{review_id}/mask-file")


def mask_audit_warning(warning_id: int):
    return _post_json(f"/audit-warnings/{warning_id}/mask")
