import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
for env_file in (ROOT / ".env", ROOT / "fury_fleet" / ".env"):
    load_dotenv(env_file, override=True)

# API key present -> Gemini API key mode. Otherwise -> Vertex AI with ADC (previous setup).
if os.getenv("GOOGLE_API_KEY", "").strip():
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
else:
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"


from . import agent