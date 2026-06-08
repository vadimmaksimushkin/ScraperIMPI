# FIXME: "Por el momento el sitio no se encuentra disponible, por favor
# inténtelo más tarde." handling
import asyncio
import logging
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Settings
from session import browser_session
from pages.home import HomePage

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


async def main() -> None:
    settings = Settings()
    async with browser_session(settings) as page:
        home_page = await HomePage.open(page, settings)
        # await home_page.set_page_size_50()
        await asyncio.sleep(2)
        await home_page.open_copies()
        await asyncio.sleep(2)
        await home_page.open_record_search()
        await asyncio.sleep(2)
        await home_page.open_advanced_search()
        await asyncio.sleep(2)
        await home_page.open_init()

        # await home_page.download_archive("xlsx", "pdf")

        await page.pause()


if __name__ == "__main__":
    asyncio.run(main())
