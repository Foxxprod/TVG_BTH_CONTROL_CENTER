# Integration Stream Deck (Elgato Stream Deck XL, 8 colonnes x 4 lignes =
# 32 touches - voir streamdeck_hid.py pour le backend materiel).
#
# Le Stream Deck NE LANCE AUCUN GRAPHIQUE (exigence explicite de
# l'utilisateur, apres une V1 qui le permettait : "je ne veux pas les
# lancer avec le stream deck") - il sert uniquement a :
#   1) taper un dossard/une cible dans une boite de dialogue DEJA OUVERTE
#      a la souris depuis l'interface web (_ask_bib/_ask_target) ;
#   2) a defaut de boite ouverte, mettre a jour EN DIRECT le dossard/la
#      cible du DERNIER graphique joue/pris DEPUIS L'INTERFACE WEB (voir
#      SceneButton._last_active), qu'il soit en preview ou a l'antenne ;
#   3) tourner les pages de ce meme dernier graphique, s'il est
#      multipage_on ;
#   4) les actions globales du footer (ALL OUT/CLEAR PRW/TAKE PREVIEW/
#      SWAP/SPONSOR IN), identiques a celles de l'interface web.
#
# DISPOSITION DES 32 TOUCHES (row-major, index = ligne*8 + colonne) -
# le pave numerique est A DROITE, ses 2 boutons de mode + CLEAR CIBLE sont
# COLLES contre lui (colonne juste a gauche du pave), et le reste des
# actions (pagination + footer) est plus loin encore a gauche - exigence
# explicite de l'utilisateur : "met le pave a droite, je veux colle au
# pave les bouton relatif au pave et ensuite tu met a gauche le rest des
# bouton" :
#
#   Ligne 0 : [PAGE 1][<PREC][SUIV>][ . ][DOSSARD]     [ 7 ][ 8 ][ 9 ]
#   Ligne 1 : [  .   ][  .  ][  .  ][ . ][CIBLE]       [ 4 ][ 5 ][ 6 ]
#   Ligne 2 : [  .   ][  .  ][  .  ][ . ][CLR CIBLE]   [ 1 ][ 2 ][ 3 ]
#   Ligne 3 : [ALL OUT][CLR PRW][TAKE PRW][SWAP][SPONSOR IN][EFF][ 0 ][VAL]
#
# Le pave numerique (disposition clavier d'ordinateur - 7 8 9 en haut -
# exigence explicite d'une iteration precedente) occupe TOUJOURS les memes
# touches, que sa cible du moment soit une boite de dialogue ouverte ou le
# dernier graphique actif (voir _on_digit/_on_validate/_on_clear_entry : la
# boite de dialogue, si une est ouverte, est TOUJOURS prioritaire).
#
# ARCHITECTURE (voir aussi streamdeck_hid.py et scene_button.py) :
#   - Un seul StreamDeckController, demarre une fois au lancement du
#     logiciel (main.py, @app.on_startup) et jamais recree.
#   - set_key_callback_async() programme les callbacks sur LA MEME boucle
#     asyncio que le serveur NiceGUI, necessaire pour y appeler du code
#     asynchrone (caspar/SceneButton) en toute securite.
#   - PIEGE EVITE : ui.notify() (et tout ce qui touche `ui.context`) leve
#     une RuntimeError hors d'une tache rattachee a un client NiceGUI - une
#     touche physique n'est PAS un clic navigateur. Tout code touchant un
#     SceneButton passe donc par `with button.client:` (voir SceneButton.
#     client) pour re-entrer le contexte de son client proprietaire, en
#     plus d'un garde-fou try/except global (on_key_change) qui empeche
#     de toute facon qu'une erreur d'affichage n'interrompe la lecture des
#     touches physiques.
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from StreamDeck.ImageHelpers import PILHelper

import streamdeck_icons as icons
from scene_button import SceneButton
from streamdeck_hid import get_device_manager

if TYPE_CHECKING:
    from biathdata_ws import BiathDataWebsocket
    from casparcg import CasparCG

KEY_GOTO_PAGE1, KEY_PREV_PAGE, KEY_NEXT_PAGE = 0, 1, 2
KEY_MODE_BIB = 4
KEY_DIGIT_7, KEY_DIGIT_8, KEY_DIGIT_9 = 5, 6, 7

KEY_MODE_TARGET = 12
KEY_DIGIT_4, KEY_DIGIT_5, KEY_DIGIT_6 = 13, 14, 15

KEY_CLEAR_TARGET = 20
KEY_DIGIT_1, KEY_DIGIT_2, KEY_DIGIT_3 = 21, 22, 23

KEY_ALL_OUT, KEY_CLEAR_PRW, KEY_TAKE_PREVIEW, KEY_SWAP = 24, 25, 26, 27
KEY_SPONSOR_IN = 28
KEY_CLEAR_ENTRY, KEY_DIGIT_0, KEY_VALIDATE = 29, 30, 31

#disposition PAVE NUMERIQUE D'ORDINATEUR (7 8 9 en haut, 1 2 3 en bas) -
#PAS telephone/calculatrice - exigence explicite d'une iteration precedente
DIGIT_KEY_TO_VALUE: dict[int, int] = {
    KEY_DIGIT_7: 7, KEY_DIGIT_8: 8, KEY_DIGIT_9: 9,
    KEY_DIGIT_4: 4, KEY_DIGIT_5: 5, KEY_DIGIT_6: 6,
    KEY_DIGIT_1: 1, KEY_DIGIT_2: 2, KEY_DIGIT_3: 3,
    KEY_DIGIT_0: 0,
}

_USED_KEYS = {
    KEY_MODE_BIB, KEY_MODE_TARGET, KEY_CLEAR_TARGET,
    KEY_GOTO_PAGE1, KEY_PREV_PAGE, KEY_NEXT_PAGE,
    KEY_CLEAR_ENTRY, KEY_VALIDATE,
    KEY_ALL_OUT, KEY_CLEAR_PRW, KEY_TAKE_PREVIEW, KEY_SWAP, KEY_SPONSOR_IN,
    *DIGIT_KEY_TO_VALUE,
}
BLANK_KEYS = [key for key in range(32) if key not in _USED_KEYS]

MAX_BIB_DIGITS = 4
MAX_TARGET_DIGITS = 2


class StreamDeckController:
    def __init__(self, caspar: "CasparCG", data: "BiathDataWebsocket", deck) -> None:
        self.caspar = caspar
        self.data = data
        self.deck = deck

        self.numpad_mode: str | None = None  # None | "bib" | "target"
        self.numpad_buffer = ""
        #derniere valeur EFFECTIVEMENT appliquee (affichage seul, aucun
        #effet fonctionnel - contrairement a l'ancienne V1, aucune valeur
        #n'est jamais "en attente" pour un futur play : chaque validation
        #pousse IMMEDIATEMENT une mise a jour au graphique actif)
        self.last_bib_applied: str | None = None
        self.last_target_applied: int | None = None

    def _push(self, key: int, image) -> None:
        native = PILHelper.to_native_key_format(self.deck, image)
        self.deck.set_key_image(key, native)

    def render_all(self) -> None:
        self._render_numpad()
        self._render_page_nav()
        self._render_static()

    def _render_numpad(self) -> None:
        for key, digit in DIGIT_KEY_TO_VALUE.items():
            self._push(key, icons.render_digit(digit))
        self._push(KEY_CLEAR_ENTRY, icons.render_clear_entry())
        self._push(KEY_VALIDATE, icons.render_validate())
        self._push(
            KEY_MODE_BIB,
            icons.render_mode_bib(
                self.numpad_mode == "bib",
                pending=self.last_bib_applied,
                typing_buffer=self.numpad_buffer if self.numpad_mode == "bib" else "",
            ),
        )
        self._push(
            KEY_MODE_TARGET,
            icons.render_mode_target(
                self.numpad_mode == "target",
                pending=self.last_target_applied,
                typing_buffer=self.numpad_buffer if self.numpad_mode == "target" else "",
            ),
        )
        self._push(KEY_CLEAR_TARGET, icons.render_clear_target())

    #les 3 touches de pagination refletent le DERNIER graphique actif
    #(SceneButton._last_active) : grisees/inactives si aucun n'est encore
    #actif ou qu'il n'est pas multipage_on, sinon son libelle + sa page
    #courante y sont affiches (seule facon de savoir "sur quoi" on
    #navigue, aucun autre ecran disponible sur ces touches)
    def _render_page_nav(self) -> None:
        button = SceneButton._last_active
        active = button is not None and button.multipage_on
        label = button.label if active else None
        page = button._page if active else None

        self._push(KEY_GOTO_PAGE1, icons.render_page_nav("first", label, page))
        self._push(KEY_PREV_PAGE, icons.render_page_nav("prev", label, page))
        self._push(KEY_NEXT_PAGE, icons.render_page_nav("next", label, page))

    def _render_static(self) -> None:
        self._push(KEY_ALL_OUT, icons.render_all_out())
        self._push(KEY_CLEAR_PRW, icons.render_clear_prw())
        self._push(KEY_TAKE_PREVIEW, icons.render_take_preview())
        self._push(KEY_SWAP, icons.render_swap())
        self._push(KEY_SPONSOR_IN, icons.render_sponsor_in())
        for key in BLANK_KEYS:
            self._push(key, icons.render_empty())

    #callback UNIQUE branchee sur deck.set_key_callback_async (voir
    #main.py) - ne reagit qu'aux APPUIS (pressed=True), pas aux
    #relachements. Reaffiche TOUJOURS l'etat courant apres coup (simple et
    #robuste : pas besoin de determiner au cas par cas quelle touche
    #precise a besoin d'etre rafraichie).
    async def on_key_change(self, _deck, key: int, pressed: bool) -> None:
        if not pressed:
            return

        try:
            await self._dispatch(key)
        except Exception as exc:  # noqa: BLE001 - ne jamais interrompre la lecture des touches
            print(f"[StreamDeck] Erreur en traitant la touche {key} : {exc}")

        self.render_all()

    async def _dispatch(self, key: int) -> None:
        if key == KEY_MODE_BIB:
            self.numpad_mode = "bib"
            self.numpad_buffer = ""
        elif key == KEY_MODE_TARGET:
            self.numpad_mode = "target"
            self.numpad_buffer = ""
        elif key == KEY_CLEAR_TARGET:
            await self._on_clear_target_live()
        elif key == KEY_CLEAR_ENTRY:
            self._on_clear_entry()
        elif key == KEY_VALIDATE:
            await self._on_validate()
        elif key in DIGIT_KEY_TO_VALUE:
            self._on_digit(DIGIT_KEY_TO_VALUE[key])
        elif key == KEY_GOTO_PAGE1:
            await self._on_page_nav("first")
        elif key == KEY_PREV_PAGE:
            await self._on_page_nav("prev")
        elif key == KEY_NEXT_PAGE:
            await self._on_page_nav("next")
        elif key == KEY_ALL_OUT:
            await SceneButton.stop_all_pgm()
        elif key == KEY_CLEAR_PRW:
            await SceneButton.clear_all_previews()
        elif key == KEY_TAKE_PREVIEW:
            await SceneButton.take_all_previews()
        elif key == KEY_SWAP:
            await SceneButton.swap_preview_pgm()
        elif key == KEY_SPONSOR_IN:
            await SceneButton.invoke_all_pgm("sponsor_in")

    #un chiffre va TOUJOURS en priorite vers une boite de dialogue
    #actuellement ouverte a la souris (SceneButton._active_streamdeck_input,
    #voir _ask_bib/_ask_target) - exigence explicite : "quand j'ouvre une
    #boite de dialogue avec la souris je puisse rentrer avec le stream
    #deck une valeure". A defaut, accumule dans le buffer local (mode
    #DOSSARD/CIBLE), applique seulement a la validation (_on_validate).
    def _on_digit(self, digit: int) -> None:
        dialog_input = SceneButton._active_streamdeck_input
        if dialog_input is not None:
            dialog_input.append_digit(digit)
            return

        if self.numpad_mode is None:
            return

        max_digits = MAX_TARGET_DIGITS if self.numpad_mode == "target" else MAX_BIB_DIGITS
        if len(self.numpad_buffer) >= max_digits:
            return

        self.numpad_buffer += str(digit)

    def _on_clear_entry(self) -> None:
        dialog_input = SceneButton._active_streamdeck_input
        if dialog_input is not None:
            dialog_input.clear()
            return

        self.numpad_buffer = ""

    async def _on_validate(self) -> None:
        dialog_input = SceneButton._active_streamdeck_input
        if dialog_input is not None:
            dialog_input.submit()
            return

        await self._apply_to_last_active()

    #pousse le buffer courant (dossard ou cible selon le mode) EN DIRECT
    #sur SceneButton._last_active - le dernier graphique joue/pris depuis
    #l'interface web, qu'il soit en preview ou a l'antenne (voir sa
    #docstring dans scene_button.py). Exigence explicite : "je veux que le
    #bouton dossard et cible agissent sur la graphique qui est a
    #l'antenne, lance depuis l'interface web".
    async def _apply_to_last_active(self) -> None:
        if not self.numpad_buffer or self.numpad_mode is None:
            return

        button = SceneButton._last_active
        if button is None or button._state == "idle":
            print(
                "[StreamDeck] Aucun graphique actif (preview/programme) sur "
                "lequel appliquer cette valeur"
            )
            self.numpad_buffer = ""
            return

        value = self.numpad_buffer
        self.numpad_buffer = ""

        with button.client:
            if self.numpad_mode == "bib":
                if not button.bib_on:
                    print(f"[StreamDeck] '{button.label}' n'a pas de champ dossard (bib_on)")
                    return
                button._bib = value
                self.last_bib_applied = value
            else:
                if not button.target_on:
                    print(f"[StreamDeck] '{button.label}' n'a pas de champ cible (target_on)")
                    return
                try:
                    target_value = max(1, min(30, int(value)))
                except ValueError:
                    return
                button._target = target_value
                self.last_target_applied = target_value

            await button._update_data()
            button._refresh_appearance()

    #efface DIRECTEMENT la cible du dernier graphique actif (pas besoin de
    #retaper - demande explicite : "peut tu aussi mettre un bouton pour
    #clear la valeure de la cible")
    async def _on_clear_target_live(self) -> None:
        button = SceneButton._last_active
        if button is None or button._state == "idle" or not button.target_on:
            return

        with button.client:
            button._target = None
            self.last_target_applied = None
            await button._update_data()
            button._refresh_appearance()

    #tourne les pages du dernier graphique actif, s'il est multipage_on -
    #reutilise EXACTEMENT les memes methodes que les fleches de
    #l'interface web (_on_next_page/_on_prev_page/_goto_first_page, voir
    #scene_button.py), aucune reimplementation. Fonctionne QUEL QUE SOIT
    #l'etat (idle/preview/programme), comme ces fleches sur le web.
    async def _on_page_nav(self, kind: str) -> None:
        button = SceneButton._last_active
        if button is None or not button.multipage_on:
            return

        with button.client:
            if kind == "first":
                await button._goto_first_page()
            elif kind == "prev":
                await button._on_prev_page()
            else:
                await button._on_next_page()


#demarre le Stream Deck (enumeration + ouverture + callback + premier
#rendu) - appele depuis main.py au demarrage du logiciel (@app.on_startup).
#Ne leve JAMAIS d'exception : l'absence de Stream Deck branche (ou d'un
#backend HID fonctionnel, voir streamdeck_hid.py) est un cas normal, pas
#une erreur fatale - le reste du logiciel doit demarrer normalement sans.
def start_streamdeck(caspar: "CasparCG", data: "BiathDataWebsocket") -> StreamDeckController | None:
    try:
        manager = get_device_manager()
        decks = manager.enumerate()
    except Exception as exc:  # noqa: BLE001
        print(f"Stream Deck : aucun backend HID disponible ({exc})")
        return None

    if not decks:
        print("Stream Deck : aucun peripherique detecte")
        return None

    deck = decks[0]
    deck.open()
    deck.reset()
    deck.set_brightness(60)

    controller = StreamDeckController(caspar, data, deck)
    controller.render_all()

    loop = asyncio.get_event_loop()
    deck.set_key_callback_async(controller.on_key_change, loop=loop)

    #rafraichit l'affichage (dossard/cible en cours, pagination du dernier
    #graphique actif) a chaque nouveau message websocket complet - memes
    #mecanisme et justification que partout ailleurs dans ce logiciel
    #(BiathDataWebsocket.on_message, SceneButton._subscribe_to_new_data) :
    #ce controleur vit pour toute la duree du processus, pas besoin de
    #jamais se desabonner (contrairement a un SceneButton lie a un onglet).
    data.on_message(controller.render_all)

    print(f"Stream Deck '{deck.deck_type()}' connecte ({deck.key_count()} touches)")
    return controller
