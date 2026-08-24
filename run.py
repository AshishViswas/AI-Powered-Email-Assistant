import os
import subprocess
import sys
import time

def main():
    print("🚀 Starting Gmail Agent System...")
    print("  • FastAPI OAuth Backend listening on: http://localhost:7860")
    print("  • Streamlit Web Dashboard launching on: http://localhost:8501")

    venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python")
    python_bin = venv_python if os.path.exists(venv_python) else sys.executable
    print(f"  • Using Python environment: {python_bin}")

    # Start FastAPI server on port 7860
    fastapi_proc = subprocess.Popen(
        [python_bin, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
    )

    # Allow FastAPI to initialize DB and scheduler
    time.sleep(2)

    # Environment setup to prevent Streamlit onboarding prompts
    env = os.environ.copy()
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    # Start Streamlit UI on port 8501
    streamlit_proc = subprocess.Popen(
        [
            python_bin,
            "-m",
            "streamlit",
            "run",
            "app/ui/streamlit_app.py",
            "--server.port",
            "8501",
            "--server.address",
            "0.0.0.0",
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        env=env,
    )

    try:
        fastapi_proc.wait()
        streamlit_proc.wait()
    except KeyboardInterrupt:
        print("\nStopping services...")
        fastapi_proc.terminate()
        streamlit_proc.terminate()

if __name__ == "__main__":
    main()
