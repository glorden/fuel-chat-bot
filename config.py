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
