from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    target_url: str = "https://siga.impi.gob.mx/"
    browser_lang: str = "en-US"
    browser_timezone: str = "America/Mexico_City"
    page_timeout_ms: float = 25_000.0
    action_timeout_ms: float = 10_000.0
    download_timeout_ms: float = 120_000.0
    download_path: Path = Path("SIGA IMPI GACETAS")
    launch_args: tuple[str, ...] = (
        "--use-gl=angle",
        "--use-angle=gl-egl",
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--screen-info={1366x768}",
        "--window-size=1366,728",
    )
