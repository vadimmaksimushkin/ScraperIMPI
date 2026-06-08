import asyncio
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Settings
from errors import SiteUnavailableError
from session import browser_session
from pages.home import HomePage

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


async def fetch_daily_update(settings: Settings) -> list[Path]:
    async with browser_session(settings) as page:
        home = await HomePage.open(page, settings)
        paths = await home.download_archive("xlsx")
        log.info(f"Downloaded {len(paths)} file(s): {paths}")
        return paths


async def search_records(settings: Settings) -> None:
    async with browser_session(settings) as page:
        home = await HomePage.open(page, settings)
        await home.open_copies()
        await home.open_record_search()
        await home.open_advanced_search()
        await home.open_init()
        await page.pause()


async def main() -> None:
    settings = Settings()
    try:
        # await fetch_daily_update(settings)
        await search_records(settings)
    except SiteUnavailableError as exc:
        log.warning(f"SIGA unavailable, please try again later: {exc}")
        return None


if __name__ == "__main__":
    asyncio.run(main())
