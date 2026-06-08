import logging
from pathlib import Path
from playwright.async_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)
from config import Settings
from errors import SiteUnavailableError

log = logging.getLogger(__name__)


class BasePage:
    def __init__(self, page: Page, settings: Settings) -> None:
        self.page = page
        self.settings = settings

    async def download_file(self, target: Locator) -> Path:
        async with self.page.expect_download(
            timeout=self.settings.download_timeout_ms
        ) as download:
            await target.click(timeout=self.settings.action_timeout_ms)
        file = await download.value
        self.settings.download_path.mkdir(parents=True, exist_ok=True)
        path = self.settings.download_path / file.suggested_filename
        await file.save_as(path)
        log.info(f"Saved {path}")
        return path


class SigaPage(BasePage):
    INIT = "Inicio"
    COPIES = "Ejemplares"
    RECORD_SEARCH = "Búsqueda en fichas"
    ADVANCED_SEARCH = "Búsqueda especializada"
    ERROR_BANNER = "app-message-error-inicio"

    async def raise_if_unavailable(self, wait_ms: float = 2_000.0) -> None:
        banner = self.page.locator(self.ERROR_BANNER).first
        try:
            await banner.wait_for(state="visible", timeout=wait_ms)
        except PlaywrightTimeoutError:
            return
        message = (await banner.inner_text()).strip()
        log.warning("SIGA is temporarily unavailable")
        raise SiteUnavailableError(message or "SIGA is temporarily unavailable")

    async def navigate(self, text: str) -> None:
        link = self.page.locator("a", has_text=text).first
        await link.click(timeout=self.settings.action_timeout_ms)

    async def open_init(self):
        from pages.home import HomePage # resolve circular import errors
        await self.navigate(self.INIT)
        return HomePage(self.page, self.settings)

    async def open_copies(self):
        from pages.copies import CopiesPage
        await self.navigate(self.COPIES)
        return CopiesPage(self.page, self.settings)

    async def open_record_search(self):
        from pages.record_search import RecordSearchPage
        await self.navigate(self.RECORD_SEARCH)
        return RecordSearchPage(self.page, self.settings)

    async def open_advanced_search(self):
        from pages.advanced_search import AdvancedSearchPage
        await self.navigate(self.ADVANCED_SEARCH)
        return AdvancedSearchPage(self.page, self.settings)
