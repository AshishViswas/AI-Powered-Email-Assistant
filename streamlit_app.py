"""
Streamlit Community Cloud Main Entrypoint for Gmail Agent AI.
Allows seamless deployment on Streamlit Cloud & local execution via `streamlit run streamlit_app.py`.
"""
import os
import sys

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import and execute main Streamlit UI
import app.ui.streamlit_app
