import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from playwright.async_api import Locator

from errors import SiteUnavailableError
from pages.base import SigaPage

log = logging.getLogger(__name__)

# The spinner the page shows while it loads Secciones / runs the search.
LOADING = 'img[src*="loading-impi"]'
# A search over a month of gacetas takes ~1-2 minutes server-side.
SEARCH_TIMEOUT_MS = 180_000.0
# One result card per record inside the #ExportTable results container.
CARDS = "#ExportTable .div-box-shadow"
# Parse every visible result card: the DOM id is the record id, and each
# field is a '<strong>Label: </strong> value' pair inside the card.
RESULTS_JS = r"""
() => Array.from(document.querySelectorAll('#ExportTable .div-box-shadow')).map((card) => {
    const entry = { id: card.id };
    for (const strong of card.querySelectorAll('strong')) {
        const label = strong.textContent.replace(/[:\s]+$/, '');
        let value = '';
        for (let n = strong.nextSibling; n; n = n.nextSibling) value += n.textContent;
        entry[label] = value.replace(/\s+/g, ' ').trim();
    }
    return entry;
})
"""


class Columna(StrEnum):
    """Common 'Columna' (search field) options. The select offers ~38 in all;
    these are the ones with known value types."""

    CLASE = "Clase"  # int, e.g. 42
    DENOMINACION = "Denominación"  # text, e.g. ZESTO
    EXPEDIENTE = "Expediente"  # int, e.g. 3618676
    FECHA_PRESENTACION = "Fecha de presentación"  # single date (not yet supported)


@dataclass(frozen=True)
class Term:
    """One Columna + término pair. `operator` joins it to the previous term
    (None for the first term); only used for the 2nd+ term."""

    columna: str
    value: str
    operator: str | None = None


@dataclass(frozen=True)
class AdvancedQuery:
    """A Búsqueda especializada search. Área, the date range, Gacetas,
    Secciones and at least one Term are all required by the form."""

    area: str
    date_from: date
    date_to: date
    gacetas: tuple[str, ...] = ()
    secciones: tuple[str, ...] = ()
    terms: tuple[Term, ...] = ()


class AdvancedSearchPage(SigaPage):
    AREA_LABEL = "Área"
    COLUMNA_LABEL = "Columna"
    TERM_PLACEHOLDER = "Ingrese el término de búsqueda."

    async def search(self, query: AdvancedQuery) -> int | None:
        """Fill the whole form for `query`, submit, and log/return results.

        Pacing matters: filling fields back-to-back trips the site's bot
        detection ("temporarily unavailable"), so we pause between fields.
        Order: Área -> Fecha first, then Gacetas -> Secciones (which only
        enable after a Gaceta) -> term(s). A loading overlay appears between
        async steps and is waited out.
        """
        await self.choose_select(self.AREA_LABEL, query.area)
        await self._wait_overlay()
        await self.human_pause()
        await self.pick_date_range(query.date_from, query.date_to)
        await self.human_pause()
        if query.gacetas:
            await self.fill_chip_list(self._chip_input("Gacetas"), *query.gacetas)
            # gaceta selection loads its Secciones — can be slow
            await self._wait_overlay(60_000.0)
            await self.human_pause()
        if query.secciones:
            await self.fill_chip_list(self._chip_input("Secciones"), *query.secciones)
            await self.human_pause()
        for index, term in enumerate(query.terms):
            await self._fill_term(index, term)
            await self.human_pause()
        await self.click_search()
        await self._wait_overlay(SEARCH_TIMEOUT_MS)  # the search itself is slow
        return await self.log_results()

    def _chip_input(self, label: str) -> Locator:
        """The mat-chip-list input inside the form field carrying `label`."""
        return (
            self.page.locator("mat-form-field")
            .filter(has=self.page.get_by_text(label, exact=True))
            .locator('input[aria-haspopup="listbox"]')
            .first
        )

    async def _fill_term(self, index: int, term: Term) -> None:
        if isinstance(term.value, date):
            raise NotImplementedError(
                "Columna 'Fecha de presentación' (single-date term) not yet supported"
            )
        timeout = self.settings.action_timeout_ms
        if index > 0:
            await self.page.get_by_role("button", name="Add").first.click(timeout=timeout)
            if term.operator:
                # operator control between terms — best effort, unvalidated
                await self.page.get_by_text(term.operator, exact=True).first.click(
                    timeout=timeout
                )
        # nth Columna select + nth término input belong to this term row
        await self.page.get_by_role("combobox", name=self.COLUMNA_LABEL).nth(index).click(
            timeout=timeout
        )
        await self.page.get_by_role("option", name=term.columna, exact=True).first.click(
            timeout=timeout
        )
        await self.page.get_by_placeholder(self.TERM_PLACEHOLDER).nth(index).fill(
            str(term.value), timeout=timeout
        )
        log.info(f"Term[{index}] {term.columna} = {term.value!r}")

    async def _wait_overlay(self, timeout: float | None = None) -> None:
        """Wait out the loading spinner (it may appear a beat after an action).

        A spinner that never clears is how the rate-limit wall manifests
        mid-form (the pending XHR just never answers, no error banner), so a
        timeout here raises SiteUnavailableError rather than letting the next
        action die on an opaque click-timeout.
        """
        spinner = self.page.locator(LOADING).first
        try:
            await spinner.wait_for(state="visible", timeout=1_500)
        except Exception:  # noqa: BLE001 - spinner may never show for fast steps
            pass
        try:
            await spinner.wait_for(
                state="hidden", timeout=timeout or self.settings.page_timeout_ms
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(f"loading overlay did not clear in time: {exc}")
            await self.raise_if_unavailable()
            raise SiteUnavailableError(
                "loading overlay never cleared — likely rate-limited"
            ) from exc

    async def log_results(self) -> int | None:
        """Wait for the results to render, then log the count and a sample."""
        results = (
            self.page.get_by_text("Resultados encontrados")
            .or_(self.page.locator(CARDS))
            .first
        )
        try:
            await results.wait_for(state="visible", timeout=SEARCH_TIMEOUT_MS)
        except Exception as exc:  # noqa: BLE001 - still log whatever rendered
            log.warning(f"no results element appeared: {exc}")
        count = await self.result_count()
        entries = await self.results_on_page()
        if entries:
            log.info(f"First card: {entries[0]}")
        return count

    async def results_on_page(self) -> list[dict[str, str]]:
        """Parse the result cards shown on the current paginator page."""
        entries: list[dict[str, str]] = await self.page.evaluate(RESULTS_JS)
        log.info(f"Parsed {len(entries)} result card(s) on this page")
        return entries

    async def next_page(self) -> bool:
        """Advance the paginator; False once on the last page.

        Pagination is client-side (the search response holds the full record
        set), so this only re-renders cards — no further server traffic.
        """
        button = self.page.locator(".mat-paginator-navigation-next").first
        if not await button.count() or not await button.is_enabled():
            return False
        first_id = await self.page.locator(CARDS).first.get_attribute("id")
        await button.click(timeout=self.settings.action_timeout_ms)
        if first_id:
            stale = self.page.locator(f'{CARDS}[id="{first_id}"]')
            try:
                await stale.wait_for(
                    state="detached", timeout=self.settings.action_timeout_ms
                )
            except Exception:  # noqa: BLE001 - same id resurfacing is harmless
                pass
        return True

    async def all_results(
        self, max_pages: int | None = None
    ) -> list[dict[str, str]]:
        """Collect result cards across paginator pages (all by default)."""
        entries = await self.results_on_page()
        pages = 1
        while (max_pages is None or pages < max_pages) and await self.next_page():
            entries += await self.results_on_page()
            pages += 1
        log.info(f"Collected {len(entries)} card(s) over {pages} page(s)")
        return entries
