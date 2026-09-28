"""Limit browser uploads before any uploader is shown, including after reruns.

Only our static, reviewed script is passed to st.html. Never interpolate user
content here. This uses Streamlit's supported same-document JavaScript API;
no site-packages files, CORS/XSRF settings or offline wheels are modified.
"""
from pathlib import Path

import streamlit as st

_SCRIPT = Path(__file__).with_name('assets') / 'upload_queue.js'


def install_upload_queue() -> None:
    st.html('<script>' + _SCRIPT.read_text(encoding='utf-8') + '</script>',
            unsafe_allow_javascript=True, width='content')
