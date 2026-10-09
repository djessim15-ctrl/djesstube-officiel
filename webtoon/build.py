# -*- coding: utf-8 -*-
"""DjessTube — le webtoon.
Assemble l'épisode 1 « Zéro pub » : lettrage des planches (bulles, cartouches,
logo), bande verticale façon webtoon, puis pagination PDF au format A5.

Sorties :
  out/ep1_strip.jpg          bande verticale (webtoon)
  djesstube-ep1_A5.pdf       édition A5 (300 dpi)
Usage : python3 webtoon/build.py
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = os.path.dirname(os.path.abspath(__file__))
PAN = os.path.join(BASE, "panneaux")
FONTS = os.path.join(BASE, "fonts")
OUT = os.path.join(BASE, "out")
os.makedirs(OUT, exist_ok=True)

ACC1 = (255, 61, 90)
ACC2 = (255, 122, 61)
BG = (10, 10, 15)
CREAM = (255, 246, 216)
NOIR = (12, 12, 16)

NAT_W, NAT_H = 848, 1264


def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), int(size))


F_BANG = lambda s: font("Bangers-Regular.ttf", s)
F_COMIC = lambda s: font("ComicNeue-Bold.ttf", s)
F_COMIC_R = lambda s: font("ComicNeue-Regular.ttf", s)


def grad_h(w, h):
    """Dégradé horizontal ACC1->ACC2."""
    g = Image.new("RGB", (max(1, w), 1))
    for x in range(max(1, w)):
        t = x / max(1, w - 1)
        g.putpixel((x, 0), tuple(int(a + (b - a) * t) for a, b in zip(ACC1, ACC2)))
    return g.resize((max(1, w), max(1, h)))


def text_size(d, txt, f):
    l, t, r, b = d.textbbox((0, 0), txt, font=f)
    return r - l, b - t


def draw_gradient_text(img, d, xy, txt, f, anchor="mm"):
    """Texte rempli du dégradé rouge/orange + léger halo sombre."""
    tmp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    td.text(xy, txt, font=f, fill=(255, 255, 255, 255), anchor=anchor)
    bbox = tmp.getbbox()
    if bbox is None:
        return
    l, t, r, b = bbox
    mask = tmp.split()[3]
    g = grad_h(r - l, b - t).convert("RGBA")
    g.putalpha(mask.crop((l, t, r, b)))
    img.paste(g, (l, t), g)


def draw_spaced(d, xy, txt, f, fill, spacing, anchor="mm"):
    """Texte avec espacement des lettres, centré en xy."""
    widths = [d.textlength(c, font=f) for c in txt]
    total = sum(widths) + spacing * (len(txt) - 1)
    x = xy[0] - total / 2
    for c, w in zip(txt, widths):
        d.text((x, xy[1]), c, font=f, fill=fill, anchor="la")
        x += w + spacing


def wrap(txt, f, maxw, d):
    lines, cur = [], ""
    for w in txt.split():
        test = (cur + " " + w).strip()
        if text_size(d, test, f)[0] <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def bulle(img, rect, txt, fs, tail):
    """Bulle de dialogue. rect=(x1,y1,x2,y2) en coords natives ; tail=(tx,ty) natif."""
    s = img.width / NAT_W
    d = ImageDraw.Draw(img)
    f = F_COMIC(fs * s)
    pad = 22 * s
    bw = max(3, 5 * s)
    x1, y1, x2, y2 = [v * s for v in rect]
    maxw = (x2 - x1) - 2 * pad
    lines = wrap(txt, f, maxw, d)
    lh = text_size(d, "Ag", f)[1] * 1.25
    h = lh * len(lines) + 2 * pad
    body = (x1, y1, x2, y1 + h)
    r = 26 * s
    tx, ty = tail[0] * s, tail[1] * s
    bx = min(max(tx, x1 + 40 * s), x2 - 40 * s)
    # queue : triangle noir puis ouverture blanche
    tri_b = [(bx - 26 * s, y1 + h - 2 * s), (bx + 26 * s, y1 + h - 2 * s), (tx, ty)]
    d.polygon(tri_b, fill=NOIR)
    d.rounded_rectangle(body, radius=r, fill=(255, 255, 255), outline=NOIR, width=int(bw))
    ty2 = ty - (14 * s if ty > y1 + h else -14 * s)
    tri_w = [(bx - 18 * s, y1 + h - bw), (bx + 18 * s, y1 + h - bw), (bx + (tx - bx) * 0.82, ty2)]
    d.polygon(tri_w, fill=(255, 255, 255))
    yy = y1 + pad + lh / 2
    for ln in lines:
        d.text(((x1 + x2) / 2, yy), ln, font=f, fill=NOIR, anchor="ma")
        yy += lh


def cartouche(img, rect, txt, fs):
    s = img.width / NAT_W
    d = ImageDraw.Draw(img)
    f = F_COMIC(fs * s)
    x1, y1, x2, y2 = [v * s for v in rect]
    d.rounded_rectangle((x1, y1, x2, y2), radius=10 * s, fill=CREAM, outline=NOIR, width=int(4 * s))
    lines = txt.split("\n")
    lh = text_size(d, "Ag", f)[1] * 1.3
    yy = (y1 + y2) / 2 - lh * (len(lines) - 1) / 2
    for ln in lines:
        d.text(((x1 + x2) / 2, yy), ln, font=f, fill=NOIR, anchor="mm")
        yy += lh


def logo(img, cx, cy, size):
    """Logo « DjessTube » : Djess blanc + Tube en pastille dégradée."""
    d = ImageDraw.Draw(img)
    f = F_BANG(size)
    w1, h1 = text_size(d, "Djess", f)
    w2, h2 = text_size(d, "Tube", f)
    pad = size * 0.22
    total = w1 + 6 + w2 + 2 * pad
    x = cx - total / 2
    d.text((x, cy), "Djess", font=f, fill=(255, 255, 255), anchor="lm")
    x2 = x + w1 + 6
    g = grad_h(int(w2 + 2 * pad), int(h2 + pad))
    mask = Image.new("L", g.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, g.size[0] - 1, g.size[1] - 1), radius=int((h2 + pad) / 2), fill=255)
    img.paste(g, (int(x2), int(cy - (h2 + pad) / 2)), mask)
    d.text((x2 + pad, cy), "Tube", font=f, fill=(255, 255, 255), anchor="lm")


def sfx(img, xy, txt, fs, rot=-8, fill=ACC2):
    s = img.width / NAT_W
    f = F_BANG(fs * s)
    t = Image.new("RGBA", (int(fs * 8 * s), int(fs * 3 * s)), (0, 0, 0, 0))
    td = ImageDraw.Draw(t)
    td.text((t.size[0] / 2, t.size[1] / 2), txt, font=f, fill=fill + (255,), anchor="mm")
    t = t.rotate(rot, expand=True, resample=Image.BICUBIC)
    img.paste(t, (int(xy[0] * s - t.size[0] / 2), int(xy[1] * s - t.size[1] / 2)), t)


def charge(img):
    p = Image.open(os.path.join(PAN, img)).convert("RGB")
    return p


OVERLAYS = {
    "01_cover.jpg": lambda img: (
        draw_spaced(ImageDraw.Draw(img), (img.width / 2, 62 * img.width / NAT_W),
                    "UN WEBTOON MAISON", F_COMIC(20 * img.width / NAT_W), (230, 230, 240), 6),
        logo(img, img.width / 2, 158 * img.width / NAT_W, 108 * img.width / NAT_W),
        draw_gradient_text(img, ImageDraw.Draw(img),
                           (img.width / 2, 262 * img.width / NAT_W),
                           "ÉPISODE 1 : ZÉRO PUB", F_BANG(46 * img.width / NAT_W)),
    ),
    "02_pubs.jpg": lambda img: (
        cartouche(img, (36, 40, 470, 152), "2 H 47 DU MATIN.\nCHAMBRE DE LÉO.", 28),
        bulle(img, (420, 120, 816, 340),
              "30 secondes de pub… pour une vidéo de 2 minutes ?!", 30, (610, 452)),
    ),
    "03_code.jpg": lambda img: (
        bulle(img, (452, 36, 818, 250),
              "Personne ne le fait ? Alors je le fais MOI.", 32, (585, 330)),
        sfx(img, (240, 1160), "TAK TAK TAK !", 40, -6, ACC1),
    ),
    "04_lancement.jpg": lambda img: (
        bulle(img, (36, 40, 520, 240), "DjessTube v1.0… Allez. Lance-toi.", 30, (565, 430)),
        ImageDraw.Draw(img).text((185 * img.width / NAT_W, 537 * img.width / NAT_W),
                                 "DjessTube", font=F_COMIC(24 * img.width / NAT_W),
                                 fill=(255, 255, 255), anchor="lm"),
        ImageDraw.Draw(img).rounded_rectangle(
            (392 * img.width / NAT_W, 742 * img.width / NAT_W,
             476 * img.width / NAT_W, 786 * img.width / NAT_W),
            radius=int(9 * img.width / NAT_W), fill=ACC1),
        ImageDraw.Draw(img).text((434 * img.width / NAT_W, 764 * img.width / NAT_W),
                                 "1080p", font=F_COMIC(24 * img.width / NAT_W),
                                 fill=(255, 255, 255), anchor="mm"),
    ),
    "05_soer.jpg": lambda img: (
        bulle(img, (26, 56, 340, 330), "Wouhou ! Zéro pub, et ça télécharge trop vite !", 30,
              (365, 420)),
    ),
    "06_final.jpg": lambda img: (
        draw_gradient_text(img, ImageDraw.Draw(img), (img.width / 2, 175 * img.width / NAT_W),
                           "YOUTUBE EN MIEUX.", F_BANG(62 * img.width / NAT_W)),
        draw_spaced(ImageDraw.Draw(img), (img.width / 2, 262 * img.width / NAT_W),
                    "EN LOCAL.", F_BANG(52 * img.width / NAT_W), (255, 255, 255), 4),
    ),
}


def composed(name, width):
    img = charge(name).resize((width, int(NAT_H * width / NAT_W)), Image.LANCZOS)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=90))
    OVERLAYS[name](img)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, img.width - 1, img.height - 1), outline=(0, 0, 0), width=max(3, int(4 * width / NAT_W)))
    return img


def badge_1080p():
    """Petit badge 1080p ajouté sur l'écran de la planche 4 (après composition)."""
    pass


# --------------------------------------------------------------- bande webtoon
STRIP_W = 1696


def carte_haut():
    h = 560
    img = Image.new("RGB", (STRIP_W, h), BG)
    d = ImageDraw.Draw(img)
    draw_spaced(d, (STRIP_W / 2, 90), "D J E S S T U B E   P R É S E N T E",
                F_COMIC(30), (154, 154, 173), 2)
    logo(img, STRIP_W / 2, 230, 150)
    draw_gradient_text(img, d, (STRIP_W / 2, 392), "ÉPISODE 1 : ZÉRO PUB", F_BANG(72))
    d.line((STRIP_W / 2 - 260, 470, STRIP_W / 2 + 260, 470), fill=(42, 42, 56), width=3)
    d.text((STRIP_W / 2, 512), "un webtoon maison · lecture verticale",
           font=F_COMIC_R(28), fill=(154, 154, 173), anchor="mm")
    return img


def carte_fin():
    h = 700
    img = Image.new("RGB", (STRIP_W, h), BG)
    d = ImageDraw.Draw(img)
    draw_gradient_text(img, d, (STRIP_W / 2, 220), "À SUIVRE…", F_BANG(140))
    d.text((STRIP_W / 2, 380), "Épisode 1 : Zéro pub — DjessTube, le webtoon",
           font=F_COMIC(34), fill=(242, 242, 247), anchor="mm")
    d.text((STRIP_W / 2, 448), "Un webtoon fait maison · édition A5 incluse",
           font=F_COMIC_R(30), fill=(154, 154, 173), anchor="mm")
    logo(img, STRIP_W / 2, 570, 70)
    return img


def build_strip():
    gap = 26
    parts = [carte_haut()]
    for n in ["01_cover.jpg", "02_pubs.jpg", "03_code.jpg", "04_lancement.jpg",
              "05_soer.jpg", "06_final.jpg"]:
        parts.append(composed(n, STRIP_W))
    parts.append(carte_fin())
    H = sum(p.height for p in parts) + gap * (len(parts) - 1)
    strip = Image.new("RGB", (STRIP_W, H), BG)
    y = 0
    for p in parts:
        strip.paste(p, (0, y))
        y += p.height + gap
    out = os.path.join(OUT, "ep1_strip.jpg")
    strip.save(out, "JPEG", quality=90)
    print("strip:", out, strip.size)
    return out


# ------------------------------------------------------------------ pages A5
MM = 300 / 25.4
W, H = int(round(148 * MM)), int(round(210 * MM))          # 1748 x 2480


def page_fond():
    return Image.new("RGB", (W, H), (11, 11, 17))


def page_couverture():
    img = composed("01_cover.jpg", W)
    top = (img.height - H) // 2
    return img.crop((0, top, W, top + H))


def page_panneau(nom, num):
    img = page_fond()
    d = ImageDraw.Draw(img)
    d.text((94, 66), "DJESSTUBE — ÉP. 1 : ZÉRO PUB", font=F_COMIC(30),
           fill=(154, 154, 173), anchor="lm")
    d.text((W - 94, 66), str(num), font=F_BANG(40), fill=ACC2, anchor="rm")
    d.line((94, 96, W - 94, 96), fill=(42, 42, 56), width=2)
    p = composed(nom, W - 168)
    y = 128
    img.paste(p, (84, y))
    d.text((W / 2, H - 52), "· %d ·" % num, font=F_COMIC(28), fill=(154, 154, 173), anchor="mm")
    return img


def page_fin(num):
    img = page_fond()
    d = ImageDraw.Draw(img)
    logo(img, W / 2, 500, 130)
    draw_gradient_text(img, d, (W / 2, 900), "À SUIVRE…", F_BANG(170))
    d.text((W / 2, 1180), "Épisode 1 : Zéro pub", font=F_COMIC(44), fill=(242, 242, 247), anchor="mm")
    d.text((W / 2, 1260), "DjessTube — YouTube en mieux, en local.", font=F_COMIC_R(34),
           fill=(154, 154, 173), anchor="mm")
    d.text((W / 2, 1330), "Recherche, lecture 1080p sans pub,", font=F_COMIC_R(34),
           fill=(154, 154, 173), anchor="mm")
    d.text((W / 2, 1395), "téléchargements & sous-titres FR.", font=F_COMIC_R(34),
           fill=(154, 154, 173), anchor="mm")
    d.line((W / 2 - 200, 1600, W / 2 + 200, 1600), fill=(42, 42, 56), width=3)
    d.text((W / 2, 1700), "Un webtoon fait maison.", font=F_COMIC(32), fill=(242, 242, 247), anchor="mm")
    d.text((W / 2, H - 52), "· %d ·" % num, font=F_COMIC(28), fill=(154, 154, 173), anchor="mm")
    return img


def build_pdf():
    pages = [page_couverture()]
    noms = ["02_pubs.jpg", "03_code.jpg", "04_lancement.jpg", "05_soer.jpg", "06_final.jpg"]
    for i, n in enumerate(noms, start=2):
        pages.append(page_panneau(n, i))
    pages.append(page_fin(len(noms) + 2))
    paths = []
    for i, p in enumerate(pages):
        q = os.path.join(OUT, "_page%02d.jpg" % i)
        p.save(q, "JPEG", quality=93)
        paths.append(q)
    import img2pdf
    pdf = os.path.join(BASE, "djesstube-ep1_A5.pdf")
    with open(pdf, "wb") as f:
        f.write(img2pdf.convert(paths,
                                layout_fun=img2pdf.get_fixed_dpi_layout_fun((300, 300))))
    print("pdf:", pdf, len(pages), "pages")
    # aperçus de contrôle
    for i, p in enumerate(pages):
        p.resize((p.width // 4, p.height // 4), Image.LANCZOS).save(
            os.path.join(OUT, "apercu_p%d.jpg" % (i + 1)), "JPEG", quality=80)
    return pdf


if __name__ == "__main__":
    build_strip()
    build_pdf()
