#!/usr/bin/env python3
"""Build the globe page's sky assets into site/globe/sky/ (see .claude/handoffs/2026-09-19-globe-sky.md).

  stars.bin    the Yale Bright Star Catalogue (BSC5, 9,110 stars to V 6.5; public domain, Hoffleit & Warren 1991,
               ASCII from http://tdc-www.harvard.edu/catalogs/bsc5.dat.gz) packed as int16 x5 per star:
               RA (0..65535 = 0..360 deg), Dec (x32767/90), V*100, (B-V)*100 (32767 = unknown), HR number.
  milkyway.jpg ESO's Milky Way panorama (eso0932a, Serge Brunier, CC BY 4.0; plate carree in galactic coordinates,
               centre l=0, l increasing to the LEFT as the sky is seen) with its own stars median-filtered out
               (the page draws stars as points), downsampled to 2048x1024.
  moon.jpg     NASA CGI Moon Kit colour map (SVS 4720, LRO WAC; public domain) at 1024x512: the disc is never
               more than ~30 device pixels wide on this page, so 2048 would be waste.

usage: python3 scripts/sky_assets.py <bsc5.dat> <eso0932a.jpg> <lroc_color_poles_2k.tif>
"""
import struct, sys
from PIL import Image

bsc, eso, moon = sys.argv[1:4]
out = 'site/globe/sky/'

n = 0
with open(bsc, 'rb') as f, open(out + 'stars.bin', 'wb') as o:
    for raw in f:
        line = raw.decode('latin-1')
        try:
            hr = int(line[0:4])
            rah, ram, ras = int(line[75:77]), int(line[77:79]), float(line[79:83])
            sg, dd, dm, ds = line[83], int(line[84:86]), int(line[86:88]), int(line[88:90])
            mag = float(line[102:107])
        except ValueError:
            continue                                          # novae and the like: no J2000 position or no V
        ra = (rah + ram / 60 + ras / 3600) * 15
        dec = (dd + dm / 60 + ds / 3600) * (-1 if sg == '-' else 1)
        try: bv = int(round(float(line[109:114]) * 100))
        except ValueError: bv = 32767
        o.write(struct.pack('<5h', int(ra / 360 * 65536) - 32768, int(round(dec / 90 * 32767)), int(round(mag * 100)), bv, hr))
        n += 1
print('stars', n)

# the panorama is a photograph with its own star field; the page draws the stars itself as points, so the photo's are
# removed (a median over 9 px at 4000 wide swallows a point star and keeps the band) and the rest softened
from PIL import ImageFilter
im = Image.open(eso).convert('RGB').filter(ImageFilter.MedianFilter(9)).resize((2048, 1024), Image.LANCZOS).filter(ImageFilter.GaussianBlur(2.5))
im.save(out + 'milkyway.jpg', quality=82, optimize=True, progressive=True)
# a check of the image's own frame: the Large Magellanic Cloud (l=280.5 b=-32.9) should be bright and its mirror dark
px = im.load()
def at(l, b):
    x = int((0.5 - ((l + 180) % 360 - 180) / 360) * 2048) % 2048; y = int((0.5 - b / 180) * 1024)
    s = 0
    for dx in range(-6, 7):
        for dy in range(-6, 7): s += sum(px[(x + dx) % 2048, min(1023, max(0, y + dy))])
    return s / 169 / 3
print('milkyway centre %.0f, north pole %.0f, LMC %.0f, LMC mirrored %.0f' % (at(0, 0), at(0, 90), at(280.5, -32.9), at(79.5, -32.9)))

m = Image.open(moon).convert('RGB').resize((1024, 512), Image.LANCZOS)
m.save(out + 'moon.jpg', quality=85, optimize=True)
