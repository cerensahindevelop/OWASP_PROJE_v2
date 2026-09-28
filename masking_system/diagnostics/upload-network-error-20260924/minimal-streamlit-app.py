import streamlit as st

with st.form('upload'):
    files = st.file_uploader('Select folder', accept_multiple_files='directory')
    submitted = st.form_submit_button('Start')
if submitted:
    st.write(len(files))
