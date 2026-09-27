#!/usr/bin/env python3
"""Render the web view's home-screen icons (the sphere, frozen) into web/.

iOS ignores SVG touch icons and rounds the corners itself, so these are full-bleed
square PNGs. Re-run after changing the look: python3 web/make_icons.py
"""
import math
import os

import cairo

OUT = os.path.dirname(os.path.abspath(__file__))
BG = (0.051, 0.055, 0.075)        # page background #0d0e13
TRACK = (1, 1, 1, 0.13)
AMBER = (0.98, 0.75, 0.25)
PURPLE = (0.66, 0.33, 0.97)
GLASS = (0.08, 0.09, 0.13)


def draw(size, path):
    s = cairo.ImageSurface(cairo.FORMAT_RGB24, size, size)
    cr = cairo.Context(s)
    cr.scale(size / 100, size / 100)  # draw on a 100×100 grid
    cr.set_source_rgb(*BG)
    cr.paint()

    cx = cy = 50
    rw, R = 9, 34                     # ring width / radius; leaves room for iOS's corner mask
    r = R - rw / 2 - 5

    cr.set_line_width(rw)
    cr.set_source_rgba(*TRACK)
    cr.arc(cx, cy, R, 0, 2 * math.pi)
    cr.stroke()
    cr.set_source_rgb(*AMBER)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.arc(cx, cy, R, -math.pi / 2, -math.pi / 2 + 2 * math.pi * 0.68)
    cr.stroke()

    # week-elapsed tick
    a = -math.pi / 2 + 2 * math.pi * 0.52
    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_width(2.2)
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.move_to(cx + (R - rw / 2 - 1.5) * math.cos(a), cy + (R - rw / 2 - 1.5) * math.sin(a))
    cr.line_to(cx + (R + rw / 2 + 1.5) * math.cos(a), cy + (R + rw / 2 + 1.5) * math.sin(a))
    cr.stroke()

    # glass ball + two-layer water
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.set_source_rgb(*GLASS)
    cr.fill_preserve()
    cr.save()
    cr.clip()
    top = cy + r - 2 * r * 0.58
    for layer, (alpha, phase) in enumerate(((0.45, 0.0), (1.0, 2.0))):
        cr.move_to(cx - r, cy + r)
        x = cx - r
        while x <= cx + r + 0.5:
            cr.line_to(x, top + 2.2 * math.sin((x - cx) / r * 2.2 * math.pi + phase) + layer * 1.6)
            x += 0.5
        cr.line_to(cx + r, cy + r)
        cr.close_path()
        cr.set_source_rgba(*PURPLE, alpha)
        cr.fill()
    # soft highlight, upper left
    g = cairo.RadialGradient(cx - r * 0.4, cy - r * 0.45, 0, cx - r * 0.4, cy - r * 0.45, r * 0.7)
    g.add_color_stop_rgba(0, 1, 1, 1, 0.16)
    g.add_color_stop_rgba(1, 1, 1, 1, 0)
    cr.set_source(g)
    cr.paint()
    cr.restore()
    cr.set_line_width(0.8)
    cr.set_source_rgba(1, 1, 1, 0.28)
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.stroke()

    s.write_to_png(os.path.join(OUT, path))


if __name__ == "__main__":
    for size, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"),
                       (512, "icon-512.png"), (32, "favicon.png")):
        draw(size, name)
        print(name)
