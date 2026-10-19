"""Gera os arquivos do logotipo (ícone "user-group" do Lucide) a partir da geometria oficial.

Saídas, em frontend/assets/:
    logo-icone.svg   fonte vetorial (usada como favicon moderno e para qualquer ampliação)
    favicon.png      64x64   (navegadores sem suporte a favicon SVG)
    logo-icone.png   256x256 (apple-touch-icon e uso geral)

Uso:
    pip install pillow
    python scripts/gerar_icones.py

O ícone é o "user-group" do Lucide (https://lucide.dev/icons/user-group), licença ISC.
Geometria copiada de lucide-static v1.59.0. Para mudar a cor de fundo ou do traço, edite as
constantes abaixo e rode de novo.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

FUNDO = "#A92823"   # vermelho-acento do projeto (--vermelho)
TRACO = "#F4F2EE"   # off-white
MARGEM = 10 / 64    # fração livre em volta do ícone
SUPER = 8           # super-amostragem para suavizar as bordas

DESTINO = Path(__file__).resolve().parent.parent / "frontend" / "assets"

# Geometria do Lucide "user-group" (viewBox 24x24, traço 2, pontas arredondadas)
SVG_ICONE = """<path d="M17 21v-1a2 2 0 00-2-2H9a2 2 0 00-2 2v1"/>
    <path d="M19 10h1a2 2 0 012 2v1"/>
    <path d="M5 10H4a2 2 0 00-2 2v1"/>
    <circle cx="12" cy="11" r="3"/>
    <circle cx="18" cy="4" r="2"/>
    <circle cx="6" cy="4" r="2"/>"""


def arco(cx: float, cy: float, r: float, ini: float, fim: float, passos: int = 24) -> list[tuple[float, float]]:
    """Pontos de um arco (ângulos em graus, eixo y para baixo, como no SVG)."""
    return [(cx + r * math.cos(math.radians(ini + (fim - ini) * i / passos)),
             cy + r * math.sin(math.radians(ini + (fim - ini) * i / passos))) for i in range(passos + 1)]


# Os três traços abertos do ícone, em coordenadas 24x24
TRACOS = [
    [(17, 21), (17, 20), *arco(15, 20, 2, 0, -90), (9, 18), *arco(9, 20, 2, -90, -180), (7, 20), (7, 21)],
    [(19, 10), (20, 10), *arco(20, 12, 2, -90, 0), (22, 12), (22, 13)],
    [(5, 10), (4, 10), *arco(4, 12, 2, -90, -180), (2, 12), (2, 13)],
]
CIRCULOS = [(12, 11, 3), (18, 4, 2), (6, 4, 2)]


def desenhar(tamanho: int) -> Image.Image:
    n = tamanho * SUPER
    img = Image.new("RGB", (n, n), FUNDO)
    d = ImageDraw.Draw(img)
    lado = n * (1 - 2 * MARGEM)
    k = lado / 24               # unidade do ícone em pixels
    ox = oy = n * MARGEM
    espessura = 2 * k
    mapear = lambda p: (ox + p[0] * k, oy + p[1] * k)  # noqa: E731

    raio = espessura / 2
    for tr in TRACOS:
        pts = [mapear(p) for p in tr]
        # Traço = círculos carimbados a cada ~0,5 px ao longo do caminho: pontas e junções redondas
        # (como stroke-linecap/linejoin "round" do SVG), sem os chuviscados do d.line(joint="curve").
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            passos = max(1, int(math.hypot(x1 - x0, y1 - y0) * 2))
            for i in range(passos + 1):
                x, y = x0 + (x1 - x0) * i / passos, y0 + (y1 - y0) * i / passos
                d.ellipse((x - raio, y - raio, x + raio, y + raio), fill=TRACO)
    for cx, cy, r in CIRCULOS:
        x, y = mapear((cx, cy))
        ext = r * k + espessura / 2  # raio externo do anel (o traço é centrado no raio nominal)
        d.ellipse((x - ext, y - ext, x + ext, y + ext), outline=TRACO, width=round(espessura))
    return img.resize((tamanho, tamanho), Image.LANCZOS)


def svg() -> str:
    escala = (1 - 2 * MARGEM) * 64 / 24
    desloc = MARGEM * 64
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="ImpactoCidadão">
  <!-- Ícone user-group do Lucide (ISC) — https://lucide.dev/icons/user-group -->
  <rect width="64" height="64" fill="{FUNDO}"/>
  <g transform="translate({desloc:.3f} {desloc:.3f}) scale({escala:.4f})" fill="none" stroke="{TRACO}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    {SVG_ICONE}
  </g>
</svg>
"""


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    (DESTINO / "logo-icone.svg").write_text(svg(), encoding="utf-8")
    desenhar(64).save(DESTINO / "favicon.png", optimize=True)
    desenhar(256).save(DESTINO / "logo-icone.png", optimize=True)
    print("Gerados em", DESTINO, ":", "logo-icone.svg, favicon.png (64), logo-icone.png (256)")


if __name__ == "__main__":
    main()
