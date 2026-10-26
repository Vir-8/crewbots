"""The app icon: three of the crew's faces together, in the app's own bot shapes and colors, on a dark macOS squircle.

Here: python3 icon.py && swift render.swift. Then, from app/: npx tauri icon src-tauri/icons/source/icon.png
(and delete the android/ and ios/ folders it also makes).
"""

import math

DROP = "M16 2.5c0 0-11 11.5-11 18a11 11 0 0 0 22 0c0-6.5-11-18-11-18z"
HEX = "M16 3.5 27 9.8v12.4L16 28.5 5 22.2V9.8z"

BG = "#17181c"
EYES = '<ellipse cx="12.5" cy="16.5" rx="1.7" ry="2.3" fill="#fff"/><ellipse cx="19.5" cy="16.5" rx="1.7" ry="2.3" fill="#fff"/>'


def shape(kind: str, fill: str, extra: str = "") -> str:
    if kind == "flower":
        centres = [(16 + 7.5 * math.cos(math.radians(a)), 16 + 7.5 * math.sin(math.radians(a))) for a in range(0, 360, 60)]
        petals = "".join(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="6.5" fill="{fill}" {extra}/>' for x, y in centres)
        return petals + f'<circle cx="16" cy="16" r="8" fill="{fill}" {extra}/>'
    if kind == "drop":
        return f'<path d="{DROP}" fill="{fill}" {extra}/>'
    return f'<path d="{HEX}" fill="{fill}" stroke="{fill}" stroke-width="3" stroke-linejoin="round" {extra}/>'


def face(kind: str, color: str, cx: float, cy: float, scale: float, ring: float = 0) -> str:
    """A face centred on (cx, cy). `ring`: a band of the background around it, keeping it apart from the one behind."""
    out = f'<g transform="translate({cx - 16 * scale:.1f},{cy - 16 * scale:.1f}) scale({scale})">'
    if ring:
        if kind == "hex":  # its shape already has a stroke, for its rounded corners
            out += (f'<path d="{HEX}" fill="{BG}" stroke="{BG}" '
                    f'stroke-width="{ring + 3}" stroke-linejoin="round"/>')
        else:
            out += shape(kind, BG, f'stroke="{BG}" stroke-width="{ring}" stroke-linejoin="round"')
    return out + shape(kind, color) + EYES + "</g>"


svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#25272d"/>
      <stop offset="1" stop-color="{BG}"/>
    </linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/>
  <rect x="100.5" y="100.5" width="823" height="823" rx="184.5" fill="none" stroke="#ffffff" stroke-opacity="0.08"/>
  {face("flower", "#9085e9", 512, 405, 10.6)}
  {face("drop", "#199e70", 362, 622, 9.6, ring=3.2)}
  {face("hex", "#d95926", 664, 626, 9.6, ring=3.2)}
</svg>'''
open("icon.svg", "w").write(svg)
