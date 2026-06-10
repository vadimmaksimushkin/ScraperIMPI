import logging
from dataclasses import dataclass
from datetime import date

from pages.base import SigaPage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordQuery:
    """A single 'Búsqueda en fichas' search.

    `ficha` (the search term) is required. `area` and the date range are
    optional filters that live behind the 'Opciones avanzadas' toggle; pass a
    date range as two dates (same date twice for a single day).
    """

    ficha: str
    area: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    gacetas: tuple[str, ...] = ()


class RecordSearchPage(SigaPage):
    FICHA_LABEL = "Buscador de fichas"
    ADVANCED_TOGGLE = "Opciones avanzadas"
    CHIP_INPUT = 'input[aria-haspopup="listbox"]'

    async def search(self, query: RecordQuery) -> int | None:
        """Fill the form for `query`, submit, and log/return the result count."""
        await self.page.get_by_role("textbox", name=self.FICHA_LABEL).first.fill(
            query.ficha, timeout=self.settings.action_timeout_ms
        )
        log.info(f"Ficha = {query.ficha!r}")
        if query.area or query.gacetas or (query.date_from and query.date_to):
            await self._open_advanced()
            if query.area:
                await self.choose_select("Área", query.area)
            if query.gacetas:
                await self.fill_chip_list(
                    self.page.locator(self.CHIP_INPUT).first, *query.gacetas
                )
            if query.date_from and query.date_to:
                await self.pick_date_range(query.date_from, query.date_to)
        await self.click_search()
        return await self.log_results()

    async def _open_advanced(self) -> None:
        """Reveal the optional Área / Fecha / Gacetas filters."""
        await self.page.get_by_text(self.ADVANCED_TOGGLE).first.click(
            timeout=self.settings.action_timeout_ms
        )

    async def log_results(self) -> int | None:
        await self.page.get_by_text("Resultados encontrados").first.wait_for(
            state="visible", timeout=self.settings.action_timeout_ms
        )
        return await self.result_count()
