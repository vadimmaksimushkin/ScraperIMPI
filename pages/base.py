import logging
import random
import re
from datetime import date
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
        await self.raise_if_unavailable()

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

    # --- Angular Material form helpers (shared by every search page) ---

    async def human_pause(
        self, low_ms: float = 800.0, high_ms: float = 1_600.0
    ) -> None:
        """A short jittered delay between UI actions — back-to-back input
        trips SIGA's bot detection (see SIGA_REFERENCE.md §1)."""
        await self.page.wait_for_timeout(random.uniform(low_ms, high_ms))

    async def choose_select(self, label: str, option: str) -> None:
        """Open a <mat-select> by its label and pick an option by text."""
        timeout = self.settings.action_timeout_ms
        await self.page.get_by_role("combobox", name=label).first.click(timeout=timeout)
        await self.page.get_by_role(
            "option", name=option, exact=True
        ).first.click(timeout=timeout)
        log.info(f"Selected {label!r} = {str(option)!r}")

    async def fill_autocomplete(
        self, placeholder: str, text: str, option: str | None = None
    ) -> None:
        """Type into a mat-autocomplete and click the matching listbox option."""
        timeout = self.settings.action_timeout_ms
        field = self.page.get_by_placeholder(placeholder).first
        await field.click(timeout=timeout)
        await field.fill(text, timeout=timeout)
        await self.page.get_by_role(
            "option", name=option or text
        ).first.click(timeout=timeout)
        log.info(f"Autocomplete {placeholder!r} -> {option or text!r}")

    async def fill_chip_list(self, field: Locator, *values: str) -> None:
        """Add chips to a mat-chip-list autocomplete (multi-select).

        For each value: type it, click the matching option *in the open
        autocomplete panel*, and verify a chip really appeared. Scoping to
        the visible panel matters: a previous chip-list's panel can linger
        open and offer a similarly-named option, which would swallow the
        click without adding anything here.
        """
        timeout = self.settings.action_timeout_ms
        chips = field.locator("xpath=ancestor::mat-form-field[1]").locator("mat-chip")
        panel = self.page.locator(".mat-autocomplete-panel.mat-autocomplete-visible")
        for value in values:
            before = await chips.count()
            for attempt in (1, 2):
                await field.click(timeout=timeout)
                await field.fill(value, timeout=timeout)
                await panel.get_by_role("option", name=value).first.click(
                    timeout=timeout
                )
                try:
                    await chips.nth(before).wait_for(state="visible", timeout=5_000)
                    break
                except PlaywrightTimeoutError:
                    log.warning(f"chip {value!r} not added (attempt {attempt})")
                    await self.human_pause()
            else:
                raise RuntimeError(f"chip {value!r} was never added")
            await field.fill("", timeout=timeout)
            await field.press("Escape", timeout=timeout)  # close the panel
            text = (await chips.nth(before).inner_text()).strip()
            log.info(f"Added chip {text.removesuffix('cancel').strip()!r}")

    async def pick_date_range(self, start: date, end: date) -> None:
        """Drive the readonly date-range calendar (single day -> start == end).

        Every calendar cell carries a ``d/m/yyyy`` aria-label, so we match
        dates exactly instead of relying on locale-specific month names.
        """
        timeout = self.settings.action_timeout_ms
        await self.page.get_by_role("button", name="Open calendar").first.click(
            timeout=timeout
        )
        await self.human_pause(500, 1_100)
        await self._calendar_pick(start)
        await self.human_pause(500, 1_100)
        await self._calendar_pick(end)
        log.info(f"Date range {start.isoformat()} .. {end.isoformat()}")

    async def _calendar_pick(self, day: date) -> None:
        """From the open day grid, drill year -> month -> day and click it.

        Date-cell aria-labels are localized (numeric '19/6/2026' on some pages,
        Spanish '19 de junio de 2026' on others), so we match cells by their
        plain text / position. The structural buttons stay English, and we gate
        on each view's prev-button so we never click a cell from a stale view.
        """
        timeout = self.settings.action_timeout_ms
        # day view -> multi-year view
        await self.page.get_by_role(
            "button", name="Choose month and year"
        ).first.click(timeout=timeout)
        await self.human_pause(500, 1_100)
        await self._scroll_years_into_view(day.year)
        await self._click_cell(str(day.year))
        await self.human_pause(500, 1_100)
        # year view: 12 month cells in Jan..Dec order
        await self.page.get_by_role("button", name="Previous year").first.wait_for(
            state="visible", timeout=timeout
        )
        await self._calendar_cells().nth(day.month - 1).click(timeout=timeout)
        await self.human_pause(500, 1_100)
        # month view: day cells, matched by their number
        await self.page.get_by_role("button", name="Previous month").first.wait_for(
            state="visible", timeout=timeout
        )
        await self._click_cell(str(day.day))

    def _calendar_cells(self) -> Locator:
        return self.page.locator(".mat-calendar-body-cell")

    async def _click_cell(self, text: str) -> None:
        """Click the calendar body cell whose visible text is exactly `text`."""
        cell = self._calendar_cells().filter(
            has_text=re.compile(rf"^\s*{re.escape(text)}\s*$")
        )
        await cell.first.click(timeout=self.settings.action_timeout_ms)

    async def _scroll_years_into_view(self, year: int) -> None:
        """Page the 24-year grid until `year` falls within the shown range."""
        timeout = self.settings.action_timeout_ms
        header = self.page.get_by_role("button", name="Choose date").first
        for _ in range(60):
            shown = re.findall(r"\d{4}", (await header.inner_text()).strip())
            lo, hi = (int(shown[0]), int(shown[-1])) if shown else (year, year)
            if lo <= year <= hi:
                return
            name = "Previous 24 years" if year < lo else "Next 24 years"
            await self.page.get_by_role("button", name=name).first.click(timeout=timeout)
            await self.human_pause(400, 800)
        raise RuntimeError(f"calendar could not reach year {year}")

    async def click_search(self) -> None:
        await self.page.get_by_role("button", name="Buscar").first.click(
            timeout=self.settings.action_timeout_ms
        )
        await self.raise_if_unavailable()

    async def click_clear(self) -> None:
        await self.page.get_by_role("button", name="Limpiar").first.click(
            timeout=self.settings.action_timeout_ms
        )

    async def log_table(self) -> int:
        """Log the row count and paginator range of a results <table>.

        Returns the number of data rows shown (0 when the table renders its
        'no data' placeholder), and logs the paginator total alongside it.
        """
        count = await self.page.locator("table tbody tr").count()
        if await self.page.get_by_text("No hay datos que coincidan").count():
            count = 0
        label = self.page.locator(".mat-paginator-range-label").first
        rng = (await label.inner_text()).strip() if await label.count() else ""
        log.info(f"Table: {count} row(s) shown{f' [{rng}]' if rng else ''}")
        return count

    async def result_count(self) -> int | None:
        """Read the 'Resultados encontrados: N' label, if the page shows one."""
        label = self.page.get_by_text("Resultados encontrados")
        if not await label.count():
            log.info("No 'Resultados encontrados' label found")
            return None
        text = (await label.first.inner_text()).strip()
        match = re.search(r"Resultados encontrados:\D*(\d+)", text)
        count = int(match.group(1)) if match else None
        log.info(f"Resultados encontrados: {count}")
        return count
