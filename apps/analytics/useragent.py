from dataclasses import dataclass
from functools import lru_cache

from user_agents import parse


@dataclass(frozen=True)
class ParsedUA:
    browser: str = "Unknown"
    browser_version: str = ""
    os: str = "Unknown"
    os_version: str = ""
    device_type: str = "other"
    is_bot: bool = False


@lru_cache(maxsize=4096)
def parse_user_agent(ua_string: str) -> ParsedUA:
    """Parse (and memoise - UA strings repeat massively) a User-Agent header."""
    if not ua_string:
        return ParsedUA()
    ua = parse(ua_string)
    if ua.is_bot:
        device = "bot"
    elif ua.is_mobile:
        device = "mobile"
    elif ua.is_tablet:
        device = "tablet"
    elif ua.is_pc:
        device = "desktop"
    else:
        device = "other"
    return ParsedUA(
        browser=(ua.browser.family or "Unknown")[:60],
        browser_version=(ua.browser.version_string or "")[:30],
        os=(ua.os.family or "Unknown")[:60],
        os_version=(ua.os.version_string or "")[:30],
        device_type=device,
        is_bot=bool(ua.is_bot),
    )
