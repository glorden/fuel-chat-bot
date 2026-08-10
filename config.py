import os

from dotenv import load_dotenv

load_dotenv()

VK_TOKEN = os.environ["VK_TOKEN"]
GROUP_ID = int(os.environ["GROUP_ID"])
ADMIN_ID = int(os.environ["ADMIN_ID"]) if os.environ.get("ADMIN_ID") else None
AUTO_REPLY_ON_QUESTION = os.environ.get("AUTO_REPLY_ON_QUESTION", "false").lower() == "true"
FRESH_MINUTES = int(os.environ.get("FRESH_MINUTES", "240"))
STALE_MINUTES = int(os.environ.get("STALE_MINUTES", "480"))
MIN_REPLY_GAP_SECONDS = int(os.environ.get("MIN_REPLY_GAP_SECONDS", "5"))

LLM_ENABLED = os.environ.get("LLM_ENABLED", "false").lower() == "true"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or None
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "10"))
