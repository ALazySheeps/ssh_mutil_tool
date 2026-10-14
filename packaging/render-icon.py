"""Regenerate shipped PNGs from the SVG using Ubuntu's librsvg/Cairo tools.

Developer dependencies: python3-gi python3-cairo gir1.2-rsvg-2.0.
These dependencies are not needed to run the app.
"""
from pathlib import Path

import cairo
import gi

gi.require_version('Rsvg', '2.0')
from gi.repository import Rsvg

root = Path(__file__).resolve().parent.parent
assets = root / 'ssh_multi_tool' / 'assets'
handle = Rsvg.Handle.new_from_file(str(assets / 'app-icon.svg'))
for size in (16, 32, 48, 64, 128, 256, 512):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    context = cairo.Context(surface)
    viewport = Rsvg.Rectangle()
    viewport.x = viewport.y = 0
    viewport.width = viewport.height = size
    handle.render_document(context, viewport)
    surface.write_to_png(str(assets / f'app-icon-{size}.png'))
