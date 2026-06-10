import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pages.base import SigaPage

log = logging.getLogger(__name__)


class Area(StrEnum):
    """The four 'Área' options offered by the Ejemplares form."""

    EXTRAORDINARIOS = "Extraordinarios"
    PATENTES = "Patentes"
    MARCAS = "Marcas"
    PROPIEDAD = "Protección a la Propiedad Intelectual"


@dataclass(frozen=True)
class CopiesQuery:
    """A single Ejemplares search.

    `area` and the date range are required; `gaceta` is optional. The date is
    always a range of two dates — pass the same date twice for a single day
    (see `single_day`).
    """

    area: str
    date_from: date
    date_to: date
    gaceta: str | None = None

    @classmethod
    def single_day(
        cls, area: str, day: date, gaceta: str | None = None
    ) -> "CopiesQuery":
        return cls(area=area, date_from=day, date_to=day, gaceta=gaceta)


class CopiesPage(SigaPage):
    AREA_LABEL = "Área"
    GACETA_PLACEHOLDER = "Seleccione una Gaceta."

    async def search(self, query: CopiesQuery) -> list[str]:
        """Fill the form for `query`, submit, and log/return the results.

        Returns the gaceta-category titles when no Gaceta was filtered
        (empty list when the result is a direct table).
        """
        await self.choose_select(self.AREA_LABEL, query.area)
        await self.pick_date_range(query.date_from, query.date_to)
        if query.gaceta:
            await self.fill_autocomplete(self.GACETA_PLACEHOLDER, query.gaceta)
        await self.click_search()
        return await self.log_results()

    async def log_results(self) -> list[str]:
        """Summarise the search result and return any gaceta-category titles.

        Without a Gaceta filter the result is an accordion of categories;
        with one it is a results table. We log whichever is present.
        """
        await self.page.wait_for_load_state("networkidle")
        titles = await self.category_titles()
        if titles:
            log.info(f"Found {len(titles)} gaceta categor(y/ies):")
            for title in titles:
                log.info(f"  - {title}")
        else:
            await self.log_table()
        return titles

    async def category_titles(self) -> list[str]:
        """Titles of the gaceta-category accordion panels (empty if none)."""
        panels = self.page.locator("mat-expansion-panel-header .mat-content")
        return [
            (await panels.nth(i).inner_text()).strip()
            for i in range(await panels.count())
        ]

    async def open_category(self, title: str) -> int:
        """Expand a gaceta category panel and log its now-loaded table."""
        await self.page.get_by_role("button", name=title).first.click(
            timeout=self.settings.action_timeout_ms
        )
        await self.page.locator("table").first.wait_for(
            state="visible", timeout=self.settings.action_timeout_ms
        )
        return await self.log_table()
