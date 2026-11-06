# Rendu des icones affichees sur les touches du Stream Deck (voir
# streamdeck_controller.py). Dessinees directement avec PIL (formes
# vectorielles simples + texte), PAS des fichiers image externes : evite
# toute dependance a une connexion internet ou a une police d'icones tierce
# au moment de l'execution (coherent avec le reste du logiciel, compile en
# executable autonome via PyInstaller - voir PROJECT_CONTEXT.txt section 13).
#
# Couleurs alignees sur celles DEJA utilisees par l'interface web (voir
# scene_button.py SceneButton._refresh_appearance et interface.py
# build_footer) pour rester visuellement coherent entre le Stream Deck et
# le navigateur : idle=gris ardoise, preview=jaune, programme=rouge, et les
# memes teintes que les boutons du footer (orange/jaune/vert/bleu) pour les
# actions correspondantes.
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

KEY_SIZE = 96

#memes teintes que les classes Tailwind utilisees par l'interface web (voir
#docstring plus haut) - permet de reconnaitre un etat au premier coup d'oeil
#en passant du navigateur au Stream Deck ou inversement
COLOR_DISABLED = (30, 41, 59)
COLOR_ORANGE = (234, 88, 12)
COLOR_YELLOW = (202, 138, 4)
COLOR_GREEN = (22, 163, 74)
COLOR_BLUE = (37, 99, 235)
COLOR_PURPLE = (147, 51, 234)
COLOR_RED = (220, 38, 38)
COLOR_SLATE = (51, 65, 85)
COLOR_BLACK = (15, 23, 42)
WHITE = (255, 255, 255)

#police EN GRAS deja presente sur tout Windows (aucun fichier a embarquer) -
#utilisee a plusieurs tailles selon le contenu (gros chiffre plein cadre vs
#libelle de plusieurs mots)
_FONT_PATH = Path(r"C:\Windows\Fonts\arialbd.ttf")


def _font(size: int) -> ImageFont.FreeTypeFont:
    if _FONT_PATH.exists():
        return ImageFont.truetype(str(_FONT_PATH), size)
    return ImageFont.load_default()


def _blank(bg: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", (KEY_SIZE, KEY_SIZE), bg)


#touche INUTILISEE de la disposition (voir streamdeck_controller.
#BLANK_KEYS) - simple fond noir, aucun texte
def render_empty() -> Image.Image:
    return _blank(COLOR_BLACK)


#pave numerique (chiffres 0-9) : gros chiffre centre, fond neutre - la
#touche EFFACER/VALIDER du meme bloc a sa propre fonction plus bas
def render_digit(digit: int) -> Image.Image:
    image = _blank(COLOR_SLATE)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.text((KEY_SIZE / 2, KEY_SIZE / 2), str(digit), font=_font(44), fill=WHITE, anchor="mm")
    return image


#efface le buffer de chiffres SAISIS (pas la valeur cible deja validee -
#voir render_clear_target) - croix + libelle
def render_clear_entry() -> Image.Image:
    image = _blank(COLOR_ORANGE)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.line([(30, 26), (52, 48)], fill=WHITE, width=6)
    draw.line([(52, 26), (30, 48)], fill=WHITE, width=6)
    draw.text((KEY_SIZE / 2, 68), "EFFACER", font=_font(13), fill=WHITE, anchor="mm")
    return image


#valide le buffer courant (dossard ou cible selon le mode actif) - coche
def render_validate() -> Image.Image:
    image = _blank(COLOR_GREEN)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.line([(26, 32), (42, 48)], fill=WHITE, width=7)
    draw.line([(42, 48), (68, 20)], fill=WHITE, width=7)
    draw.text((KEY_SIZE / 2, 68), "VALIDER", font=_font(13), fill=WHITE, anchor="mm")
    return image


#bascule le pave numerique en mode "saisie d'un dossard" - silhouette
#simplifiee + #. Sert AUSSI d'ecran (aucune touche dediee disponible sur
#les 32 - budget entierement alloue, voir streamdeck_controller.py) :
#pending affiche la derniere valeur VALIDEE (persistante), typing_buffer
#le nombre EN COURS DE SAISIE (prioritaire sur pending tant qu'on tape)
def render_mode_bib(active: bool, pending: str | None = None, typing_buffer: str = "") -> Image.Image:
    image = _blank(COLOR_BLUE if not active else WHITE)
    draw = ImageDraw.Draw(image)
    text_color = WHITE if not active else COLOR_BLUE
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)

    display_value = typing_buffer or pending
    if display_value:
        draw.text((KEY_SIZE / 2, 30), display_value, font=_font(26), fill=text_color, anchor="mm")
    else:
        draw.ellipse([38, 12, 58, 32], outline=text_color, width=4)
        draw.arc([26, 32, 70, 68], start=200, end=340, fill=text_color, width=4)

    draw.text((KEY_SIZE / 2, 80), "DOSSARD", font=_font(12), fill=text_color, anchor="mm")
    return image


#bascule le pave numerique en mode "saisie d'une cible" - cible/bullseye,
#meme principe d'ecran integre que render_mode_bib ci-dessus
def render_mode_target(active: bool, pending: int | None = None, typing_buffer: str = "") -> Image.Image:
    image = _blank(COLOR_PURPLE if not active else WHITE)
    draw = ImageDraw.Draw(image)
    text_color = WHITE if not active else COLOR_PURPLE
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)

    display_value = typing_buffer or (str(pending) if pending is not None else "")
    if display_value:
        draw.text((KEY_SIZE / 2, 30), display_value, font=_font(26), fill=text_color, anchor="mm")
    else:
        center = (KEY_SIZE / 2, 30)
        for radius in (18, 11, 5):
            draw.ellipse(
                [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
                outline=text_color,
                width=3,
            )

    draw.text((KEY_SIZE / 2, 80), "CIBLE", font=_font(13), fill=text_color, anchor="mm")
    return image


#efface DIRECTEMENT la valeur cible deja validee d'un bouton (sans repasser
#par une saisie au clavier) - cible barree, exigence explicite de
#l'utilisateur : "peut tu aussi mettre un bouton pour clear la valeure de
#la cible"
def render_clear_target() -> Image.Image:
    image = _blank(COLOR_RED)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    center = (KEY_SIZE / 2, 38)
    for radius in (22, 14, 6):
        draw.ellipse(
            [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius],
            outline=WHITE,
            width=3,
        )
    draw.line([(center[0] - 24, center[1] - 24), (center[0] + 24, center[1] + 24)], fill=WHITE, width=5)
    draw.text((KEY_SIZE / 2, 78), "CLEAR CIBLE", font=_font(11), fill=WHITE, anchor="mm")
    return image


#navigue les pages du DERNIER graphique actif (SceneButton._last_active,
#voir streamdeck_controller.py) - kind: "first"/"prev"/"next". Grisee/
#inactive si aucune cible valide (label=None) : evite de laisser croire
#que la touche fait quelque chose alors qu'aucun graphique multipage_on
#n'a encore ete joue/pris depuis l'interface web.
def render_page_nav(kind: str, label: str | None, page: int | None) -> Image.Image:
    active = label is not None
    image = _blank(COLOR_SLATE if active else COLOR_DISABLED)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    color = WHITE if active else (100, 116, 139)

    if kind == "first":
        draw.rectangle([26, 18, 30, 42], fill=color)
        draw.polygon([(50, 18), (50, 42), (32, 30)], fill=color)
        title = "PAGE 1"
    elif kind == "prev":
        draw.polygon([(56, 18), (56, 42), (34, 30)], fill=color)
        title = "PRECEDENTE"
    else:
        draw.polygon([(40, 18), (40, 42), (62, 30)], fill=color)
        title = "SUIVANTE"

    draw.text((KEY_SIZE / 2, 54), title, font=_font(11), fill=color, anchor="mm")
    if active:
        draw.text((KEY_SIZE / 2, 72), label[:14], font=_font(9), fill=color, anchor="mm")
        draw.text((KEY_SIZE / 2, 86), f"page {page}", font=_font(9), fill=color, anchor="mm")
    return image


#4 actions du footer web (voir interface.build_footer) - memes teintes,
#meme intitule, pour rester immediatement reconnaissable
def render_all_out() -> Image.Image:
    image = _blank(COLOR_ORANGE)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.ellipse([26, 14, 70, 58], outline=WHITE, width=5)
    draw.rectangle([44, 22, 52, 44], fill=WHITE)
    draw.ellipse([44, 46, 52, 54], fill=WHITE)
    draw.text((KEY_SIZE / 2, 76), "ALL OUT", font=_font(14), fill=WHITE, anchor="mm")
    return image


def render_clear_prw() -> Image.Image:
    image = _blank(COLOR_YELLOW)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.rectangle([26, 16, 70, 32], outline=WHITE, width=3)
    draw.rectangle([26, 36, 70, 52], outline=WHITE, width=3)
    draw.line([(22, 12), (74, 58)], fill=WHITE, width=5)
    draw.text((KEY_SIZE / 2, 76), "CLEAR PRW", font=_font(12), fill=WHITE, anchor="mm")
    return image


def render_take_preview() -> Image.Image:
    image = _blank(COLOR_GREEN)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.polygon([(48, 12), (30, 38), (42, 38), (42, 56), (54, 56), (54, 38), (66, 38)], fill=WHITE)
    draw.text((KEY_SIZE / 2, 76), "TAKE PRW", font=_font(13), fill=WHITE, anchor="mm")
    return image


def render_swap() -> Image.Image:
    image = _blank(COLOR_BLUE)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.line([(38, 40), (38, 14)], fill=WHITE, width=5)
    draw.polygon([(30, 20), (46, 20), (38, 8)], fill=WHITE)
    draw.line([(58, 30), (58, 56)], fill=WHITE, width=5)
    draw.polygon([(50, 50), (66, 50), (58, 62)], fill=WHITE)
    draw.text((KEY_SIZE / 2, 78), "SWAP", font=_font(15), fill=WHITE, anchor="mm")
    return image


#5eme action du footer web (voir interface.build_footer) - invoque
#"sponsor_in" (CG INVOKE) sur tous les graphiques a l'antenne, meme teinte
#violette et meme forme de tag/etiquette que le bouton web (icone
#"loyalty")
def render_sponsor_in() -> Image.Image:
    image = _blank(COLOR_PURPLE)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, KEY_SIZE - 1, KEY_SIZE - 1], outline=COLOR_BLACK, width=2)
    draw.polygon([(24, 22), (48, 22), (68, 42), (48, 62), (24, 62)], outline=WHITE, width=3)
    draw.ellipse([32, 34, 42, 44], fill=WHITE)
    draw.text((KEY_SIZE / 2, 80), "SPONSOR IN", font=_font(11), fill=WHITE, anchor="mm")
    return image
