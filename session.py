"""Browser bootstrapping — everything that is *not* page logic.

Owns the seleniumbase CDP driver, the Playwright connection and the
user-agent cleanup. Exposes a single async context manager that yields a
ready-to-drive `Page`.
"""
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from playwright.async_api import Page, async_playwright
from seleniumbase import cdp_driver  # pyright: ignore[reportMissingTypeStubs]

from config import Settings

log = logging.getLogger(__name__)


async def clean_ua(page: Page) -> None:
    """Strip the 'HeadlessChrome' tell from the user-agent string."""
    real_ua = await page.evaluate("() => navigator.userAgent")
    clean = real_ua.replace("HeadlessChrome", "Chrome")
    cdp = await page.context.new_cdp_session(page)
    await cdp.send(  # pyright: ignore[reportUnknownMemberType]
        "Network.setUserAgentOverride",
        {"userAgent": clean},
    )


@asynccontextmanager
async def browser_session(settings: Settings) -> AsyncGenerator[Page]:
    async with async_playwright() as p:
        log.info("Launching chromium: %s", p.chromium.executable_path)
        driver = await cdp_driver.start_async(  # pyright: ignore
            lang=settings.browser_lang,
            tzone=settings.browser_timezone,
            browser_args=list(settings.launch_args),
        )
        browser = await p.chromium.connect_over_cdp(driver.get_endpoint_url())
        page = browser.contexts[0].pages[0]
        await clean_ua(page)
        yield page
