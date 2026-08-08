import os

from dotenv import load_dotenv

load_dotenv()

VK_TOKEN = os.environ["VK_TOKEN"]
GROUP_ID = int(os.environ["GROUP_ID"])
ADMIN_ID = int(os.environ["ADMIN_ID"]) if os.environ.get("ADMIN_ID") else None
AUTO_REPLY_ON_QUESTION = os.environ.get("AUTO_REPLY_ON_QUESTION", "false").lower() == "true"
