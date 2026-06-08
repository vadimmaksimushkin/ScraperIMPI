from playwright.async_api import Page
from pathlib import Path
from config import Settings
from pages.base import SigaPage


S_XLSX_BUTTON = (
    r"body > div.custom-container > app-root > div > app-inicio > "
    r"mat-drawer-container > mat-drawer-content > div > mat-card > div.grid > "
    r"div.col-12.md\:col-6.lg\:col-9.ng-tns-c703605562-0 > "
    r"section > img:nth-child(1)"
)
S_PDF_BUTTON = (
    r"body > div.custom-container > app-root > div > app-inicio > "
    r"mat-drawer-container > mat-drawer-content > div > mat-card > div.grid > "
    r"div.col-12.md\:col-6.lg\:col-9.ng-tns-c703605562-0 > "
    r"section > img:nth-child(2)"
)
S_PAGE_SIZE_SELECT = "#mat-select-0"
S_PAGE_SIZE_50_OPTION = "#mat-option-2"
S_TABLE = "#ExportTable"


class HomePage(SigaPage):
    @classmethod
    async def open(cls, page: Page, settings: Settings) -> HomePage:
        """Navigate to the site root and return the landing page object."""
        await page.goto(
            settings.target_url,
            timeout=settings.page_timeout_ms,
            wait_until="networkidle",
        )
        return cls(page, settings)

    async def set_page_size_50(self) -> None:
        """Open the rows-per-page dropdown and pick 50."""
        await self.page.locator(S_PAGE_SIZE_SELECT).first.click(
            timeout=self.settings.action_timeout_ms,
        )
        await self.page.locator(S_PAGE_SIZE_50_OPTION).first.click(
            timeout=self.settings.action_timeout_ms,
        )

    async def download_archive(self, *types: str) -> list[Path]:
        """Download the requested archive formats; returns saved paths."""
        saved: list[Path] = []
        for kind in types:
            if kind in ("xls", "xlsx"):
                button = self.page.locator(S_XLSX_BUTTON).first
            elif kind == "pdf":
                button = self.page.locator(S_PDF_BUTTON).first
            else:
                continue
            saved.append(await self.download_file(button))
        return saved
