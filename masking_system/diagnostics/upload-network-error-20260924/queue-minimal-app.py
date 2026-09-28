import sys
sys.path.insert(0, '/home/aipc/Desktop/OWASP_PROJE/masking_system/masking_service')
import streamlit as st
from app.webapp.upload_queue import install_upload_queue
install_upload_queue()
with st.form('upload'):
    files = st.file_uploader('Select folder', accept_multiple_files='directory')
    submitted = st.form_submit_button('Start')
if submitted:
    st.write(f'RECEIVED: {len(files)}')
