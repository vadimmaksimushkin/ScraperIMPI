import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Settings
from errors import SiteUnavailableError
from session import browser_session
from pages.home import HomePage
from pages.copies import Area, CopiesQuery
from pages.record_search import RecordQuery
from pages.advanced_search import AdvancedQuery, Columna, Term

GACETA_SOLICITUDES = (
    "Solicitudes de Marcas, Avisos y Nombres Comerciales "
    "presentadas ante el Instituto"
)

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


async def fetch_daily_update(settings: Settings) -> list[Path]:
    async with browser_session(settings) as page:
        home = await HomePage.open(page, settings)
        paths = await home.download_archive("xlsx")
        log.info(f"Downloaded {len(paths)} file(s): {paths}")
        return paths


async def search_records(settings: Settings) -> None:
    async with browser_session(settings) as page:
        home = await HomePage.open(page, settings)

        advanced = await home.open_advanced_search()
        await advanced.search(
            AdvancedQuery(
                area=Area.MARCAS,
                date_from=date(2026, 5, 1),
                date_to=date(2026, 6, 9),
                gacetas=(GACETA_SOLICITUDES,),
                secciones=("Solicitudes de Marcas",),
                terms=(Term(columna=Columna.CLASE, value="42"),),
            )
        )

        # Record (Búsqueda en fichas): ficha required, advanced filters optional.
        # record = await home.open_record_search()
        # await record.search(
        #     RecordQuery(ficha="3618676", area=Area.MARCAS, gacetas=(GACETA_SOLICITUDES,))
        # )

        # Copies (Ejemplares): no gaceta -> category accordion; expand to table.
        # copies = await home.open_copies()
        # titles = await copies.search(
        #     CopiesQuery(Area.MARCAS, date(2026, 1, 1), date(2026, 6, 9))
        # )
        # if titles:
        #     await copies.open_category(titles[0])


async def main() -> None:
    settings = Settings()
    try:
        # await fetch_daily_update(settings)
        await search_records(settings)
    except SiteUnavailableError as exc:
        log.warning(f"SIGA unavailable, please try again later: {exc}")
        return None


if __name__ == "__main__":
    asyncio.run(main())

# Reference notes for the remaining pages (Record search, Advanced search).
# CopiesPage is implemented in pages/copies.py; see also the shared Angular
# Material helpers (choose_select / fill_autocomplete / pick_date_range /
# click_search) on SigaPage in pages/base.py.
r"""
# CopiesPage choices
# Área is available first, 2 others are disabled
get_by_role("combobox", name="Área").locator("span")
# Área choices
get_by_text("Extraordinarios")
get_by_text("Patentes")
get_by_text("Marcas")
get_by_text("Protección a la Propiedad")
# after Área choice two others are available
get_by_role("combobox", name="Gacetas")
# one of many choices
get_by_role("option", name="Clasificación de Productos y")
<mat-option _ngcontent-ng-c1598465451="" role="option" class="mat-option mat-focus-indicator ng-tns-c1598465451-29 ng-star-inserted mat-active" id="mat-option-82" tabindex="0" aria-disabled="false"><!----><span class="mat-option-text"> Clasificación de Productos y Servicios de Marcas </span><!----><div mat-ripple="" class="mat-ripple mat-option-ripple"></div></mat-option>
# and the most dificult - the calendar
get_by_role("button", name="Open calendar")
get_by_role("button", name="Choose month and year")
get_by_role("button", name="2016")
get_by_role("button", name="2039")
get_by_role("button", name="Choose date")
# clicked 2026 year, options to choose month
get_by_role("button", name="/1/2026")
# clicked june, options to choose concrete date
get_by_role("button", name="25/6/")
# then all over again and choosing second date that must be younger than
# the first one or the first one will be rechosen and still we need
# to choose the second date
# also buttons for previous/next months
get_by_role("button", name="Previous month")
get_by_role("button", name="Next month")
# Same choice of 3 of Área, Fecha.., Gacetas on Record Search under the button
get_by_text("Opciones avanzadas ▼")
# but the most important one is with necessary field
get_by_role("textbox", name="Buscador de fichas")
# Advanced search only has Área and Fecha.. at first, after choosing Área
# Gacetas and secciones show up with ability to choose many of those
get_by_role("combobox", name="Gacetas")
get_by_role("combobox", name="Secciones")
# And those too, the 'columna' as single option out of list
locator("div").filter(has_text=re.compile(r"^Columna \*$")).nth(3)
locator(".mat-select-placeholder")
# with options as <div role="listbox" ..> with
get_by_text("Clasificación CIP")
<mat-option _ngcontent-ng-c4105946849="" role="option" class="mat-option mat-focus-indicator mat-active ng-tns-c84697453-27 ng-star-inserted" id="mat-option-28" tabindex="0" aria-disabled="false" style=""><!----><span class="mat-option-text">Acción</span><!----><div mat-ripple="" class="mat-ripple mat-option-ripple"></div></mat-option>
# and optinal fields as
get_by_placeholder("Ingrese el término de bú")
# and option to add more
get_by_role("button", name="Add")
# and/or/not
get_by_text("OR", exact=True)
# another 'columna'
locator(".mat-select-placeholder")
# and with another text field termino de búsqueda
# All search pages have buttons to clear and search
get_by_role("button", name="Limpiar")
get_by_role("button", name="Buscar")
# Copies: Área and Fecha.. are necessary to search, Gacetas is optional
# Record: Ficha is necessary (use 3618676 to testing), others are optional
# Advanced: Área, Fecha.., Gacetas, Secciones, Columna are necessary and
# termino and another Columna with termino are optional
"""
