"""
Generate a STDWeb photometry tutorial PDF from real tasks:
- Task 3776 : photometry SN2026fvx G-band
- Task 3815 : template subtraction SN2026fvx G-band
"""

import asyncio
import os
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import async_playwright

BASE_URL    = "http://localhost:7000"
TASK_PHOT   = 3776   # photometry_done  – SN2026fvx G
TASK_SUB    = 3815   # subtraction_done – SN2026fvx G
OUT_DIR     = Path("/tmp/stdweb_tutorial")
OUT_DIR.mkdir(exist_ok=True)

USERNAME = "pyl"

STEPS = []   # filled during capture

# ── fonts ─────────────────────────────────────────────────────────────────────
def _font(size, bold=False):
    variant = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = f"/usr/share/fonts/truetype/dejavu/{variant}"
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def get_session_cookie():
    """Create a Django session for USERNAME directly, bypassing password."""
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stdweb.settings")
    django.setup()

    from django.contrib.auth.models import User
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY

    user = User.objects.get(username=USERNAME)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return session.session_key


async def screenshot(page, name, label, note="", section=None, highlight=None, action=None):
    """
    Capture viewport screenshot. Optionally highlight a button/element.

    highlight : CSS selector or visible text (prefixed with 'text:') to find and draw a red box around
    action    : string shown in bottom callout box, e.g. "Pour lancer X, cliquez sur « Run »"
    """
    path = OUT_DIR / f"{name}.png"
    await page.screenshot(path=str(path), full_page=False)

    bbox = None
    if highlight:
        try:
            if highlight.startswith("text:"):
                loc = page.get_by_text(highlight[5:], exact=True).first
            else:
                loc = page.locator(highlight).first
            bbox = await loc.bounding_box()
        except Exception:
            bbox = None

    STEPS.append({
        "path": path, "label": label, "note": note,
        "section": section, "bbox": bbox, "action": action,
    })
    print(f"  ✓ {name}" + (f"  [box: {bbox}]" if bbox else ""))


async def scroll_to(page, text, fallback_y):
    try:
        loc = page.locator(f"text={text}").first
        await loc.scroll_into_view_if_needed()
        await page.wait_for_timeout(400)
    except Exception:
        await page.evaluate(f"window.scrollTo(0, {fallback_y})")


async def capture():
    from asgiref.sync import sync_to_async
    session_key = await sync_to_async(get_session_cookie)()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        await ctx.add_cookies([{
            "name": "sessionid", "value": session_key,
            "domain": "localhost", "path": "/",
        }])
        page = await ctx.new_page()

        # ══════════════════════════════════════════════════════════════════════
        # SECTION A — DÉMARRAGE
        # ══════════════════════════════════════════════════════════════════════

        print("\n── Section A : Démarrage ──")

        print("A1: Login form")
        await page.goto(f"{BASE_URL}/login/")
        await page.wait_for_load_state("networkidle")
        await page.fill("input[name=username]", USERNAME)
        await screenshot(page, "A1_login", "Connexion à STDWeb",
            "Accédez à http://votre-serveur:7000, saisissez votre identifiant et mot de passe.",
            section="A",
            highlight="text:Log in",
            action="Pour vous connecter, cliquez sur « Log in »")

        print("A2: Task list")
        await page.goto(f"{BASE_URL}/tasks")
        await page.wait_for_load_state("networkidle")
        await screenshot(page, "A2_tasklist", "Liste des tâches",
            "La liste affiche toutes vos tâches avec leur état (uploaded, photometry_done, subtraction_done…).",
            section="A",
            highlight="text:Upload",
            action="Pour créer une nouvelle tâche, cliquez sur « Upload »")

        print("A3: Upload form")
        await page.goto(f"{BASE_URL}/upload")
        await page.wait_for_load_state("networkidle")
        await screenshot(page, "A3_upload", "Téléversement d'une image FITS",
            "Sélectionnez votre fichier FITS. Renseignez le gain (e⁻/ADU) et le niveau de saturation "
            "si connus — sinon STDWeb les estime automatiquement.",
            section="A",
            highlight="button[type=submit]",
            action="Pour téléverser l'image, cliquez sur le bouton « Upload »")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION B — PHOTOMÉTRIE DIRECTE  (tâche 3776)
        # ══════════════════════════════════════════════════════════════════════

        print("\n── Section B : Photométrie directe ──")

        await page.goto(f"{BASE_URL}/tasks/{TASK_PHOT}")
        await page.wait_for_load_state("networkidle")

        print("B1: Inspection form")
        await page.evaluate("window.scrollTo(0, 0)")
        await screenshot(page, "B1_inspect_form", "Paramétrage de l'inspection initiale",
            "Entrez le nom ou les coordonnées de la cible (résolution SIMBAD/TNS automatique), "
            "le gain et le niveau de saturation. Cochez « Mask cosmics » pour masquer les rayons cosmiques.",
            section="B",
            highlight="text:Run initial inspection",
            action="Pour lancer l'inspection de l'image, cliquez sur « Run initial inspection »")

        print("B2: Inspection result")
        await page.evaluate("window.scrollTo(0, 700)")
        await screenshot(page, "B2_inspect_result", "Résultat de l'inspection — image annotée",
            "Les étoiles détectées apparaissent en vert, la cible en rouge. "
            "Les métriques (FWHM, fond de ciel, échelle de plaque) sont affichées dans les logs.",
            section="B",
            highlight="text:Photometry",
            action="L'inspection est terminée. Faites défiler vers le bas pour accéder au formulaire Photometry")

        print("B3: Photometry form")
        await scroll_to(page, "Photometry", 1400)
        await screenshot(page, "B3_phot_form", "Formulaire de photométrie",
            "Choisissez le catalogue (Gaia EDR3 recommandé), le filtre, et la magnitude limite.",
            section="B",
            highlight="text:Run Photometry",
            action="Pour lancer la calibration et la mesure de magnitude, cliquez sur « Run Photometry »")

        print("B4: Photometry result")
        await scroll_to(page, "photometry_done", 2200)
        await screenshot(page, "B4_phot_result", "Résultat — magnitude calibrée",
            "Le résultat donne la magnitude, l'erreur photométrique, le rapport signal/bruit et le point zéro.",
            section="B",
            highlight="text:target.vot",
            action="Pour télécharger les résultats de la cible, cliquez sur « target.vot »")

        print("B5: Calibration plot")
        await page.evaluate("window.scrollTo(0, 3200)")
        await screenshot(page, "B5_calib_plot", "Courbe de calibration photométrique",
            "Magnitude instrumentale vs magnitude catalogue. La droite ajustée détermine le point zéro. "
            "Les étoiles aberrantes sont automatiquement rejetées (sigma-clipping).",
            section="B")

        print("B6: Download")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(300)
        await screenshot(page, "B6_download", "Export des résultats (VOTable / CSV)",
            "Téléchargez les résultats en VOTable (.vot, compatible TopCat/STILTS) ou CSV.",
            section="B",
            highlight="text:objects.vot",
            action="Pour télécharger toutes les étoiles mesurées, cliquez sur « objects.vot »")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION C — SOUSTRACTION DE MOTIF  (tâche 3815)
        # ══════════════════════════════════════════════════════════════════════

        print("\n── Section C : Soustraction de motif ──")

        await page.goto(f"{BASE_URL}/tasks/{TASK_SUB}")
        await page.wait_for_load_state("networkidle")

        print("C1: Task overview — photometry done, subtraction available")
        await page.evaluate("window.scrollTo(0, 0)")
        await screenshot(page, "C1_sub_task", f"Tâche #{TASK_SUB} — état subtraction_done",
            "En haut de la tâche : l'état est subtraction_done. La soustraction est disponible "
            "dès que l'inspection initiale et la photométrie ont été effectuées.",
            section="C",
            highlight="text:subtraction_done",
            action="La tâche est prête. Faites défiler vers le bas pour accéder à « Template subtraction »")

        print("C2: Photometry result on this task")
        await page.evaluate("window.scrollTo(0, 1600)")
        await page.wait_for_timeout(400)
        await screenshot(page, "C2_phot_on_sub_task", "Photométrie directe sur la même tâche",
            "La photométrie directe est toujours présente. La soustraction vient en complément "
            "pour éliminer la contamination galactique.",
            section="C")

        print("C3: Subtraction form")
        await page.evaluate("window.scrollTo(0, 4600)")
        await page.wait_for_timeout(400)
        await screenshot(page, "C3_sub_form", "Formulaire de soustraction de motif",
            "Choisissez le relevé de référence (PS1, ZTF…), la bande, et la méthode (HOTPANTS recommandé). "
            "Le mode « Target photometry » centre sur la cible.",
            section="C",
            highlight="text:Run subtraction",
            action="Pour lancer la soustraction de motif, cliquez sur « Run subtraction »")

        print("C4: Subtraction result — difference image")
        await page.evaluate("window.scrollTo(0, 5100)")
        await page.wait_for_timeout(400)
        await screenshot(page, "C4_sub_result", "Résultat — image différence et magnitude",
            "L'image différence (science − template convoluée) isole la SN de sa galaxie hôte. "
            "La magnitude mesurée est exempte de contamination galactique.",
            section="C",
            highlight="text:sub_target.vot",
            action="Pour télécharger la magnitude mesurée sur l'image différence, cliquez sur « sub_target.vot »")

        await browser.close()
        print(f"\n{len(STEPS)} screenshots captured in {OUT_DIR}")


# ── PDF helpers ───────────────────────────────────────────────────────────────

SECTION_META = {
    "A": {"title": "A — Démarrage",             "color": (30,  80, 140)},
    "B": {"title": "B — Photométrie directe",   "color": (20, 110,  60)},
    "C": {"title": "C — Soustraction de motif", "color": (130, 50,  20)},
    "D": {"title": "D — Référence des options", "color": (80,  40, 120)},
}


def add_caption(img: Image.Image, label: str, note: str, step: int, section: str,
                bbox=None, action=None) -> Image.Image:
    meta   = SECTION_META.get(section, SECTION_META["A"])
    color  = meta["color"]
    W, H   = img.size
    header_h = 56

    # annotation layer on top of screenshot
    annotated = img.copy()
    if bbox:
        vp_h = img.size[1]
        in_view = (bbox["y"] >= 0 and bbox["y"] < vp_h
                   and (bbox["y"] + bbox["height"]) > 0
                   and bbox["width"] > 0 and bbox["height"] > 0)
        if in_view:
            da  = ImageDraw.Draw(annotated)
            pad = 6
            x0 = max(0, int(bbox["x"]) - pad)
            y0 = max(0, int(bbox["y"]) - pad)
            x1 = min(W, int(bbox["x"] + bbox["width"])  + pad)
            y1 = min(H, int(bbox["y"] + bbox["height"]) + pad)
            if x1 > x0 and y1 > y0:
                # thick red rectangle
                for t in range(3):
                    da.rectangle([x0 - t, y0 - t, x1 + t, y1 + t], outline=(220, 30, 30))
                # corner accents
                cl = 14
                for cx, cy, dx, dy in [(x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)]:
                    da.line([(cx, cy), (cx + dx * cl, cy)], fill=(255, 60, 60), width=4)
                    da.line([(cx, cy), (cx, cy + dy * cl)], fill=(255, 60, 60), width=4)

    # action callout box
    action_h = 0
    if action:
        lines    = textwrap.wrap(action, width=100)
        action_h = 20 + len(lines) * 30 + 10

    # note footer
    note_lines = textwrap.wrap(note, width=120) if note else []
    note_h     = (20 + len(note_lines) * 24 + 10) if note_lines else 0

    total_h = H + header_h + action_h + note_h

    out = Image.new("RGB", (W, total_h), (245, 245, 245))

    # header band
    hdr = Image.new("RGB", (W, header_h), color)
    dh  = ImageDraw.Draw(hdr)
    dh.text((14, 8),   f"Étape {step}", font=_font(20, bold=True), fill=(255, 255, 255))
    dh.text((110, 12), label,           font=_font(16),             fill=(220, 235, 255))
    dh.text((W - 260, 18), f" {meta['title']} ", font=_font(13), fill=(200, 220, 255))
    out.paste(hdr, (0, 0))

    # screenshot (annotated)
    out.paste(annotated, (0, header_h))

    y_cur = header_h + H

    # action callout box (red-tinted)
    if action:
        lines = textwrap.wrap(action, width=100)
        box_h = 20 + len(lines) * 30 + 10
        act = Image.new("RGB", (W, box_h), (255, 240, 240))
        da2 = ImageDraw.Draw(act)
        da2.rectangle([0, 0, W, 4], fill=(200, 30, 30))
        da2.rectangle([0, 0, 6, box_h], fill=(200, 30, 30))
        # arrow icon
        da2.polygon([(18, box_h//2 - 8), (18, box_h//2 + 8), (30, box_h//2)], fill=(200, 30, 30))
        for i, line in enumerate(lines):
            da2.text((38, 12 + i * 30), line, font=_font(16, bold=True), fill=(150, 10, 10))
        out.paste(act, (0, y_cur))
        y_cur += box_h

    # note footer (grey)
    if note_lines:
        ftr = Image.new("RGB", (W, note_h), (248, 248, 252))
        df  = ImageDraw.Draw(ftr)
        df.rectangle([0, 0, W, 3], fill=tuple(max(c - 40, 0) for c in color))
        for i, line in enumerate(note_lines):
            df.text((14, 10 + i * 24), line, font=_font(13), fill=(60, 60, 80))
        out.paste(ftr, (0, y_cur))

    return out


def make_section_divider(section: str, W: int = 1280, H: int = 140) -> Image.Image:
    meta = SECTION_META[section]
    img  = Image.new("RGB", (W, H), meta["color"])
    d    = ImageDraw.Draw(img)
    d.text((40, 30), meta["title"], font=_font(36, bold=True), fill=(255, 255, 255))
    return img


def make_options_page() -> Image.Image:
    """Full-page reference card for all photometry options."""
    W = 1280
    options = [
        ("Use color term",
         "Prend en compte un terme de couleur stellaire lors du calcul du point zéro.\n"
         "Recommandé avec filtre non standard ou capteur à large réponse spectrale. Activé par défaut."),
        ("Refine astrometry",
         "Raffine le WCS après la première solution astrométrique via SCAMP (ordre 3).\n"
         "Utile si la solution d'en-tête est approximative. S'active automatiquement après Blind match."),
        ("Blind match",
         "Résout l'astrométrie from scratch sans se fier à l'en-tête FITS.\n"
         "Indispensable si le WCS est absent ou si l'échelle de plaque est incorrecte (>100\"/pix).\n"
         "STDWeb l'active automatiquement dans ces cas."),
        ("Filter catalogue blends",
         "Supprime du catalogue les étoiles trop proches (doubles non résolues, blends).\n"
         "Regroupe les voisins à moins de 2×FWHM et fusionne ou élimine les groupes.\n"
         "Réduit les faux matches astrométriques et photométriques."),
        ("Pre-filter detections",
         "Avant calibration, un algorithme Isolation Forest rejette les détections non stellaires\n"
         "(galaxies, artefacts, rayons cosmiques). Une image diagnostic prefilter.png est sauvegardée.\n"
         "Les sources rejetées sont exclues de l'astrométrie fine."),
        ("Centroid targets",
         "Re-centre itérativement (5 passes) l'ouverture de photométrie sur le centroïde réel\n"
         "de la cible. Recommandé si la position prédite n'est pas parfaitement centrée sur le pixel."),
        ("Non-linearity",
         "Option présente dans l'interface mais non encore implémentée dans le code.\n"
         "La cocher n'a actuellement aucun effet."),
        ("Color term diagnostics",
         "Si le terme de couleur ajusté est aberrant (|terme| > 0,5 mag) ou si demandé,\n"
         "recalcule la calibration pour tous les filtres du catalogue.\n"
         "Utile pour déboguer une calibration suspecte — pas nécessaire en routine."),
        ("Inspect background",
         "Exporte la carte de fond de ciel SExtractor (image_bg.fits) et la carte RMS\n"
         "(image_rms.fits) dans le répertoire de la tâche.\n"
         "Pratique pour vérifier la qualité du fond, surtout sur champs encombrés."),
    ]

    # estimate height
    line_h   = 22
    title_h  = 70
    row_h    = 110
    total_h  = title_h + len(options) * row_h + 40
    img      = Image.new("RGB", (W, total_h), (250, 250, 252))
    d        = ImageDraw.Draw(img)

    # title bar
    meta = SECTION_META["D"]
    d.rectangle([0, 0, W, title_h], fill=meta["color"])
    d.text((30, 16), meta["title"] + "  —  Référence des options de photométrie",
           font=_font(24, bold=True), fill=(255, 255, 255))

    y = title_h + 10
    for i, (name, desc) in enumerate(options):
        bg = (240, 240, 248) if i % 2 == 0 else (250, 250, 255)
        d.rectangle([10, y, W - 10, y + row_h - 4], fill=bg, outline=(200, 200, 215))
        d.text((20, y + 8), f"☐  {name}", font=_font(16, bold=True), fill=(40, 40, 100))
        for j, line in enumerate(desc.split("\n")):
            d.text((36, y + 32 + j * line_h), line, font=_font(13), fill=(50, 50, 60))
        y += row_h

    return img


def make_recommendation_page() -> Image.Image:
    """Quick-reference table: which options to use in routine."""
    W = 1280
    rows = [
        ("Use color term",         "✅ Routine",    "Activée par défaut — ne pas décocher sauf test"),
        ("Refine astrometry",      "✅ Routine",    "Toujours utile"),
        ("Blind match",            "⚡ Si besoin",  "Seulement si WCS absent ou incorrect"),
        ("Filter catalogue blends","✅ Routine",    "Surtout pour les champs encombrés"),
        ("Pre-filter detections",  "✅ Routine",    "Recommandé sur la plupart des images"),
        ("Centroid targets",       "✅ Routine",    "Si cible précise (SN, astéroïde…)"),
        ("Non-linearity",          "❌ Non dispo",  "Non implémentée — sans effet"),
        ("Color term diagnostics", "⚡ Si besoin",  "Pour déboguer une calibration suspecte"),
        ("Inspect background",     "⚡ Si besoin",  "Pour vérification visuelle du fond"),
    ]

    title_h = 70
    row_h   = 52
    total_h = title_h + len(rows) * row_h + 20
    img     = Image.new("RGB", (W, total_h), (250, 250, 252))
    d       = ImageDraw.Draw(img)

    meta = SECTION_META["D"]
    d.rectangle([0, 0, W, title_h], fill=meta["color"])
    d.text((30, 16), "Récapitulatif — quand cocher chaque option ?",
           font=_font(24, bold=True), fill=(255, 255, 255))

    # column headers
    y = title_h
    d.rectangle([10, y, W - 10, y + row_h - 4], fill=(220, 210, 240))
    d.text((20,  y + 14), "Option",       font=_font(15, bold=True), fill=(40, 40, 80))
    d.text((430, y + 14), "Usage",        font=_font(15, bold=True), fill=(40, 40, 80))
    d.text((620, y + 14), "Commentaire",  font=_font(15, bold=True), fill=(40, 40, 80))
    y += row_h

    for i, (name, usage, comment) in enumerate(rows):
        bg = (240, 240, 248) if i % 2 == 0 else (250, 250, 255)
        d.rectangle([10, y, W - 10, y + row_h - 4], fill=bg, outline=(200, 200, 215))
        d.text((20,  y + 14), name,    font=_font(14), fill=(40, 40, 80))
        d.text((430, y + 14), usage,   font=_font(14, bold=True), fill=(20, 100, 40) if "✅" in usage else (140, 80, 10) if "⚡" in usage else (160, 40, 40))
        d.text((620, y + 14), comment, font=_font(13), fill=(60, 60, 60))
        y += row_h

    return img


def make_cover() -> Image.Image:
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (15, 40, 90))
    d   = ImageDraw.Draw(img)

    # gradient-like bands
    for y in range(H):
        r = int(15  + (30  - 15)  * y / H)
        g = int(40  + (70  - 40)  * y / H)
        b = int(90  + (140 - 90)  * y / H)
        d.line([(0, y), (W, y)], fill=(r, g, b))

    # accent bar
    d.rectangle([0, 0, 8, H], fill=(100, 180, 255))

    # logo area
    d.rectangle([40, 60, 340, 130], fill=(255, 255, 255, 0), outline=(100, 180, 255), width=2)
    d.text((56, 72), "STDWeb", font=_font(36, bold=True), fill=(100, 180, 255))

    # title
    d.text((40, 180), "Guide de photométrie",       font=_font(52, bold=True), fill=(255, 255, 255))
    d.text((40, 250), "avec soustraction de motif", font=_font(36),            fill=(160, 210, 255))

    # separator line
    d.rectangle([40, 320, W - 40, 323], fill=(100, 180, 255))

    # subtitle block
    d.text((40, 345), "Exemple réel",                      font=_font(18, bold=True), fill=(150, 200, 255))
    d.text((40, 378), f"Tâche #{TASK_PHOT} · SN2026fvx · filtre Gaia G · 2026-05-04", font=_font(20), fill=(220, 235, 255))
    d.text((40, 414), f"Soustraction : tâche #{TASK_SUB} · SN2026fvx · filtre G · 2026-05-07", font=_font(20), fill=(220, 235, 255))

    # footer
    d.rectangle([0, H - 60, W, H], fill=(10, 25, 60))
    d.text((40, H - 42), "OBS-BZH / RAPAS  ·  Généré automatiquement depuis la base STDWeb",
           font=_font(15), fill=(120, 160, 210))
    d.text((W - 220, H - 42), "Mai 2026", font=_font(15), fill=(120, 160, 210))

    return img


def make_toc() -> Image.Image:
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (250, 251, 255))
    d   = ImageDraw.Draw(img)

    # header
    d.rectangle([0, 0, W, 80], fill=(15, 40, 90))
    d.text((40, 20), "Sommaire", font=_font(36, bold=True), fill=(255, 255, 255))

    sections = [
        ("A", "Démarrage",
         [("A1", "Connexion à STDWeb",             "4"),
          ("A2", "Liste des tâches",                "5"),
          ("A3", "Téléversement d'une image FITS",  "6")]),
        ("B", "Photométrie directe",
         [("B1", "Paramétrage de l'inspection initiale",  "7"),
          ("B2", "Résultat de l'inspection — image annotée", "8"),
          ("B3", "Formulaire de photométrie",              "9"),
          ("B4", "Résultat — magnitude calibrée",          "10"),
          ("B5", "Courbe de calibration photométrique",    "11"),
          ("B6", "Export des résultats (VOTable / CSV)",   "12")]),
        ("C", "Soustraction de motif",
         [("C1", "Vue d'ensemble — tâche prête",           "13"),
          ("C2", "Photométrie directe sur la même tâche",  "14"),
          ("C3", "Formulaire Template subtraction",        "15"),
          ("C4", "Résultat — image différence et magnitude","16")]),
        ("D", "Référence des options",
         [("D1", "Description détaillée de chaque option", "17"),
          ("D2", "Tableau récapitulatif — quand cocher ?", "18")]),
    ]

    colors = {"A": (30, 80, 140), "B": (20, 110, 60), "C": (130, 50, 20), "D": (80, 40, 120)}

    y = 100
    for sec, title, items in sections:
        col = colors[sec]
        # section header
        d.rectangle([30, y, W - 30, y + 36], fill=col)
        d.text((44, y + 8), f"Section {sec}  —  {title}", font=_font(18, bold=True), fill=(255, 255, 255))
        y += 42
        for code, label, page in items:
            # dotted leader
            d.text((60, y + 4), f"{code}", font=_font(14, bold=True), fill=col)
            d.text((100, y + 4), label, font=_font(14), fill=(40, 40, 60))
            # dots
            for xd in range(850, W - 80, 8):
                d.ellipse([xd, y + 12, xd + 2, y + 14], fill=(180, 180, 200))
            d.text((W - 70, y + 4), f"p. {page}", font=_font(14, bold=True), fill=(40, 40, 80))
            y += 30
        y += 8

    return img


def make_flowchart() -> Image.Image:
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (245, 246, 252))
    d   = ImageDraw.Draw(img)

    # header
    d.rectangle([0, 0, W, 72], fill=(15, 40, 90))
    d.text((40, 16), "Flux de travail — les 4 étapes", font=_font(32, bold=True), fill=(255, 255, 255))

    # steps definition
    steps = [
        {"num": "1", "title": "Upload",
         "color": (30, 80, 140),
         "lines": ["Téléversez votre image FITS",
                   "Renseignez le gain (e⁻/ADU)",
                   "et le niveau de saturation"]},
        {"num": "2", "title": "Inspection",
         "color": (20, 110, 60),
         "lines": ["Entrez le nom de la cible",
                   "STDWeb détecte les étoiles,",
                   "mesure FWHM et fond de ciel"]},
        {"num": "3", "title": "Photométrie",
         "color": (130, 90, 10),
         "lines": ["Choisissez catalogue et filtre",
                   "STDWeb calibre et mesure",
                   "la magnitude de la cible"]},
        {"num": "4", "title": "Soustraction\n(si besoin)",
         "color": (130, 40, 20),
         "lines": ["Cible dans une galaxie hôte ?",
                   "Soustrayez le motif de fond",
                   "avec Pan-STARRS ou ZTF"]},
    ]

    box_w, box_h = 240, 200
    gap          = 40
    total_w      = len(steps) * box_w + (len(steps) - 1) * gap
    x0           = (W - total_w) // 2
    y0           = 130

    for i, s in enumerate(steps):
        bx = x0 + i * (box_w + gap)
        col = s["color"]
        light = tuple(min(255, c + 160) for c in col)

        # shadow
        d.rectangle([bx + 4, y0 + 4, bx + box_w + 4, y0 + box_h + 4], fill=(180, 180, 190))
        # box
        d.rectangle([bx, y0, bx + box_w, y0 + box_h], fill=light, outline=col, width=3)
        # top band
        d.rectangle([bx, y0, bx + box_w, y0 + 48], fill=col)
        # number circle
        d.ellipse([bx + 8, y0 + 8, bx + 40, y0 + 40], fill=(255, 255, 255))
        d.text((bx + 16, y0 + 11), s["num"], font=_font(18, bold=True), fill=col)
        # title
        title_lines = s["title"].split("\n")
        for ti, tl in enumerate(title_lines):
            d.text((bx + 50, y0 + 10 + ti * 22), tl, font=_font(18, bold=True), fill=(255, 255, 255))
        # body text
        for j, line in enumerate(s["lines"]):
            d.text((bx + 14, y0 + 60 + j * 28), line, font=_font(13), fill=(30, 30, 50))

        # arrow to next box
        if i < len(steps) - 1:
            ax = bx + box_w + 4
            ay = y0 + box_h // 2
            d.line([(ax, ay), (ax + gap - 8, ay)], fill=(80, 80, 120), width=3)
            # arrowhead
            d.polygon([(ax + gap - 8, ay - 8), (ax + gap - 8, ay + 8), (ax + gap + 2, ay)],
                      fill=(80, 80, 120))

    # optional badge for step 4
    bx4 = x0 + 3 * (box_w + gap)
    d.rectangle([bx4 + 6, y0 + box_h + 12, bx4 + box_w - 6, y0 + box_h + 44],
                fill=(220, 80, 50), outline=(160, 40, 10), width=2)
    d.text((bx4 + 14, y0 + box_h + 18), "Optionnel — cible dans galaxie",
           font=_font(13, bold=True), fill=(255, 255, 255))

    # bottom note
    note = ("Le workflow est séquentiel : chaque étape s'appuie sur la précédente. "
            "La soustraction de motif (étape 4) est réservée aux cibles situées dans une galaxie hôte "
            "afin d'éliminer la contamination de fond.")
    for i, line in enumerate(textwrap.wrap(note, 100)):
        d.text((40, 420 + i * 26), line, font=_font(15), fill=(60, 60, 80))

    # legend
    d.rectangle([40, H - 100, W - 40, H - 20], fill=(230, 232, 245), outline=(160, 170, 210))
    d.text((60, H - 88), "Outils STDWeb utilisés :", font=_font(14, bold=True), fill=(40, 40, 90))
    legend = [
        ((30, 80, 140),  "Étape 1 : Upload FITS"),
        ((20, 110, 60),  "Étape 2 : Initial inspection & masking"),
        ((130, 90, 10),  "Étape 3 : Photometry (Gaia EDR3 / PS1)"),
        ((130, 40, 20),  "Étape 4 : Template subtraction (HOTPANTS)"),
    ]
    lx = 60
    for col, label in legend:
        d.rectangle([lx, H - 55, lx + 18, H - 37], fill=col)
        d.text((lx + 26, H - 56), label, font=_font(13), fill=(40, 40, 60))
        lx += 280

    return img


def build_pdf():
    PDF_PATH = "/home/pyl/repo/stdweb/STDWeb_photometry_tutorial.pdf"

    pages = []

    # ── Page 1 : Couverture ──────────────────────────────────────────────────
    pages.append(make_cover())

    # ── Page 2 : Sommaire ────────────────────────────────────────────────────
    pages.append(make_toc())

    # ── Page 3 : Diagramme en blocs ──────────────────────────────────────────
    pages.append(make_flowchart())

    # ── Pages 4+ : Screenshots annotés ──────────────────────────────────────
    prev_section = None
    step_num = 0
    for step in STEPS:
        sec = step.get("section", "A")
        if sec != prev_section:
            pages.append(make_section_divider(sec))
            prev_section = sec
        step_num += 1
        img  = Image.open(step["path"]).convert("RGB")
        page = add_caption(img, step["label"], step["note"], step_num, sec,
                           bbox=step.get("bbox"), action=step.get("action"))
        pages.append(page)

    # ── Section D : Référence des options ────────────────────────────────────
    pages.append(make_section_divider("D"))
    pages.append(make_options_page())
    pages.append(make_recommendation_page())

    first = pages[0]
    rest  = pages[1:]
    first.save(PDF_PATH, save_all=True, append_images=rest, format="PDF")
    print(f"\n✅ PDF saved: {PDF_PATH}  ({len(pages)} pages)")
    return PDF_PATH


async def main():
    await capture()
    build_pdf()


if __name__ == "__main__":
    asyncio.run(main())
