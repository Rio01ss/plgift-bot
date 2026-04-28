import os

def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Переменная окружения {name} не задана. "
            f"Добавь её во вкладке Secrets и перезапусти бота."
        )
    return value

# === ОСНОВНОЙ БОТ ===
MAIN_BOT_TOKEN = _require("MAIN_BOT_TOKEN")

# === АДМИН БОТ ===
ADMIN_BOT_TOKEN = _require("ADMIN_BOT_TOKEN")

# === ВЛАДЕЛЕЦ ===
OWNER_ID = int(_require("OWNER_ID"))

# === КАНАЛ ===
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003837640479"))
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/+TtgEz7w8DBhmYWYy")

# === TIKTOK ===
TIKTOK_ACCOUNT = os.environ.get("TIKTOK_ACCOUNT", "https://www.tiktok.com/@riospecter")

# === СПОНСОР ===
SPONSOR_ENABLED = os.environ.get("SPONSOR_ENABLED", "false").lower() == "true"
SPONSOR_LINK = os.environ.get("SPONSOR_LINK", "https://t.me/placeholder")

# === НАСТРОЙКИ ===
ADMIN_CHECK_INTERVAL = int(os.environ.get("ADMIN_CHECK_INTERVAL", "30"))
