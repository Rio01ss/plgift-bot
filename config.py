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

def _parse_int_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


def _parse_str_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


# === КАНАЛ (основной + резервные для Force Sub, в т.ч. по заявке на вступление) ===
# Резерв: CHANNEL_BACKUP_IDS=-100..., CHANNEL_BACKUP_LINKS=https://t.me/...
# Бот должен быть админом канала и получать апдейты chat_join_request.
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1003837640479"))
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/+TtgEz7w8DBhmYWYy")

CHANNEL_BACKUP_IDS = _parse_int_list(os.environ.get("CHANNEL_BACKUP_IDS"))
CHANNEL_BACKUP_LINKS = _parse_str_list(os.environ.get("CHANNEL_BACKUP_LINKS"))

_seen: set[int] = set()
FORCE_SUB_CHANNEL_IDS: list[int] = []
for _cid in [CHANNEL_ID, *CHANNEL_BACKUP_IDS]:
    if _cid not in _seen:
        FORCE_SUB_CHANNEL_IDS.append(_cid)
        _seen.add(_cid)

# === TIKTOK ===
TIKTOK_ACCOUNT = os.environ.get("TIKTOK_ACCOUNT", "https://www.tiktok.com/@riospecter")

# === СПОНСОР (SPONSOR_ENABLED=true и одна или несколько ссылок через запятую) ===
SPONSOR_ENABLED = os.environ.get("SPONSOR_ENABLED", "false").lower() == "true"
SPONSOR_LINK = os.environ.get("SPONSOR_LINK", "https://t.me/placeholder")
SPONSOR_LINKS = _parse_str_list(os.environ.get("SPONSOR_LINKS"))
if SPONSOR_ENABLED:
    if not SPONSOR_LINKS and SPONSOR_LINK:
        SPONSOR_LINKS = [SPONSOR_LINK]
else:
    SPONSOR_LINKS = []

# === НАСТРОЙКИ ===
ADMIN_CHECK_INTERVAL = int(os.environ.get("ADMIN_CHECK_INTERVAL", "30"))
