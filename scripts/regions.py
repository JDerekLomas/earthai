"""Ocean regions per cloud regime, and the date sampler.

Each region is a lon/lat box chosen to sit over open ocean so the background is
uniform and the model learns clouds, not coastlines. Boxes stay a few degrees
off every coast and out of winter sea ice (Bering, Labrador). Boxes are generous; the
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
    surface: str = "ocean"   # "ocean" for the cloud set, "land" for the landscape set


REGIONS: list[Region] = [
    # Subtropical marine stratocumulus decks: closed/open cells, pockets of open cells
    Region("california", "stratocumulus", -138, 18, -122, 32),
    Region("namibia", "stratocumulus", -8, -30, 9, -12),
    Region("peru", "stratocumulus", -100, -32, -84, -6),
    Region("canaries", "stratocumulus", -32, 22, -17, 33),
    # Trade-wind cumulus: streets, sugar/gravel/flower/fish organisation
    Region("tradewind_atlantic", "tradewind_cumulus", -60, 8, -35, 24),
    Region("tradewind_pacific", "tradewind_cumulus", -160, 10, -135, 25),
    # Deep tropical convection, ITCZ, anvils
    Region("itcz_pacific", "convection", -165, -2, -120, 12),
    Region("itcz_indian", "convection", 60, -8, 95, 8),
    Region("westpac_warmpool", "convection", 135, 0, 165, 18),
    # Mid-latitude cyclones and fronts
    Region("north_atlantic", "cyclone", -45, 42, -14, 58),
    Region("north_pacific", "cyclone", -178, 38, -150, 52),
    Region("southern_ocean_indian", "cyclone", 50, -62, 110, -44),
    Region("southern_ocean_pacific", "cyclone", -150, -62, -90, -44),
    # Cold-air outbreaks, cloud streets, polar lows
    Region("norwegian_sea", "cold_air_outbreak", -8, 62, 12, 73),
    Region("labrador_sea", "cold_air_outbreak", -54, 52, -40, 62),
    Region("bering_sea", "cold_air_outbreak", -180, 51, -162, 57),
]

# Landscape set: cloud-free land at the same 600 m/px scale. Regime = landform type.
REGIONS += [
    Region("sahara", "desert", -8, 19, 24, 29, "land"),
    Region("arabia", "desert", 42, 19, 55, 28, "land"),
    Region("australia_interior", "desert", 121, -30, 138, -21, "land"),
    Region("gobi", "desert", 96, 40, 110, 46, "land"),
    Region("himalaya", "mountains", 76, 28, 95, 36, "land"),
    Region("andes", "mountains", -74, -30, -66, -15, "land"),
    Region("rockies", "mountains", -117, 38, -106, 50, "land"),
    Region("tibet_plateau", "mountains", 82, 31, 96, 36, "land"),
    Region("us_midwest", "farmland", -100, 38, -86, 46, "land"),
    Region("ukraine_steppe", "farmland", 30, 46, 40, 52, "land"),
    Region("pampas", "farmland", -64, -38, -58, -32, "land"),
    Region("north_china_plain", "farmland", 113, 33, 120, 39, "land"),
    Region("siberia_taiga", "boreal", 90, 58, 130, 68, "land"),
    Region("canadian_shield", "boreal", -100, 54, -72, 62, "land"),
    Region("amazon", "rainforest_rivers", -70, -8, -55, 0, "land"),
    Region("congo", "rainforest_rivers", 15, -5, 28, 3, "land"),
    Region("ganges_delta", "rainforest_rivers", 86, 21, 92, 26, "land"),
]

REGIMES = sorted({r.regime for r in REGIONS})


def regions_for(surface: str) -> list[Region]:
    return [r for r in REGIONS if surface == "all" or r.surface == surface]


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
        print(f"{r.surface:6} {r.regime:20} {r.name:24} lon {r.lon_min:>6} .. {r.lon_max:<6} lat {r.lat_min:>4} .. {r.lat_max}")
    print(len(sample_dates(date(2015, 1, 1), date(2025, 12, 31), 5)), "dates at every 5 days")
