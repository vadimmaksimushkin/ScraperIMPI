import asyncio
import logging, sys
from pathlib import Path
from typing import Any
from playwright.async_api import async_playwright, Page, Locator
from seleniumbase import cdp_driver  # pyright: ignore[reportMissingTypeStubs]

TARGET_URL = "https://siga.impi.gob.mx/"
BROWSER_LANG = "en-US"
BROWSER_TIMEZONE = "America/Mexico_City"
LAUNCH_ARGS = [
    "--use-gl=angle",
    "--use-angle=gl-egl",
    "--ignore-gpu-blocklist",
    "--enable-gpu-rasterization",
    "--screen-info={1366x768}",
    "--window-size=1366,728",
]

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


class PageIMPI:
    target_url: str = "https://siga.impi.gob.mx/"
    page_timeout_ms = 25_000.0

    def __init__(
        self,
        page: Page,
        download_path: str | Path = Path("downloaded_files")
    ) -> None:
        self.page: Page = page
        self.download_path = Path(download_path)
        # XPath: /html/body/div[2]/app-root/div/app-inicio/mat-drawer-container/mat-drawer-content/div/mat-card/div[1]/div[2]/section/img[2]
        # Full XPath: /html/body/div[2]/app-root/div/app-inicio/mat-drawer-container/mat-drawer-content/div/mat-card/div[1]/div[2]/section/img[2]
        # CSS: body > div.custom-container > app-root > div > app-inicio > mat-drawer-container > mat-drawer-content > div > mat-card > div.grid > div.col-12.md\:col-6.lg\:col-9.ng-tns-c703605562-0 > section > img:nth-child(2)
        self.s_download_archive_pdf = r"body > div.custom-container > app-root > div > app-inicio > mat-drawer-container > mat-drawer-content > div > mat-card > div.grid > div.col-12.md\:col-6.lg\:col-9.ng-tns-c703605562-0 > section > img:nth-child(2)"
        # XPath: /html/body/div[2]/app-root/div/app-inicio/mat-drawer-container/mat-drawer-content/div/mat-card/div[1]/div[2]/section/img[1]
        # Full XPath: /html/body/div[2]/app-root/div/app-inicio/mat-drawer-container/mat-drawer-content/div/mat-card/div[1]/div[2]/section/img[1]
        # CSS: body > div.custom-container > app-root > div > app-inicio > mat-drawer-container > mat-drawer-content > div > mat-card > div.grid > div.col-12.md\:col-6.lg\:col-9.ng-tns-c703605562-0 > section > img:nth-child(1)
        self.s_download_archive_xlsx = r"body > div.custom-container > app-root > div > app-inicio > mat-drawer-container > mat-drawer-content > div > mat-card > div.grid > div.col-12.md\:col-6.lg\:col-9.ng-tns-c703605562-0 > section > img:nth-child(1)"
        self.s_elements_per_page = "#mat-select-0"
        self.s_50_elements_option = "#mat-option-2"
        self.s_main_table: str = "#ExportTable"

    async def download_file(self, element: Locator) -> None:
        async with self.page.expect_download() as download_file:
            await element.click()
            file = await download_file.value
            file_name: Path = self.download_path / Path(file.suggested_filename)
            await file.save_as(file_name)
            log.info(f"Saved {file_name}")

    async def download_archive(
        self,
        *types: str,
    ) -> None:
        for type in types:
            if type == "xlsx" or type == "xls":
                e_download_archive_xlsx = self.page.locator(self.s_download_archive_xlsx)
                # e_download_archive_xlsx = self.page.locator("img").nth(4)
                await self.download_file(e_download_archive_xlsx)
            if type == "pdf":
                e_download_archive_pdf = self.page.locator(self.s_download_archive_pdf)
                # e_download_archive_pdf = self.page.locator("img").nth(5)
                await self.download_file(e_download_archive_pdf)

    async def click_50_element_per_page(self) -> None:
        e_elements_per_page = self.page.locator(self.s_elements_per_page)
        await e_elements_per_page.click(timeout=10_000.0)
        e_50_elements_option = self.page.locator(self.s_50_elements_option)
        await e_50_elements_option.click()

    async def table_ops(self) -> None:
        s_main_table: str = "#ExportTable"
        e_main_table = self.page.locator(s_main_table)

        headers = await e_main_table.locator("thead th").all_inner_texts()
        rows = e_main_table.locator("tbody tr")
        records: list[dict[str, Any]] = []
        for i in range(await rows.count()):
            cells = await rows.nth(i).locator("td").all_inner_texts()
            records.append(dict(zip(headers, [c.strip() for c in cells])))

        for record in records:
            log.info(record)


async def clean_ua(page: Page) -> None:
    real_ua = await page.evaluate("() => navigator.userAgent")
    # log.info(f"real_ua: {real_ua}")
    clean_ua = real_ua.replace("HeadlessChrome", "Chrome")
    # log.info(f"clean_ua: {clean_ua}")
    cdp = await page.context.new_cdp_session(page)
    await cdp.send( # pyright: ignore[reportUnknownMemberType]
        "Network.setUserAgentOverride",
        {"userAgent": clean_ua}
    )


async def main() -> None:
    async with async_playwright() as p:
        chromium_path = p.chromium.executable_path
        log.info(f"Launching chromium: {chromium_path}")
        driver = await cdp_driver.start_async( # pyright: ignore[reportUnknownMemberType]
            lang=BROWSER_LANG,
            tzone=BROWSER_TIMEZONE,
            browser_args=LAUNCH_ARGS,
        )

        browser = await p.chromium.connect_over_cdp(driver.get_endpoint_url())
        page = browser.contexts[0].pages[0]
        await clean_ua(page)

        await page.goto(
            PageIMPI.target_url,
            timeout=PageIMPI.page_timeout_ms,
            wait_until="networkidle"
        )
        page_impi = PageIMPI(page, "SIGA IMPI GACETAS")
        await page_impi.download_archive("xlsx")
        # await page_impi.click_50_element_per_page()
        # await page_impi.table_ops()
        # await page.screenshot(path="ScraperIMPI/debug_headless.png", full_page=True)
        # await page.pause()


if __name__ == "__main__":
    asyncio.run(main())
