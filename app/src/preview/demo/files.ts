// Files the bots shared, for the preview: one of each kind the app shows. They're made here (a render drawn on a
// canvas, a chord written as a WAV, a page, some code) rather than kept as files, except a three-second video.

import type { Attachment } from "../../types";
import sampleVideo from "./sample.mp4?url";

/** Each shared file's path → what the preview draws for it. */
export const demoFiles = new Map<string, string>();

let nextId = 0xa11ce0;
export function shared(name: string, mime: string, url: string, size: number): Attachment {
  const id = (nextId++).toString(16).padStart(12, "0");
  const path = `~/.crewbots/crew/attachments/${id}/${name}`;
  demoFiles.set(path, url);
  return { id, name, path, mime, size, kind: mime.startsWith("image/") ? "image" : "file" };
}

const text = (body: string, mime = "text/plain") => `data:${mime};charset=utf-8,${encodeURIComponent(body)}`;

/** A render, as a 3D tool would make one: a lit sphere on a floor. */
function render(): string {
  const canvas = globalThis.document?.createElement("canvas");
  const g = canvas?.getContext("2d");
  if (!canvas || !g) return "";
  canvas.width = 640;
  canvas.height = 400;
  const sky = g.createLinearGradient(0, 0, 0, 400);
  sky.addColorStop(0, "#2b3d5c");
  sky.addColorStop(1, "#c98b5e");
  g.fillStyle = sky;
  g.fillRect(0, 0, 640, 400);
  g.fillStyle = "#1d1a18";
  g.fillRect(0, 300, 640, 100);
  g.fillStyle = "rgba(0,0,0,0.45)";
  g.beginPath();
  g.ellipse(330, 305, 110, 18, 0, 0, Math.PI * 2);
  g.fill();
  const ball = g.createRadialGradient(290, 170, 10, 320, 210, 110);
  ball.addColorStop(0, "#ffe3c4");
  ball.addColorStop(0.35, "#e0733c");
  ball.addColorStop(1, "#3a1508");
  g.fillStyle = ball;
  g.beginPath();
  g.arc(320, 210, 95, 0, Math.PI * 2);
  g.fill();
  return canvas.toDataURL("image/png");
}

/** A second and a half of a C major chord, as a WAV. */
function chord(): string {
  const rate = 8000;
  const n = Math.floor(rate * 1.5);
  const bytes = new Uint8Array(44 + n);
  const view = new DataView(bytes.buffer);
  const ascii = (at: number, s: string) => [...s].forEach((c, i) => view.setUint8(at + i, c.charCodeAt(0)));
  ascii(0, "RIFF");
  view.setUint32(4, 36 + n, true);
  ascii(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true); // 8-bit
  ascii(36, "data");
  view.setUint32(40, n, true);
  for (let i = 0; i < n; i++) {
    const t = i / rate;
    const s = [261.6, 329.6, 392].reduce((sum, f) => sum + Math.sin(2 * Math.PI * f * t), 0) / 3;
    bytes[44 + i] = 128 + Math.round(90 * s * Math.min(1, (1.5 - t) * 4));
  }
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return `data:audio/wav;base64,${btoa(binary)}`;
}

const chart = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180" width="320" height="180">
<rect width="320" height="180" fill="#ffffff"/>
<g fill="#2a78d6"><rect x="40" y="70" width="48" height="90"/><rect x="120" y="40" width="48" height="120"/><rect x="200" y="100" width="48" height="60"/></g>
<g font-family="-apple-system, sans-serif" font-size="12" fill="#333" text-anchor="middle"><text x="64" y="62">1,840</text><text x="144" y="32">2,410</text><text x="224" y="92">1,120</text>
<text x="64" y="176">CSV</text><text x="144" y="176">XLSX</text><text x="224" y="176">PDF</text></g></svg>`;

const page = `<!doctype html><html><head><style>body{font:14px -apple-system,sans-serif;margin:16px;color:#222;background:#fff}
h1{font-size:18px;margin:0 0 8px}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:4px 10px;text-align:left}</style></head>
<body><h1>Export usage, last 30 days</h1><p id="note">Exports by format, with the share that used a filter.</p>
<table><tr><th>Format</th><th>Exports</th><th>Filtered</th></tr><tr><td>CSV</td><td>1,840</td><td>62%</td></tr>
<tr><td>XLSX</td><td>2,410</td><td>48%</td></tr><tr><td>PDF</td><td>1,120</td><td>15%</td></tr></table>
<script>document.getElementById("note").textContent = "Its scripts ran: this line was written by one.";</script></body></html>`;

const code = `import csv
from io import StringIO


def export_rows(rows, filters):
    """The rows the page shows, as CSV: the filters apply here too."""
    out = StringIO()
    writer = csv.writer(out)
    for row in rows:
        if all(f(row) for f in filters):  # an empty filter list keeps every row
            writer.writerow(row)
    return out.getvalue()


def test_an_empty_filter_exports_everything():
    assert export_rows([[1, "a"], [2, "b"]], []) == "1,a\\r\\n2,b\\r\\n"
${Array.from({ length: 30 }, (_, i) => `\n\ndef test_case_${i}():\n    assert export_rows([[${i}]], []) == "${i}\\r\\n"`).join("")}
`;

const firstPage = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 340 440" width="340" height="440"><rect width="340" height="440" fill="#fff"/>
<text x="28" y="52" font-family="Georgia, serif" font-size="20" fill="#111">Release notes 2.4</text>
<g fill="#c9c9c9"><rect x="28" y="72" width="284" height="6"/><rect x="28" y="86" width="250" height="6"/><rect x="28" y="100" width="270" height="6"/>
<rect x="28" y="130" width="284" height="6"/><rect x="28" y="144" width="200" height="6"/><rect x="28" y="174" width="284" height="80" fill="#e6eefb"/>
<rect x="28" y="272" width="284" height="6"/><rect x="28" y="286" width="230" height="6"/><rect x="28" y="300" width="260" height="6"/></g></svg>`;

export const files = {
  render: shared("hero-render.png", "image/png", render(), 412_000),
  chart: shared("exports-by-format.svg", "image/svg+xml", text(chart, "image/svg+xml"), 1_400),
  cut: shared("launch-cut.mp4", "video/mp4", sampleVideo, 21_979),
  jingle: shared("notification-chime.wav", "audio/wav", chord(), 12_044),
  notes: shared("release-notes-2.4.pdf", "application/pdf", "", 184_000),
  report: shared("export-usage.html", "text/html", text(page, "text/html"), 1_200),
  code: shared("export.py", "text/x-python", text(code), 2_300),
  scene: shared("hero.blend", "application/octet-stream", "", 8_400_000),
  viewport: shared("image.png", "image/png", render(), 96_000), // Blender's screenshot of its viewport, over MCP
};
demoFiles.set(`${files.notes.path}.png`, text(firstPage, "image/svg+xml")); // Quick Look's first page of it
