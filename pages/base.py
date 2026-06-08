import logging
from pathlib import Path
from playwright.async_api import Locator, Page
from config import Settings

log = logging.getLogger(__name__)


class BasePage:
    def __init__(self, page: Page, settings: Settings) -> None:
        self.page = page
        self.settings = settings

    async def download_file(self, target: Locator) -> Path:
        async with self.page.expect_download() as download:
            await target.click(timeout=self.settings.action_timeout_ms)
        file = await download.value
        self.settings.download_path.mkdir(parents=True, exist_ok=True)
        path = self.settings.download_path / file.suggested_filename
        await file.save_as(path)
        log.info(f"Saved {path}")
        return path
