import os

from dotenv import load_dotenv

load_dotenv()

VK_TOKEN = os.environ["VK_TOKEN"]
GROUP_ID = int(os.environ["GROUP_ID"])

# Беседы, которые бот обслуживает. Всё, что пришло из любой другой беседы,
# игнорируется целиком — без чтения, без записи, без ответа (решение Р4,
# находка F2: раньше peer_id не проверялся нигде, и сообщество, добавленное
# в чужой чат, отвечало там из общей базы и писало в неё).
#
# Переменная обязательная и без дефолта сознательно: пустое значение
# пришлось бы трактовать как "любая беседа", а это ровно та дыра, которую
# закрываем. Громкое падение на старте лучше, чем тихо открытый бот.
def _parse_allowed_peer_ids(raw: str | None) -> frozenset[int]:
    parts = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not parts:
        raise ValueError(
            "ALLOWED_PEER_IDS is required: comma-separated peer_id of every conversation "
            "the bot serves (see .env.example / DEPLOY.md)"
        )
    try:
        return frozenset(int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(
            f"ALLOWED_PEER_IDS must be a comma-separated list of integers, got {raw!r}"
        ) from exc


ALLOWED_PEER_IDS = _parse_allowed_peer_ids(os.environ.get("ALLOWED_PEER_IDS"))
ADMIN_ID = int(os.environ["ADMIN_ID"]) if os.environ.get("ADMIN_ID") else None
AUTO_REPLY_ON_QUESTION = os.environ.get("AUTO_REPLY_ON_QUESTION", "false").lower() == "true"
FRESH_MINUTES = int(os.environ.get("FRESH_MINUTES", "240"))
STALE_MINUTES = int(os.environ.get("STALE_MINUTES", "480"))
MIN_REPLY_GAP_SECONDS = int(os.environ.get("MIN_REPLY_GAP_SECONDS", "5"))

LLM_ENABLED = os.environ.get("LLM_ENABLED", "false").lower() == "true"
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "10"))

LLM_PROVIDERS = ("groq", "gemini", "mistral")

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mistral").strip().lower()
if LLM_PROVIDER not in LLM_PROVIDERS:
    raise ValueError(f"LLM_PROVIDER must be 'groq', 'gemini' or 'mistral', got {LLM_PROVIDER!r}")

# Второй провайдер, к которому идём, если основной не ответил — прежде чем
# откатываться на rule-based (решение Р1). Пусто — второй попытки нет,
# поведение как раньше. Смысл в том, что rule-based теперь не пишет факты
# (см. pipeline.py), поэтому каждый сбой основного провайдера — это ещё и
# потерянные факты, и вторая попытка их спасает.
LLM_FALLBACK_PROVIDER = (os.environ.get("LLM_FALLBACK_PROVIDER") or "").strip().lower() or None
if LLM_FALLBACK_PROVIDER is not None and LLM_FALLBACK_PROVIDER not in LLM_PROVIDERS:
    raise ValueError(
        f"LLM_FALLBACK_PROVIDER must be one of {LLM_PROVIDERS} or empty, got {LLM_FALLBACK_PROVIDER!r}"
    )

# SOCKS5-прокси для исходящих вызовов LLM-вендоров (Groq/Gemini) — см.
# DEPLOY.md. Пусто/не задано — прямые вызовы, поведение не меняется. Общий
# для обоих провайдеров, читается их клиентами.
AI_PROXY_URL = os.environ.get("AI_PROXY_URL") or None

GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or None
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or None
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY") or None
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "ministral-8b-2512")
