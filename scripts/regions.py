"""Ocean regions per cloud regime, and the date sampler.

Each region is a lon/lat box chosen to sit over open ocean so the background is
uniform and the model learns clouds, not coastlines. Boxes are generous; the
fetcher samples random tiles inside them per date, so coverage is statistical.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Region:
    name: str
    regime: str
    lon_min: float
    lat_min: float
    lon_max: float
    lat_max: float


REGIONS: list[Region] = [
    # Subtropical marine stratocumulus decks: closed/open cells, pockets of open cells
    Region("california", "stratocumulus", -138, 18, -120, 34),
    Region("namibia", "stratocumulus", -8, -30, 10, -10),
    Region("peru", "stratocumulus", -100, -32, -78, -6),
    Region("canaries", "stratocumulus", -30, 22, -14, 34),
    # Trade-wind cumulus: streets, sugar/gravel/flower/fish organisation
    Region("tradewind_atlantic", "tradewind_cumulus", -60, 8, -35, 24),
    Region("tradewind_pacific", "tradewind_cumulus", -160, 10, -135, 25),
    # Deep tropical convection, ITCZ, anvils
    Region("itcz_pacific", "convection", -165, -2, -120, 12),
    Region("itcz_indian", "convection", 60, -8, 95, 8),
    Region("westpac_warmpool", "convection", 135, 0, 165, 18),
    # Mid-latitude cyclones and fronts
    Region("north_atlantic", "cyclone", -45, 42, -12, 60),
    Region("north_pacific", "cyclone", -180, 38, -150, 56),
    Region("southern_ocean_indian", "cyclone", 50, -62, 110, -44),
    Region("southern_ocean_pacific", "cyclone", -150, -62, -90, -44),
    # Cold-air outbreaks, cloud streets, polar lows
    Region("norwegian_sea", "cold_air_outbreak", -10, 62, 15, 74),
    Region("labrador_sea", "cold_air_outbreak", -60, 52, -40, 64),
    Region("bering_sea", "cold_air_outbreak", -180, 52, -160, 62),
]

REGIMES = sorted({r.regime for r in REGIONS})


def sample_dates(start: date, end: date, every_n_days: int, seed: int = 0) -> list[date]:
    """Every Nth day from start to end, with a random per-run phase so repeated
    runs with different seeds fill in different days."""
    rng = random.Random(seed)
    offset = rng.randrange(every_n_days)
    d = start + timedelta(days=offset)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=every_n_days)
    return out


if __name__ == "__main__":
    for r in REGIONS:
        print(f"{r.regime:20} {r.name:24} lon {r.lon_min:>6} .. {r.lon_max:<6} lat {r.lat_min:>4} .. {r.lat_max}")
    print(len(sample_dates(date(2015, 1, 1), date(2025, 12, 31), 5)), "dates at every 5 days")
