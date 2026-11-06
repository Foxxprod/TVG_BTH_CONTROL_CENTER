from collections import defaultdict
from datetime import datetime
from typing import Callable

from nicegui import ui

from app_config import normalize_category_groups, save_config
from biathdata_ws import BiathDataWebsocket
from buttons_config import (
    load_button_configs,
    load_column_titles,
    load_raw_configs,
    load_restrict_controls,
    save_button_configs,
)
from casparcg import CasparCG
from paths import resource_path
from race_progress import AthleteProgress, compute_race_progress, format_elapsed
from scene_button import SceneButton

#ressource EN LECTURE SEULE embarquee dans l'executable en mode installe
#(voir paths.py) - jamais modifiee par le logiciel, contrairement a
#templates.json/config.json
LOGO_PATH = resource_path("assets", "foxxprod_logo.png")

#bouton TEST PATTERN du header (demande explicite de l'utilisateur) :
#layer video DEDIE (900), distinct de tous les layers utilises par les
#boutons de templates.json (10 a 30 actuellement) pour ne jamais entrer
#en conflit avec un vrai graphique de course - purement un outil de
#calibration technique, pas un bouton de la grille.
TEST_PATTERN_LAYER = 900
TEST_PATTERN_TEMPLATE = "test_pattern"

TEST_PATTERN_IDLE_CLASSES = (
    "border border-slate-600 bg-slate-700/40 hover:bg-slate-700/60 "
    "text-slate-300 font-bold rounded-full px-4"
)
#anime (animate-pulse) tant que le test pattern est a l'antenne - exigence
#explicite : "je veux que le bouton reste enclenche et clignote"
TEST_PATTERN_ACTIVE_CLASSES = (
    "border border-orange-500 bg-orange-600/40 hover:bg-orange-600/60 "
    "text-orange-300 font-bold rounded-full px-4 animate-pulse"
)

#etat "enclenche" du bouton TEST PATTERN, EN DEHORS de build_header (qui
#s'execute a nouveau a chaque chargement de page, voir main.py) pour
#survivre a un rechargement : si l'operateur recharge la page pendant que
#le test pattern est reellement toujours a l'antenne sur CasparCG, le
#bouton doit rester affiche "enclenche" plutot que de revenir a l'etat
#inactif par defaut - memes raisons que SceneButton._active_pgm/
#_active_preview (scene_button.py), qui suivent le meme principe pour
#chaque bouton de la grille.
_test_pattern_active = {"value": False}

#bouton LOGO TICKER du header (demande explicite de l'utilisateur) : meme
#principe que TEST PATTERN (layer dedie distinct de templates.json, etat
#"enclenche" persistant hors build_header) mais plus simple - un seul
#channel (programme uniquement, le bandeau est fait pour rester visible a
#l'antenne, pas pour etre prepare en preview), et SANS confirmation avant
#envoi (contrairement a TEST PATTERN : un bandeau logo n'a pas le meme
#impact/besoin de double-verification qu'un mire de calibration).
LOGO_TICKER_LAYER = 901
LOGO_TICKER_TEMPLATE = "bx_ticker"

LOGO_TICKER_IDLE_CLASSES = (
    "border border-slate-600 bg-slate-700/40 hover:bg-slate-700/60 "
    "text-slate-300 font-bold rounded-full px-4"
)
LOGO_TICKER_ACTIVE_CLASSES = (
    "border border-cyan-500 bg-cyan-600/40 hover:bg-cyan-600/60 "
    "text-cyan-300 font-bold rounded-full px-4 animate-pulse"
)

_logo_ticker_active = {"value": False}


#calcule le decompte/chrono de course a partir de STARTHOUR (avant/apres le depart)
def format_race_clock(data: BiathDataWebsocket) -> str:
    if data.event_infos is None:
        return "--:--"

    start_hour = getattr(data.event_infos, "STARTHOUR", None)
    if not start_hour:
        return "--:--"

    try:
        hour, minute = (int(part) for part in start_hour.split(":"))
    except (ValueError, AttributeError):
        return "--:--"

    now = datetime.now()
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta_seconds = int((now - start).total_seconds())

    sign = "-" if delta_seconds < 0 else ""
    delta_seconds = abs(delta_seconds)

    hours, remainder = divmod(delta_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{sign}{minutes:02d}:{seconds:02d}"


#cree une case de statut divisee en 2 moities egales : titre en haut, statut
#en bas (statut sans marge, remplit toute sa moitie). Collee a ses voisines
#(bordure gauche), hauteur pleine, jamais sur 2 lignes. cursor-help + tooltip
#(texte rempli par _apply_status) : demande explicite de l'utilisateur -
#voir l'IP (et le ping, si dispo) au survol, sans devoir ouvrir Parametres.
def _build_status_box(title: str) -> tuple[ui.row, ui.icon, ui.label, ui.tooltip]:
    with ui.column().classes(
        "h-full w-32 justify-between border-l border-slate-700 gap-0"
    ):
        with ui.row().classes("flex-1 w-full items-center justify-center px-3"):
            ui.label(title).classes(
                "text-sm font-bold tracking-wide text-slate-300 whitespace-nowrap"
            )

        with ui.row().classes(
            replace="flex-1 w-full flex items-center justify-center gap-1 flex-nowrap cursor-help"
        ) as status_row:
            icon = ui.icon("wifi_off").classes("text-base")
            status_label = ui.label("DECONNECTE").classes("text-xs font-bold whitespace-nowrap")
            tooltip = ui.tooltip("").classes("bg-slate-700 text-white font-bold")

    return status_row, icon, status_label, tooltip


#cree une case horloge (label en haut, heure en gros en dessous), fond blanc
def _build_clock_box(title: str, border_left: bool) -> ui.label:
    border_classes = "border-l border-slate-300 " if border_left else ""
    with ui.column().classes(
        f"h-full justify-center items-center {border_classes}px-4 gap-0"
    ):
        ui.label(title).classes(
            "text-[10px] font-bold tracking-wide text-slate-500 whitespace-nowrap"
        )
        value_label = ui.label().classes(
            "text-2xl font-mono font-bold leading-none text-slate-900 whitespace-nowrap"
        )

    return value_label


#applique la couleur/icone/texte d'une case de statut selon l'etat connecte
#fond legerement transparent (comme le bouton emergency all out), clignote
#lentement quand deconnecte. `detail` (IP:port + ping si disponible, voir
#build_header/update_header) alimente le tooltip au survol.
def _apply_status(
    row: ui.row, icon: ui.icon, label: ui.label, tooltip: ui.tooltip, connected: bool, detail: str
) -> None:
    base = "flex-1 w-full flex items-center justify-center gap-1 flex-nowrap cursor-help "

    if connected:
        row.classes(replace=base + "bg-green-600/20 text-green-400")
    else:
        row.classes(replace=base + "bg-red-600/20 text-red-400 animate-pulse")

    icon.name = "wifi" if connected else "wifi_off"
    label.text = "CONNECTE" if connected else "DECONNECTE"
    tooltip.text = detail


#construit la barre d'outils en haut de la page. `footer_hooks` est un
#dict simple (cree une seule fois par chargement de page dans main.py,
#voir index()) partage avec build_footer, pour que la case a cocher
#"Afficher la progression de la course" ci-dessous puisse basculer en
#direct la visibilite de la section correspondante du footer - build_header
#s'execute AVANT build_footer, mais l'appel reel (clic sur "Enregistrer")
#n'arrive qu'apres que build_footer ait eu l'occasion de peupler ce dict.
def build_header(
    caspar: CasparCG, data: BiathDataWebsocket, config: dict, footer_hooks: dict
) -> None:
    #declenche l'emergency all out et notifie le resultat
    async def on_emergency_click() -> None:
        prw_response, pgm_response = await caspar.emergency_all_out()
        SceneButton.reset_all()

        #emergency_all_out() vide ENTIEREMENT les 2 channels (CLEAR sans
        #layer precis, donc TOUS les producers) - le bandeau LOGO TICKER
        #(layer dedie 901, HORS SceneButton, voir plus bas) y passe donc
        #aussi, contrairement au bouton ALL OUT du footer (SceneButton.
        #stop_all_pgm, qui ne touche que les boutons de la grille - le
        #ticker n'y est deja pas expose). Le rejouer immediatement s'il
        #etait actif restaure son affichage - exigence explicite : "le
        #bouton all out [ne doit pas] agir sur le ticker".
        if _logo_ticker_active["value"]:
            await caspar.play_template("pgm", LOGO_TICKER_TEMPLATE, layer=LOGO_TICKER_LAYER)

        if prw_response.is_error or pgm_response.is_error:
            details = "; ".join(
                response.message
                for response in (prw_response, pgm_response)
                if response.is_error
            )
            ui.notify(f"Erreur lors de l'emergency all out : {details}", type="negative")
        else:
            ui.notify("Emergency all out effectue", type="warning")

    #bouton TEST PATTERN (demande explicite de l'utilisateur) : au premier
    #clic, demande confirmation PUIS envoie le meme template test_patern
    #simultanement sur le preview ET le programme (avec un _label distinct
    #par channel, pour reperer lequel est lequel a l'ecran) ; au clic
    #suivant (bouton deja enclenche), reclique = clear direct des DEUX
    #channels, SANS confirmation cette fois - seul l'ENVOI initial du
    #graphique est juge assez consequent pour meriter une confirmation.
    async def on_test_pattern_click() -> None:
        if _test_pattern_active["value"]:
            prw_response = await caspar.clear("prw", layer=TEST_PATTERN_LAYER)
            pgm_response = await caspar.clear("pgm", layer=TEST_PATTERN_LAYER)

            _test_pattern_active["value"] = False
            test_pattern_button.classes(replace=TEST_PATTERN_IDLE_CLASSES)

            if prw_response.is_error or pgm_response.is_error:
                details = "; ".join(
                    response.message
                    for response in (prw_response, pgm_response)
                    if response.is_error
                )
                ui.notify(f"Erreur lors du clear du test pattern : {details}", type="negative")
            else:
                ui.notify("Test pattern retire (preview + programme)", type="positive")
            return

        with ui.dialog() as confirm_dialog, ui.card():
            ui.label("Envoyer le test pattern ?").classes("text-base font-bold")
            ui.label(
                "Le graphique test_patern sera envoye simultanement sur le "
                "preview et le programme."
            ).classes("text-sm text-slate-500")
            with ui.row().classes("justify-end gap-2 w-full mt-2"):
                ui.button("Annuler", on_click=lambda: confirm_dialog.submit(False))
                ui.button(
                    "Envoyer", color=None, on_click=lambda: confirm_dialog.submit(True)
                ).classes("bg-orange-600 text-white")

        if not await confirm_dialog:
            return

        prw_response = await caspar.play_template(
            "prw",
            TEST_PATTERN_TEMPLATE,
            data={"_label": "GFX PRW"},
            layer=TEST_PATTERN_LAYER,
        )
        pgm_response = await caspar.play_template(
            "pgm",
            TEST_PATTERN_TEMPLATE,
            data={"_label": "GFX PGM"},
            layer=TEST_PATTERN_LAYER,
        )

        if prw_response.is_error or pgm_response.is_error:
            details = "; ".join(
                response.message
                for response in (prw_response, pgm_response)
                if response.is_error
            )
            ui.notify(f"Erreur lors de l'envoi du test pattern : {details}", type="negative")
            return

        _test_pattern_active["value"] = True
        test_pattern_button.classes(replace=TEST_PATTERN_ACTIVE_CLASSES)
        ui.notify("Test pattern envoye (preview + programme)", type="positive")

    #bouton LOGO TICKER (demande explicite de l'utilisateur) : simple
    #bascule, directement au programme (voir constantes en tete de
    #fichier) - clic pour afficher le bandeau, reclic pour le retirer,
    #aucune confirmation (contrairement a TEST PATTERN).
    async def on_logo_ticker_click() -> None:
        if _logo_ticker_active["value"]:
            response = await caspar.clear("pgm", layer=LOGO_TICKER_LAYER)

            _logo_ticker_active["value"] = False
            logo_ticker_button.classes(replace=LOGO_TICKER_IDLE_CLASSES)

            if response.is_error:
                ui.notify(
                    f"Erreur lors du retrait du bandeau logo : {response.message}",
                    type="negative",
                )
            else:
                ui.notify("Bandeau logo retire", type="positive")
            return

        response = await caspar.play_template(
            "pgm", LOGO_TICKER_TEMPLATE, layer=LOGO_TICKER_LAYER
        )

        if response.is_error:
            ui.notify(
                f"Erreur lors de l'affichage du bandeau logo : {response.message}",
                type="negative",
            )
            return

        _logo_ticker_active["value"] = True
        logo_ticker_button.classes(replace=LOGO_TICKER_ACTIVE_CLASSES)
        ui.notify("Bandeau logo affiche", type="positive")

    #fond de la PAGE (pas du header, deja sombre en permanence) selon le
    #mode clair/sombre courant
    def apply_body_background() -> None:
        ui.query("body").classes(
            replace="bg-slate-900" if config["dark_mode"] else "bg-slate-50"
        )

    #enregistre les parametres modifies dans la boite de dialogue, les
    #sauvegarde dans config.json, puis les applique IMMEDIATEMENT (theme,
    #titre affiche, reconnexion CasparCG/WebSocket avec les nouvelles
    #valeurs) sans avoir besoin de redemarrer le logiciel
    async def on_settings_save() -> None:
        config["dark_mode"] = dark_switch.value
        config["software_title"] = title_input.value or config["software_title"]
        config["websocket_ip"] = ws_ip_input.value or config["websocket_ip"]
        config["websocket_port"] = (
            int(ws_port_input.value) if ws_port_input.value else config["websocket_port"]
        )
        config["caspar_ip"] = caspar_ip_input.value or config["caspar_ip"]
        config["caspar_port"] = (
            int(caspar_port_input.value) if caspar_port_input.value else config["caspar_port"]
        )
        config["caspar_channel_preview"] = (
            int(prw_channel_input.value)
            if prw_channel_input.value
            else config["caspar_channel_preview"]
        )
        config["caspar_channel_programme"] = (
            int(pgm_channel_input.value)
            if pgm_channel_input.value
            else config["caspar_channel_programme"]
        )
        config["race_progress_enabled"] = race_progress_switch.value
        #PAS de config["category_groups"] ici : contrairement au reste de
        #cette boite de dialogue, le regroupement des categories (glisser-
        #deposer, voir plus bas) s'applique et se persiste INSTANTANEMENT a
        #chaque deplacement, independamment du clic sur "Enregistrer" - une
        #manipulation directe n'a pas besoin d'un bouton de validation separe.

        save_config(config)

        dark_mode.set_value(config["dark_mode"])
        apply_body_background()
        SceneButton.set_dark_mode(config["dark_mode"])
        software_title_label.text = config["software_title"]
        if "set_race_progress_visible" in footer_hooks:
            footer_hooks["set_race_progress_visible"](config["race_progress_enabled"])

        ws_error = await data.change_ip(config["websocket_ip"], config["websocket_port"])
        caspar_error = await caspar.change_connection(
            config["caspar_ip"],
            config["caspar_port"],
            config["caspar_channel_preview"],
            config["caspar_channel_programme"],
        )

        settings_dialog.close()

        errors = [e for e in (ws_error, caspar_error) if e]
        if errors:
            ui.notify(
                f"Parametres enregistres, mais erreur de connexion : {'; '.join(errors)}",
                type="warning",
            )
        else:
            ui.notify("Parametres enregistres et appliques", type="positive")

    #element de controle plein ecran (Fullscreen API du navigateur, via le
    #plugin Quasar AppFullscreen) - invisible, un seul par page, pilote par
    #le bouton dedie du header (voir plus bas). bind_icon_from synchronise
    #l'icone du bouton sur fullscreen.value dans les DEUX sens de
    #declenchement : notre bouton, mais aussi une sortie du plein ecran
    #independante (touche Echap, F11 navigateur).
    fullscreen = ui.fullscreen()

    #boite de dialogue Donnees : affiche l'INTEGRALITE des donnees brutes
    #recues via le websocket (data.latest_data, tel que recu - pas les
    #SimpleNamespace assainies de BiathDataWebsocket, pour rester fidele a
    #ce qui est reellement recu), dans 3 onglets correspondant aux 3 cles
    #du message ("config"/"event_infos"/"timing_data"). ui.json_editor
    #donne une vue arborescente repliable/cherchable, bien plus lisible
    #qu'un bloc de texte JSON brut pour explorer les ~30 athletes de
    #timing_data. readOnly=True car purement consultatif - de toute facon
    #sans consequence si l'operateur editait localement, rien ne relit
    #cette copie pour la renvoyer quelque part. Rafraichie par le meme
    #ui.timer(1.0, update_header) que le reste du header (voir plus bas),
    #seulement pendant que la boite est ouverte (data_dialog.value) pour
    #ne pas envoyer inutilement de gros payloads au navigateur en continu.
    with ui.dialog() as data_dialog, ui.card().classes("gap-3 w-[52rem] max-w-[95vw]"):
        ui.label("Donnees recues (WebSocket)").classes("text-lg font-bold")

        with ui.tabs().classes("w-full") as data_tabs:
            config_tab = ui.tab("Config course")
            event_tab = ui.tab("Infos evenement")
            timing_tab = ui.tab("Timing data")

        with ui.tab_panels(data_tabs, value=config_tab).classes("w-full h-[65vh]"):
            with ui.tab_panel(config_tab).classes("h-full p-0"):
                config_editor = ui.json_editor(
                    {"content": {"json": {}}, "readOnly": True}
                ).classes("h-full")
            with ui.tab_panel(event_tab).classes("h-full p-0"):
                event_editor = ui.json_editor(
                    {"content": {"json": {}}, "readOnly": True}
                ).classes("h-full")
            with ui.tab_panel(timing_tab).classes("h-full p-0"):
                timing_editor = ui.json_editor(
                    {"content": {"json": {}}, "readOnly": True}
                ).classes("h-full")

        with ui.row().classes("justify-end w-full"):
            ui.button("Fermer", on_click=data_dialog.close)

    #pousse data.latest_data (tel que recu, voir plus haut) dans les 3
    #json_editor - "changer la propriete `properties` puis appeler
    #.update()" est le mecanisme documente par JsonEditor pour rafraichir
    #son contenu cote client
    def refresh_data_dialog() -> None:
        if not data_dialog.value:
            return

        latest = data.latest_data or {}
        for editor, section in (
            (config_editor, "config"),
            (event_editor, "event_infos"),
            (timing_editor, "timing_data"),
        ):
            editor.properties["content"] = {"json": latest.get(section, {})}
            editor.update()

    #boite de dialogue Parametres : theme, titre du logiciel, adresse/port
    #du serveur de donnees et de CasparCG, numeros de channel CasparCG.
    #Construite une seule fois (pas a chaque ouverture, contrairement aux
    #boites de dialogue ephemeres de scene_button.py) puisqu'elle vit dans
    #le header, present en permanence.
    with ui.dialog() as settings_dialog, ui.card().classes("gap-3 w-[28rem]"):
        ui.label("Parametres").classes("text-lg font-bold")

        dark_switch = ui.switch("Mode sombre", value=config["dark_mode"])

        #voir interface._build_race_progress_section (footer) - la case
        #elle-meme applique/persiste au clic sur "Enregistrer" comme le
        #reste de cette boite de dialogue (contrairement au regroupement
        #des categories, une simple case a cocher n'a pas besoin
        #d'application instantanee sans validation)
        race_progress_switch = ui.switch(
            "Afficher la progression de la course", value=config["race_progress_enabled"]
        )

        title_input = ui.input("Titre du logiciel", value=config["software_title"]).classes(
            "w-full"
        )

        ui.label("Serveur de donnees (WebSocket)").classes(
            "text-xs font-bold uppercase tracking-wide text-slate-500 mt-2"
        )
        with ui.row().classes("gap-2 w-full"):
            ws_ip_input = ui.input("IP", value=config["websocket_ip"]).classes("flex-1")
            ws_port_input = ui.number(
                "Port", value=config["websocket_port"], min=1, max=65535, step=1
            ).classes("w-28")

        ui.label("CasparCG").classes(
            "text-xs font-bold uppercase tracking-wide text-slate-500 mt-2"
        )
        with ui.row().classes("gap-2 w-full"):
            caspar_ip_input = ui.input("IP", value=config["caspar_ip"]).classes("flex-1")
            caspar_port_input = ui.number(
                "Port", value=config["caspar_port"], min=1, max=65535, step=1
            ).classes("w-28")
        with ui.row().classes("gap-2 w-full"):
            prw_channel_input = ui.number(
                "Channel preview", value=config["caspar_channel_preview"], min=1, step=1
            ).classes("flex-1")
            pgm_channel_input = ui.number(
                "Channel programme", value=config["caspar_channel_programme"], min=1, step=1
            ).classes("flex-1")

        ui.button(
            "Regroupement des categories...",
            icon="workspaces",
            on_click=lambda: open_category_groups_panel(),
        ).props("flat dense").classes("self-start text-sm mt-2")

        with ui.row().classes("justify-end gap-2 w-full mt-2"):
            ui.button("Annuler", on_click=settings_dialog.close)
            ui.button("Enregistrer", on_click=on_settings_save).classes(
                "bg-slate-800 text-white"
            )

    #regroupement des categories (SceneButton._category_groups, voir
    #scene_button.py) : une "boite" par groupe, chacune contenant les
    #puces (ui.chip) des categories qui en font partie. Glisser une puce
    #d'une boite a une autre les regroupe/degroupe - AUTANT de categories
    #que voulu dans un meme groupe (pas limite a des paires), exigence
    #explicite de l'utilisateur. S'applique et se persiste INSTANTANEMENT
    #a chaque deplacement (voir on_category_group_sort_end), pas besoin de
    #cliquer "Enregistrer".
    #
    #PIEGE MAJEUR REELLEMENT RENCONTRE ET CORRIGE (investigation
    #approfondie, voir PROJECT_CONTEXT.txt) : element.make_sortable()
    #(SortableJS) NE FONCTIONNE PAS a l'interieur d'un ui.dialog() NI d'un
    #ui.menu() - verifie avec de vrais evenements souris simules via le
    #protocole DevTools de Chrome (donc pas un probleme de "vrai humain vs
    #simulation" : Input.dispatchMouseEvent produit des evenements
    #"trusted" indiscernables d'une vraie souris). Aucune erreur JS,
    #Sortable.create() reussit bien cote client, mais le drag ne se
    #declenche jamais - meme en repoussant make_sortable() a la premiere
    #ouverture confirmee (avec un delai supplementaire de 300ms), meme
    #avec l'option forceFallback (mode de secours sans API HTML5 native),
    #meme avec le prop Quasar "seamless" (retire le comportement modal).
    #Le POINT COMMUN entre ui.dialog()/ui.menu() (tous deux casses) et une
    #simple ui.column basculee par .visible (qui fonctionne parfaitement,
    #verifie aussi par le meme protocole) : les deux premiers sont rendus
    #via le <Teleport> de Vue (deplaces ailleurs dans le DOM que leur
    #position logique dans l'arbre de composants), pas la troisieme -
    #make_sortable()/SortableJS semble incompatible avec du contenu
    #teleporte, pour une raison non identifiee avec certitude (possible
    #decalage entre l'arbre DOM et l'arbre de composants Vue affectant la
    #resolution d'evenements de SortableJS).
    #
    #SOLUTION : ce panneau N'EST PAS un vrai ui.dialog()/ui.menu(), mais
    #une simple ui.column basculee par .visible, stylisee pour RESSEMBLER
    #a une boite de dialogue (overlay plein ecran fixed + fond assombri +
    #carte centree) sans en etre une techniquement - contourne le piege
    #ci-dessus. Ouvert depuis le bouton "Regroupement des categories..."
    #de la boite de dialogue Parametres (qui se ferme d'abord, un seul
    #panneau visible a la fois).
    config["category_groups"] = normalize_category_groups(config.get("category_groups"))

    category_groups_panel = ui.column().classes(
        "fixed inset-0 z-50 items-center justify-center bg-black/50"
    )
    category_groups_panel.visible = False

    def close_category_groups_panel() -> None:
        category_groups_panel.visible = False

    def open_category_groups_panel() -> None:
        settings_dialog.close()
        category_groups_panel.visible = True

    with category_groups_panel, ui.card().classes("gap-3 w-[40rem] max-w-[95vw]"):
        ui.label("Regroupement des categories").classes("text-lg font-bold")
        ui.label("Glissez une categorie dans une autre case pour les regrouper").classes(
            "text-xs text-slate-400"
        )

        category_group_boxes: list[ui.row] = []

        def category_group_box_classes() -> str:
            return (
                "gap-1 p-2 border border-dashed border-slate-400 rounded-lg "
                "min-h-[2.5rem] min-w-[4rem] items-center flex-wrap flex-1 select-none"
            )

        #relit l'etat de TOUTES les boites (categorie de chaque puce, dans
        #l'ordre, voir default_slot.children) pour reconstruire
        #category_groups, l'applique/persiste, et retire du DOM toute boite
        #devenue vide (une categorie deplacee ailleurs ne laisse pas une
        #case vide trainer indefiniment - "+ Nouveau groupe" permet d'en
        #recreer une au besoin)
        async def on_category_group_sort_end(_event) -> None:
            groups: list[list[str]] = []
            for box in list(category_group_boxes):
                categories = [chip.text for chip in box.default_slot.children]
                if categories:
                    groups.append(categories)
                else:
                    category_group_boxes.remove(box)
                    box.delete()

            config["category_groups"] = groups
            save_config(config)
            await SceneButton.set_category_groups(groups)

        #ce panneau n'etant PLUS un ui.dialog()/ui.menu() teleporte (voir
        #PIEGE ci-dessus), make_sortable() peut etre appelee immediatement
        #a la construction de chaque boite, sans aucun delai ni logique de
        #report - verifie fonctionner ainsi (protocole DevTools de Chrome).
        def add_category_group_box(categories: list[str]) -> None:
            with category_groups_row:
                box = ui.row().classes(category_group_box_classes())
                with box:
                    for category in categories:
                        #select-none INDISPENSABLE (pas juste cosmetique) :
                        #sans ca, un mousedown-drag sur le TEXTE de la puce
                        #est intercepte par le navigateur comme une
                        #selection de texte plutot que declencher le
                        #glisser-deposer SortableJS - piege classique de
                        #SortableJS, reellement rencontre (l'utilisateur ne
                        #pouvait pas glisser du tout, ca selectionnait le
                        #texte a la place)
                        ui.chip(category).props("dense").classes("cursor-move select-none")
                box.make_sortable(group="category_groups", on_end=on_category_group_sort_end)
            category_group_boxes.append(box)

        async def on_add_group_click() -> None:
            add_category_group_box([])

        with ui.row().classes("w-full gap-2 items-start flex-wrap") as category_groups_row:
            for group in config["category_groups"]:
                add_category_group_box(group)

        with ui.row().classes("justify-between items-center w-full mt-2"):
            ui.button("+ Nouveau groupe", on_click=on_add_group_click).props("flat dense").classes(
                "text-xs"
            )
            ui.button("Fermer", on_click=close_category_groups_panel)

    #theme clair/sombre : ui.dark_mode() pilote le rendu des composants
    #Quasar natifs (boites de dialogue, champs...), apply_body_background()
    #couvre le fond de la page, et SceneButton.set_dark_mode() couvre les
    #cartes de section (via on_dark_mode_change, voir build_body) et
    #chaque SceneButton (box, boutons, labels - voir scene_button.py). Le
    #header/footer restent sombres en permanence dans les deux modes (deja
    #bg-slate-900 fixe), non concernes par le toggle.
    dark_mode = ui.dark_mode(config["dark_mode"])
    apply_body_background()
    SceneButton.set_dark_mode(config["dark_mode"])

    #initialisation directe (pas via set_restrict_by_sex/set_restrict_by_cat/
    #set_category_groups, qui sont async et iterent SceneButton._instances
    #pour rafraichir/repousser les boutons DEJA construits) : build_header
    #s'execute AVANT build_body, aucun SceneButton n'existe encore a ce
    #stade, une simple affectation suffit et evite d'avoir a rendre
    #build_header async pour rien.
    SceneButton._restrict_by_sex = config["restrict_by_sex"]
    SceneButton._restrict_by_cat = config["restrict_by_cat"]
    #normalize_category_groups() ici aussi (independamment de la meme
    #normalisation faite dans la boite de dialogue Parametres plus haut,
    #idempotente) : ne pas dependre implicitement de l'ordre du code dans
    #cette fonction pour garantir un partitionnement valide des CATEGORIES
    #des le demarrage, meme si config.json a ete edite/corrompu a la main.
    #save_config() immediat (pas juste en memoire) : load_config() migre
    #deja l'ancien format "category_group_links" (10 cases par paire) vers
    #le nouveau "category_groups" (voir app_config.py) mais seulement EN
    #MEMOIRE - sans cette sauvegarde, le fichier resterait dans l'ancien
    #format jusqu'a ce que l'operateur pense a rouvrir Parametres. Ecrire
    #tout de suite garantit qu'un regroupement deja configure (reellement
    #rencontre : config.json avec "U15|U21": true) survit au tout premier
    #demarrage apres la mise a jour, sans action de l'operateur.
    config["category_groups"] = normalize_category_groups(config.get("category_groups"))
    save_config(config)
    SceneButton._category_groups = config["category_groups"]

    #NiceGUI applique un padding par defaut autour du contenu de page
    #(.nicegui-content), independant du repere du header (QHeader Quasar,
    #plein largeur par defaut, indente uniquement via son propre m-3 cf.
    #ci-dessous). Sans ce reset, le corps de page (build_body) heritait de
    #ce padding EN PLUS de son propre px-3, et se retrouvait plus indente
    #que le header au lieu d'etre aligne avec lui. On neutralise via
    #.style() (pas .classes(replace=...), qui casserait le flex/gap
    #internes de NiceGUI - piege 8.1) puis chaque conteneur reproduit lui
    #meme l'inset de 12px (px-3/m-3) pour rester aligne au header.
    #
    #IMPORTANT : uniquement padding-left/right, PAS le padding-top/bottom.
    #Quasar calcule dynamiquement un padding-top sur ce meme element pour
    #pousser le contenu sous le QHeader (position fixed, superpose au
    #contenu par defaut). Le mettre a 0 (comme un temps fait "padding: 0"
    #generique) annulait ce decalage automatique -> le corps de page se
    #retrouvait affiche SOUS/DERRIERE le header au lieu d'en dessous. Bug
    #reel rencontre et corrige, a ne pas reintroduire.
    ui.query(".nicegui-content").style("padding-left: 0; padding-right: 0;")

    #wrap=False PARTOUT dans le header (ui.header()/ui.row() de NiceGUI
    #passent TOUS les deux a la ligne par defaut - wrap=True implicite,
    #voir nicegui/elements/header.py et row.py) : c'est ce comportement
    #par defaut, jamais neutralise jusqu'ici, qui faisait passer le header
    #sur 2 lignes des que son contenu total (logo+titre+nom d'evenement a
    #gauche, boutons+statuts+horloges a droite) depassait la largeur de la
    #fenetre - obligeant l'operateur a dezoomer le NAVIGATEUR pour tout
    #faire tenir de nouveau sur une ligne. Corrige a la racine (plus de
    #retour a la ligne possible) PLUS le titre logiciel (le texte le plus
    #variable en longueur - configurable dans Parametres) devient
    #REDIMENSIONNABLE plutot que de forcer le header a s'elargir :
    #flex-1 min-w-0 (peut retrecir sous sa largeur naturelle, contrairement
    #au comportement flex par defaut) + une taille de police EN CLAMP()
    #(retrecit progressivement avec la largeur de la fenetre, jamais
    #au-dela des 18px/1.125rem d'origine) + "truncate" en tout dernier
    #recours (points de suspension) si meme la taille minimale du clamp ne
    #suffit pas encore - garantit que le header reste TOUJOURS sur une
    #seule ligne, exigence explicite de l'utilisateur ("scaler le titre
    #afin que le header tienne toujours en une seule ligne sans avoir a
    #dezoomer"). Le groupe de DROITE (boutons/statuts/horloges, tous
    #critiques pour le pilotage) reste lui volontairement a taille FIXE
    #(flex-shrink: 0) - c'est le groupe de GAUCHE qui absorbe seul le
    #manque de place, jamais l'inverse.
    with ui.header(wrap=False).classes(
        "items-stretch justify-between bg-slate-900 text-white p-0 "
        "m-3 rounded-2xl overflow-hidden"
    ):
        with ui.row(wrap=False).classes("items-stretch gap-0 min-w-0"):
            with ui.row().classes("items-center px-4 py-3"):
                ui.image(LOGO_PATH).classes("h-10 w-16")

            with ui.row().classes("h-full items-center bg-white px-4 min-w-0 flex-1"):
                software_title_label = ui.label(config["software_title"]).classes(
                    "font-bold text-slate-900 truncate min-w-0"
                ).style("font-size: clamp(0.7rem, 1.4vw, 1.125rem);")

            #meme traitement que software_title_label juste au-dessus : le
            #titre ET le nom d'evenement (data.event_infos.TITLE/EVENT_NAME,
            #voir update_header plus bas) sont TOUT AUSSI variables en
            #longueur (fournis par le logiciel de chronometrage, aucun
            #controle dessus) et provoquaient le MEME retour a la ligne -
            #bug reel constate visuellement (capture d'ecran) : cette rangee
            #a elle seule suffisait a faire passer le header sur plusieurs
            #lignes, meme une fois le titre logiciel corrige juste au-dessus.
            #flex-1 min-w-0 SUR LA RANGEE (pas seulement sur les labels) :
            #sans lui, cette rangee (flex-basis:auto par defaut) et la boite
            #du titre juste au-dessus (flex-basis:0% via flex-1) ne se
            #partageaient PAS equitablement l'espace restant - celle avec
            #flex-basis:auto (ici) gardait la priorite sur son propre
            #contenu, ecrasant l'autre jusqu'a 0px de large plutot que de
            #retrecir elle-meme. wrap=False sur la rangee + truncate sur
            #CHAQUE label (independants l'un de l'autre, le "-" entre les
            #deux reste toujours a sa taille naturelle).
            with ui.row(wrap=False).classes("items-center gap-1 px-4 py-3 min-w-0 flex-1"):
                title_label = ui.label().classes("text-sm text-slate-300 truncate min-w-0")
                ui.label("-").classes("text-sm text-slate-300")
                event_name_label = ui.label().classes(
                    "text-sm font-bold text-slate-300 truncate min-w-0"
                )

        with ui.row(wrap=False).classes("items-stretch gap-0").style("flex-shrink: 0;"):
            with ui.row(wrap=False).classes("items-center gap-2 px-4 py-3"):
                ui.button(
                    icon="fullscreen",
                    color=None,
                    on_click=fullscreen.toggle,
                ).classes(
                    "border border-slate-600 bg-slate-700/40 hover:bg-slate-700/60 "
                    "text-slate-300 rounded-full"
                ).props("round").bind_icon_from(
                    fullscreen, "value", backward=lambda v: "fullscreen_exit" if v else "fullscreen"
                ).tooltip("Plein ecran")

                ui.button(
                    icon="data_object",
                    color=None,
                    on_click=data_dialog.open,
                ).classes(
                    "border border-slate-600 bg-slate-700/40 hover:bg-slate-700/60 "
                    "text-slate-300 rounded-full"
                ).props("round").tooltip("Donnees recues (WebSocket)")

                ui.button(
                    icon="settings",
                    color=None,
                    on_click=settings_dialog.open,
                ).classes(
                    "border border-slate-600 bg-slate-700/40 hover:bg-slate-700/60 "
                    "text-slate-300 rounded-full"
                ).props("round")

                ui.button(
                    "EMERGENCY ALL OUT",
                    icon="warning",
                    color=None,
                    on_click=on_emergency_click,
                ).classes(
                    "border border-red-500 bg-red-600/20 hover:bg-red-600/30 "
                    "text-red-400 font-bold rounded-full px-4"
                )

                #classe initiale selon _test_pattern_active : reste
                #"enclenche" (voir sa definition) si la page est rechargee
                #pendant que le test pattern est reellement toujours a
                #l'antenne, plutot que de revenir a tort a l'etat inactif
                test_pattern_button = ui.button(
                    "TEST PATTERN",
                    color=None,
                    on_click=on_test_pattern_click,
                ).classes(
                    TEST_PATTERN_ACTIVE_CLASSES
                    if _test_pattern_active["value"]
                    else TEST_PATTERN_IDLE_CLASSES
                )

                #meme principe que test_pattern_button juste au-dessus
                #(classe initiale selon l'etat persistant hors rechargement)
                logo_ticker_button = ui.button(
                    "LOGO TICKER",
                    color=None,
                    on_click=on_logo_ticker_click,
                ).classes(
                    LOGO_TICKER_ACTIVE_CLASSES
                    if _logo_ticker_active["value"]
                    else LOGO_TICKER_IDLE_CLASSES
                )

            caspar_row, caspar_icon, caspar_label, caspar_tooltip = _build_status_box("CASPAR CG")
            ws_row, ws_icon, ws_label, ws_tooltip = _build_status_box("WS DATA")

            with ui.row(wrap=False).classes("h-full items-stretch border-l border-slate-700 bg-white gap-0"):
                clock_label = _build_clock_box("HEURE", border_left=False)
                race_clock_label = _build_clock_box(" TMPS COURSE", border_left=True)

    #rafraichit tout le contenu de la barre d'outils. title_label et
    #race_clock_label relisent tous les deux data.event_infos a chaque
    #appel (pas de valeur mise en cache) : un nouveau message websocket
    #complet (voir BiathDataWebsocket._handle_message) remplace
    #entierement data.event_infos, donc un changement de STARTHOUR se
    #repercute automatiquement sur le temps de course affiche, au plus
    #tard au tick suivant (1s), exactement comme le titre.
    def update_header() -> None:
        title_label.text = data.event_infos.TITLE if data.event_infos else ""
        event_name_label.text = data.event_infos.EVENT_NAME if data.event_infos else ""

        caspar_detail = f"{caspar.ip}:{caspar.port}"
        if caspar.connected and caspar.last_ping_ms is not None:
            caspar_detail += f" - ping {caspar.last_ping_ms:.0f} ms"
        _apply_status(caspar_row, caspar_icon, caspar_label, caspar_tooltip, caspar.connected, caspar_detail)

        ws_detail = f"{data.ip}:{data.port}"
        if data.connected and data.last_ping_ms is not None:
            ws_detail += f" - ping {data.last_ping_ms:.0f} ms"
        _apply_status(ws_row, ws_icon, ws_label, ws_tooltip, data.connected, ws_detail)

        clock_label.text = datetime.now().strftime("%H:%M:%S")
        race_clock_label.text = format_race_clock(data)

        refresh_data_dialog()

    ui.timer(1.0, update_header)


#3 phases de course disponibles pour ranger les boutons, dans l'ordre
#d'affichage (haut -> bas)
SECTIONS: list[tuple[str, str]] = [
    ("prerace", "PRE RACE"),
    ("race", "RACE"),
    ("postrace", "POST RACE"),
]

#facteur de reduction applique a tout le corps de page (hors header/
#footer, non concernes) via la propriete CSS zoom - < 1 = plus petit.
#Ajuster cette seule valeur pour retoucher la taille globale.
BODY_ZOOM = 0.8


#construit le corps de la page : 3 conteneurs empiles verticalement (pre
#race / race / post race). PRE RACE et POST RACE se reduisent a leur
#hauteur de contenu (comportement flex par defaut : flex-grow 0, ne
#prennent que la place dont ils ont besoin) ; RACE absorbe tout le reste
#de la hauteur disponible (flex-1). Ex: POST RACE avec seulement 2 boutons
#reste compact, RACE s'etend d'autant plus pour combler l'espace laisse
#libre. A l'interieur d'un conteneur, les boutons sont ranges dans une
#vraie grille CSS : chaque case (row, column) est positionnee explicitement
#(grid-row/grid-column), ce qui garantit que la colonne N s'aligne
#verticalement sur toutes les lignes de la section (contrairement a des
#lignes flex independantes, ou "colonne N" ne serait que la N-ieme case de
#CETTE ligne precise, sans alignement partage avec les autres lignes).
#
#Les boutons a creer sont lus depuis templates.json (voir buttons_config.py) :
#ajouter, modifier ou retirer un graphique ne necessite plus de toucher a ce
#fichier, seulement d'editer templates.json. La grille reserve toujours sa
#premiere ligne (grid-row 1) pour d'eventuels titres de colonne (voir
#add_column_title, entrees "_column_title": true dans templates.json) :
#les boutons commencent donc a grid-row 2, meme si leur "row" JSON demarre
#a 1 (decalage automatique et transparent, cf. add_button).
#
#Renvoie quand meme add_button(section, row, column, **kwargs_scene_button)
#pour permettre d'ajouter un bouton par code si besoin.
def build_body(caspar: CasparCG, data: BiathDataWebsocket, config: dict):
    section_grids: dict[str, ui.element] = {}
    section_cells: dict[str, dict[tuple[int, int], ui.column]] = {
        key: {} for key, _ in SECTIONS
    }
    section_column_titles: dict[str, dict[int, ui.label]] = {
        key: {} for key, _ in SECTIONS
    }
    section_cards: dict[str, ui.column] = {}

    #classes de la carte d'une section selon le theme courant (voir
    #SceneButton._dark_mode/on_dark_mode_change - meme source de verite que
    #les boutons, pour rester synchronise avec eux)
    def section_card_classes(key: str) -> str:
        grow_classes = "flex-1 " if key == "race" else ""
        if SceneButton._dark_mode:
            return (
                f"w-full {grow_classes}min-h-0 border border-slate-700 rounded-2xl "
                "bg-slate-800 p-3 gap-2 overflow-auto"
            )
        return (
            f"w-full {grow_classes}min-h-0 border border-slate-200 rounded-2xl "
            "bg-white p-3 gap-2 overflow-auto"
        )

    #reapplique les classes de toutes les cartes de section quand le theme
    #change (voir SceneButton.set_dark_mode, appele depuis build_header)
    def refresh_section_cards(_dark: bool) -> None:
        for key, card in section_cards.items():
            card.classes(replace=section_card_classes(key))

    SceneButton.on_dark_mode_change(refresh_section_cards)

    #hauteur disponible = viewport - (header + footer), estimation
    #approximative (marges/paddings des deux) ; chaque section reste
    #overflow-auto en secours si l'estimation est un peu courte (ou si son
    #propre contenu depasse la place qu'il lui reste une fois RACE etendu).
    #zoom (BODY_ZOOM) reduit tout le corps (boutons, texte, grilles...)
    #sans toucher au header/footer, qui restent en dehors de ce conteneur.
    #La hauteur brute (avant zoom) est augmentee d'autant (/BODY_ZOOM) pour
    #que la hauteur RENDUE, une fois le zoom applique, reste bien egale a
    #la hauteur disponible calculee ci-dessus (sinon un espace vide
    #apparaitrait au-dessus du footer, le zoom retrecissant aussi la boite
    #elle-meme dans le flux de la page).
    with ui.column().classes("w-full px-3 pb-3 gap-3").style(
        f"height: calc((100vh - 190px) / {BODY_ZOOM}); zoom: {BODY_ZOOM};"
    ):
        for key, title in SECTIONS:
            card = ui.column().classes(section_card_classes(key))
            section_cards[key] = card
            with card:
                ui.label(title).classes(
                    "text-sm font-bold tracking-wide text-slate-500"
                )
                #gap uniforme et MODESTE (16px) : la place des icones
                #d'alerte (connexion/format) n'est plus reservee ici, mais
                #PAR BOUTON via une marge conditionnelle sur son propre
                #conteneur (voir SceneButton._refresh_warning_spacing) -
                #un gap de grille uniformement large aurait ecarte TOUS
                #les boutons meme ceux sans aucune icone, ce qui est
                #precisement ce que l'utilisateur ne voulait plus : "si il
                #n'y a pas d'icone d'erreur entre les boutons, faire en
                #sorte que les boutons soient plus proches" (correction
                #suite au premier passage, qui avait elargi gap-x pour
                #EVITER un chevauchement - toujours evite, mais maintenant
                #par bouton plutot que par grille entiere)
                section_grids[key] = ui.element("div").classes(
                    "w-full grid gap-4 items-start justify-items-center"
                ).style("grid-auto-columns: max-content; grid-auto-rows: max-content;")

    #liste JSON brute (boutons + titres de colonne + doc), chargee UNE
    #SEULE fois : button_configs/column_titles ci-dessous en sont derives
    #(parametre `raw=`) plutot que d'une relecture independante du fichier,
    #pour que les memes objets soient reutilises partout. Necessaire pour
    #que persist_templates() (re)ecrive bien l'etat courant, y compris les
    #options de free_text ajoutees en direct depuis un SceneButton (qui
    #mute son propre dict free_text - le MEME objet que celui contenu ici,
    #voir SceneButton._on_add_free_text_option).
    raw_configs = load_raw_configs()

    #reecrit templates.json avec l'etat courant de raw_configs - passe aux
    #boutons comme on_config_changed, pour persister un ajout d'option de
    #free_text fait en direct par l'operateur
    def persist_templates() -> None:
        save_button_configs(raw_configs)

    #recupere (ou cree) la case (row, column) de la grille du conteneur
    #"section" ("prerace"/"race"/"postrace") - plusieurs elements sur la
    #meme case s'empilent verticalement dans cette case. +1 sur grid-row :
    #la ligne 1 de la grille est reservee aux titres de colonne
    #(add_column_title), row=1 cote JSON/appelant correspond donc a
    #grid-row 2. Partagee par add_button et add_restrict_controls.
    def _get_or_create_cell(section: str, row: int, column: int) -> ui.column:
        if section not in section_grids:
            known = ", ".join(key for key, _ in SECTIONS)
            raise ValueError(f"Section inconnue '{section}', attendu l'une de : {known}")

        #coercion defensive : templates.json etant edite a la main, "row"/
        #"column" peuvent facilement finir en string (ex: "1" au lieu de 1,
        #erreur reelle rencontree) - row + 1 plante sinon (TypeError: can
        #only concatenate str). int() accepte aussi bien un int qu'une
        #string numerique, donc ce cast est un no-op dans le cas normal.
        row = int(row)
        column = int(column)

        cells = section_cells[section]
        cell_key = (row, column)

        if cell_key not in cells:
            with section_grids[section]:
                cells[cell_key] = ui.column().classes("gap-2").style(
                    f"grid-row: {row + 1}; grid-column: {column};"
                )

        return cells[cell_key]

    #ajoute un SceneButton dans la case (row, column) de la grille du
    #conteneur "section" - voir _get_or_create_cell.
    def add_button(
        section: str, row: int, column: int, **scene_button_kwargs
    ) -> SceneButton:
        with _get_or_create_cell(section, row, column):
            return SceneButton(
                caspar, data, on_config_changed=persist_templates, **scene_button_kwargs
            )

    #ajoute, dans la case (row, column) de la grille du conteneur
    #"section" (voir _get_or_create_cell - peut cohabiter avec un bouton
    #dans la meme case, ils s'empilent), les 2 cases a cocher globales
    #"restreindre par sexe"/"par categorie" (voir SceneButton.
    #set_restrict_by_sex/set_restrict_by_cat). Placement libre choisi par
    #l'operateur via templates.json ("_restrict_controls": true), un peu
    #comme un titre de colonne ou un bouton - pas fixe dans le header.
    #Leur etat est persiste dans config.json (contrairement au reste de
    #l'etat en direct d'un bouton, qui ne survit jamais a un redemarrage).
    #
    #row FORCE a 1 (parametre JSON "row" retire, ignore s'il est present -
    #exigence explicite de l'utilisateur : ces cases doivent TOUJOURS etre
    #tout en haut de la pile d'elements de leur colonne, juste sous le
    #titre, jamais ailleurs). Voir aussi build_body : cette fonction est
    #appelee AVANT add_button pour le meme (section, column), afin que ces
    #cases soient bien le PREMIER enfant de la case (row=1) meme si un
    #bouton partage la meme case - l'empilement suit l'ordre d'insertion,
    #pas le contenu.
    def add_restrict_controls(section: str, column: int) -> None:
        async def on_sex_change(e) -> None:
            config["restrict_by_sex"] = e.value
            save_config(config)
            await SceneButton.set_restrict_by_sex(e.value)

        async def on_cat_change(e) -> None:
            config["restrict_by_cat"] = e.value
            save_config(config)
            await SceneButton.set_restrict_by_cat(e.value)

        with _get_or_create_cell(section, 1, column):
            with ui.column().classes("gap-1"):
                ui.checkbox(
                    "Restreindre par sexe",
                    value=config["restrict_by_sex"],
                    on_change=on_sex_change,
                )
                ui.checkbox(
                    "Restreindre par categorie",
                    value=config["restrict_by_cat"],
                    on_change=on_cat_change,
                )

    #ajoute un titre au-dessus de la colonne "column" de la grille du
    #conteneur "section" (grid-row 1, reservee a cet usage - voir
    #add_button). Chaque (section, column) ne peut avoir qu'un seul titre :
    #un second appel remplace le texte du premier plutot que d'en creer un
    #second au meme endroit.
    def add_column_title(section: str, column: int, title: str) -> None:
        if section not in section_grids:
            known = ", ".join(key for key, _ in SECTIONS)
            raise ValueError(f"Section inconnue '{section}', attendu l'une de : {known}")

        #meme coercion defensive que _get_or_create_cell (voir son
        #commentaire) - column n'y passe pas pour un titre, direct ici
        column = int(column)

        titles = section_column_titles[section]

        if column in titles:
            titles[column].text = title
            return

        #margin-bottom negatif : la grille espace normalement chaque ligne
        #de gap-4 (1rem/16px) ; on annule quasiment cet espace SOUS le
        #titre uniquement (sans toucher au gap entre les lignes de boutons
        #suivantes) pour le coller a la premiere ligne de la colonne
        with section_grids[section]:
            titles[column] = ui.label(title).classes(
                "text-sm font-bold uppercase tracking-wide text-slate-400 text-center"
            ).style(f"grid-row: 1; grid-column: {column}; margin-bottom: -14px;")

    #_restrict_controls traite EN PREMIER, avant les boutons : add_button
    #et add_restrict_controls partagent la meme case (row=1, column) via
    #_get_or_create_cell quand ils coexistent, et l'empilement dans une
    #case suit l'ordre d'INSERTION (pas de tri par contenu) - traiter les
    #cases a cocher en premier garantit qu'elles sont bien le tout premier
    #enfant de leur case, donc tout en haut de la colonne (exigence
    #explicite de l'utilisateur), meme si un bouton partage la meme case.
    for restrict_config in load_restrict_controls(raw=raw_configs):
        add_restrict_controls(restrict_config["section"], restrict_config["column"])

    #boucle sur "button_config" et non "config" : ce dernier nom designe
    #deja le dict de reglages applicatifs (config.json) passe en parametre
    #de build_body - une boucle "for config in ..." l'ecraserait pour le
    #reste de la fonction (une boucle for ne cree pas de portee propre en
    #Python), cassant add_restrict_controls plus haut des que la boucle a
    #tourne au moins une fois (piege reel evite ici, pas rencontre en prod).
    for button_config in load_button_configs(raw=raw_configs):
        kwargs = dict(button_config)
        section = kwargs.pop("section")
        row = kwargs.pop("row", 1)
        column = kwargs.pop("column", 1)
        add_button(section, row, column, **kwargs)

    for title_config in load_column_titles(raw=raw_configs):
        add_column_title(
            title_config["section"], title_config["column"], title_config["title"]
        )

    return add_button


#couleur/icone du badge d'un marqueur selon son role LE PLUS IMPORTANT
#(un athlete peut cumuler plusieurs roles - ex: leader general ET 1er de
#sa categorie ET de son sexe - un seul badge par athlete, colore/priorise
#par le role le plus significatif ; tous les roles restent listes dans le
#tooltip). Ordre de priorite : LEADER > NOUVEAU LEADER ? > 1ER <cat/sexe>
#> 2E/3E (podium general, uniquement affiche en vue etendue).
def _race_progress_badge_style(roles: list[str]) -> tuple[str, str | None]:
    if "LEADER" in roles:
        return "bg-amber-500 border-amber-200", "emoji_events"
    if "NOUVEAU LEADER ?" in roles:
        return "bg-orange-500 border-orange-200 animate-pulse", "trending_up"
    if any(role.startswith("1ER") for role in roles):
        return "bg-blue-500 border-blue-200", None
    #2E/3E : medaille (argent/bronze - la couleur du badge porte deja le
    #metal, l'icone "military_tech" renforce visuellement qu'il s'agit
    #d'une place de podium) - demande explicite de l'utilisateur
    if "2E" in roles:
        return "bg-slate-400 border-slate-200", "military_tech"
    if "3E" in roles:
        return "bg-amber-700 border-amber-300", "military_tech"
    return "bg-slate-500 border-slate-200", None


#construit la section "progression de course" affichee au-dessus des
#boutons du footer (voir build_footer) - conteneur toujours construit
#(meme si race_progress_enabled=False au demarrage), sa visibilite est
#basculee en direct depuis la boite de dialogue Parametres (voir
#build_header/footer_hooks). Renvoie (conteneur, fonction de
#rafraichissement) : le conteneur pour la bascule de visibilite, la
#fonction pour la reconnecter au polling/push de build_footer.
#
#Ligne horizontale (DEPART -> intermediaires -> ARRIVEE, dans l'ordre de
#data.config.interorder, voir race_progress.py) avec un badge par athlete
#"notable" (leader general, 1er de chaque categorie/sexe, potentiel
#nouveau leader - et 2e/3e du classement general en vue etendue),
#positionne a son intermediaire actuel. Volontairement COMPACT EN HAUTEUR
#(hauteur fixe, badges alignes sur une seule bande horizontale, meme en
#vue etendue - seul le NOMBRE de badges affiches change) - exigence
#explicite de l'utilisateur.
def _build_race_progress_section(data: BiathDataWebsocket) -> tuple[ui.column, Callable[[], None]]:
    expanded = {"value": False}

    with ui.column().classes("w-full gap-1") as container:
        with ui.row().classes("w-full items-center justify-between px-1"):
            ui.label("PROGRESSION DE COURSE").classes(
                "text-xs font-bold uppercase tracking-wide text-slate-400"
            )
            expand_icon = ui.icon("expand_more").classes(
                "text-slate-400 hover:text-white cursor-pointer text-lg"
            )

        #libelles d'intermediaires TOUJOURS visibles (pas seulement au
        #survol, exigence explicite de l'utilisateur) : peuvent depasser la
        #largeur du footer si le format de course a beaucoup d'
        #intermediaires (ex: individuel, 8 tirs + 12 boucles) - defile
        #horizontalement plutot que de laisser les encarts se chevaucher.
        #line_container (largeur MINIMALE calculee dans refresh(), voir
        #_RACE_PROGRESS_MIN_WIDTH_PER_CHECKPOINT) vit DANS ce conteneur
        #defilant, badges et encarts d'intermediaires restent donc alignes
        #entre eux quel que soit le defilement.
        with ui.element("div").classes("w-full overflow-x-auto"):
            #hauteur augmentee (64->84px) pour laisser la place au nom de
            #l'athlete sous chaque badge (voir refresh()), en plus de
            #l'icone deja presente - exigence explicite de l'utilisateur
            line_container = ui.element("div").classes("relative").style("height: 84px;")
        placeholder_label = ui.label(
            "Progression de course : en attente de donnees d'intermediaires..."
        ).classes("text-xs text-slate-500 italic px-1")
        placeholder_label.visible = False

    #rafraichit ENTIEREMENT le contenu de line_container (vide + reconstruit -
    #le NOMBRE/la position des badges change a chaque appel, pas seulement
    #leur texte, un clear()+reconstruction est plus simple/robuste qu'un
    #diff manuel pour un contenu qui change de forme, pas seulement de valeur)
    def refresh() -> None:
        if not container.visible:
            return

        progress = compute_race_progress(data.config, data.timing_data)
        line_container.clear()
        placeholder_label.visible = progress is None
        if progress is None:
            return

        sequence = progress.sequence
        gaps = max(1, len(sequence) - 1)

        #largeur minimale ~90px par intermediaire (encarts de libelle
        #desormais toujours visibles, plus larges qu'une simple puce) -
        #.style() en ADD (voir Style.__call__) fusionne avec "height: 64px"
        #deja pose a la construction, ne l'ecrase pas
        min_width = max(700, len(sequence) * 90)
        line_container.style(f"min-width: {min_width}px;")

        markers = progress.markers if expanded["value"] else [
            marker
            for marker in progress.markers
            if any(role == "LEADER" or role == "NOUVEAU LEADER ?" or role.startswith("1ER")
                   for role in marker.roles)
        ]

        #leader general = le marqueur le plus avance (role "LEADER",
        #toujours present si progress n'est pas None - voir
        #race_progress.compute_race_progress) : determine jusqu'ou colorer
        #la ligne de base ("si un athlete est deja passe a l'inter, on
        #colorie jusqu'a l'inter" - exigence explicite de l'utilisateur)
        leader = next((m for m in progress.markers if "LEADER" in m.roles), None)
        leader_pct = (leader.index / gaps * 100) if leader else 0.0

        with line_container:
            #ligne de base : segment COLORE (course deja parcourue, jusqu'au
            #leader general) + segment gris (pas encore atteint) - transition
            #animee pour un effet de progression fluide d'un rafraichissement
            #a l'autre plutot qu'un saut brut
            ui.element("div").classes("absolute bg-slate-700").style(
                "left: 12px; right: 12px; top: 54px; height: 4px; border-radius: 2px;"
            )
            ui.element("div").classes(
                "absolute bg-emerald-500 transition-all duration-500"
            ).style(
                f"left: 12px; top: 54px; height: 4px; border-radius: 2px; "
                f"width: calc((100% - 24px) * {leader_pct / 100});"
            )

            #un encart par intermediaire (TOUJOURS visible, plus une simple
            #puce+tooltip comme avant) - centre sur la ligne de base, SAUF
            #aux 2 extremites (DEPART a 0%, ARRIVEE a 100%) qui sont
            #ancrees vers l'interieur plutot que centrees : un encart
            #centre sur 0%/100% deborderait pour moitie hors du conteneur
            #defilant et serait tronque (bug reel constate visuellement :
            #"DEPART"/"ARRIVEE" tronques en "ART"/"ARR")
            #
            #cas particulier ENTREE/SORTIE TIR (demande explicite) : une
            #icone de cible + une fleche entrante (login) ou sortante
            #(logout) en plus du libelle texte, pour les distinguer d'un
            #coup d'oeil des intermediaires de boucle. Detecte sur la CLE
            #brute (_key), pas le libelle traduit - C_INTERINSHOOT = entree
            #au tir, C_INTERSHOOT = sortie du tir (voir race_progress.
            #checkpoint_label pour la correspondance complete des prefixes).
            for index, (_key, label) in enumerate(sequence):
                pct = index / gaps * 100
                translate_x = "0%" if index == 0 else "-100%" if index == len(sequence) - 1 else "-50%"
                is_shoot_entry = _key.startswith("C_INTERINSHOOT")
                is_shoot_exit = _key.startswith("C_INTERSHOOT")
                is_loop = _key.startswith("C_INTERSLOPE")
                #couleur par TYPE de checkpoint (demande explicite) : entree
                #de tir en rouge, sortie de tir en vert, inter de piste en
                #gris - remplace l'ancien code couleur "atteint/pas atteint"
                #pour ces 3 types (deja porte par le segment colore de la
                #ligne de base, voir plus haut). DEPART/ARRIVEE/RELAIS (non
                #vises par la demande) gardent l'ancien code atteint/pas
                #atteint (emerald/gris).
                if is_shoot_entry:
                    box_classes = "bg-red-800 border-red-500 text-red-100"
                elif is_shoot_exit:
                    box_classes = "bg-emerald-800 border-emerald-500 text-emerald-100"
                elif is_loop:
                    box_classes = "bg-slate-700 border-slate-500 text-slate-200"
                else:
                    reached = index <= (leader.index if leader else -1)
                    box_classes = (
                        "bg-emerald-800 border-emerald-500 text-emerald-100"
                        if reached
                        else "bg-slate-800 border-slate-600 text-slate-400"
                    )
                with ui.element("div").classes("absolute").style(
                    f"left: {pct}%; top: 56px; transform: translate({translate_x}, 0);"
                ):
                    with ui.element("div").classes(
                        f"{box_classes} flex items-center gap-0.5 text-[9px] font-bold "
                        "px-1.5 py-0.5 rounded border whitespace-nowrap"
                    ):
                        if is_shoot_entry or is_shoot_exit:
                            ui.icon("track_changes").classes("text-[11px]")
                            ui.icon("login" if is_shoot_entry else "logout").classes(
                                "text-[11px]"
                            )
                        ui.label(label)

            #badges d'athletes AU-DESSUS des encarts d'intermediaires,
            #groupes par intermediaire pour etaler HORIZONTALEMENT (pas
            #verticalement - hauteur compacte) ceux qui partagent la meme
            #position - meme ancrage aux extremites que les encarts
            #ci-dessus, pour la meme raison (evite un badge tronque)
            by_index: dict[int, list[AthleteProgress]] = defaultdict(list)
            for marker in markers:
                by_index[marker.index].append(marker)

            for index, group in by_index.items():
                pct = index / gaps * 100
                translate_x = "0%" if index == 0 else "-100%" if index == len(sequence) - 1 else "-50%"
                #ecart entre badges d'un meme checkpoint augmente (26->50px)
                #car chaque badge est desormais plus large (nom de l'athlete
                #affiche en dessous, pas seulement l'icone/numero) - sinon
                #2 noms voisins se chevaucheraient
                for slot, marker in enumerate(group):
                    x_offset = (slot - (len(group) - 1) / 2) * 50
                    badge_classes, icon = _race_progress_badge_style(marker.roles)
                    with ui.element("div").classes(
                        "absolute cursor-help flex flex-col items-center gap-0.5"
                    ).style(
                        f"left: calc({pct}% + {x_offset}px); top: 2px; width: 48px; "
                        f"transform: translateX({translate_x});"
                    ):
                        with ui.element("div").classes(
                            f"{badge_classes} rounded-full text-white text-[10px] font-bold "
                            "w-6 h-6 flex items-center justify-center border-2 shadow"
                        ):
                            if icon:
                                ui.icon(icon).classes("text-xs")
                            else:
                                ui.label(marker.bib)
                        #nom de l'athlete, TOUJOURS affiche en texte sous le
                        #badge (en plus de l'icone/numero) - exigence
                        #explicite de l'utilisateur. Tronque avec ellipse si
                        #trop long (largeur fixe du badge, voir plus haut)
                        #plutot que de deborder sur le badge voisin.
                        ui.label(marker.name).classes(
                            "text-[8px] font-semibold text-white text-center "
                            "leading-none truncate w-full"
                        )
                        with ui.tooltip().classes("text-xs"):
                            roles_text = " / ".join(marker.roles)
                            ui.label(
                                f"{marker.bib} {marker.name} ({marker.category}) - "
                                f"{roles_text} - {sequence[marker.index][1]} "
                                f"({format_elapsed(marker.elapsed)})"
                            )

    def toggle_expanded() -> None:
        expanded["value"] = not expanded["value"]
        expand_icon.props(f"name={'expand_less' if expanded['value'] else 'expand_more'}")
        refresh()

    expand_icon.on("click", toggle_expanded)

    return container, refresh


#construit le pied de page : actions globales independantes de quel(s)
#bouton(s) ont envoye ce qui est actuellement a l'antenne/en preview (utile
#si l'operateur a perdu le fil de ce qui est affiche)
def build_footer(
    caspar: CasparCG, data: BiathDataWebsocket, config: dict, footer_hooks: dict
) -> None:
    #stoppe (CG STOP, meme mecanisme que le survol "OUT" de chaque bouton,
    #avec l'auto-remove differe habituel) tous les graphiques actuellement
    #au programme, sans toucher a la preview
    async def on_all_out_click() -> None:
        await SceneButton.stop_all_pgm()
        ui.notify("ALL OUT : tous les graphiques au programme ont ete stoppes", type="warning")

    #clear (immediat, sans animation) TOUS les slots de preview
    #actuellement actifs et resynchronise les boutons concernes - voir
    #SceneButton.clear_all_previews (plusieurs boutons peuvent etre en
    #preview en meme temps depuis l'introduction du multi-slot, chacun sur
    #son propre layer)
    async def on_clear_prw_click() -> None:
        errors = await SceneButton.clear_all_previews()

        if errors:
            ui.notify(f"Erreur lors du clear preview : {'; '.join(errors)}", type="negative")
        else:
            ui.notify("Preview clear", type="info")

    #envoie au programme TOUS les graphiques actuellement en preview (Take
    #de chaque bouton qui occupe un slot : stop avec animation de sortie
    #de ce qui etait deja au programme sur son propre layer/cg_layer, puis
    #joue sa preview)
    async def on_take_preview_click() -> None:
        await SceneButton.take_all_previews()

    #meme chose que TAKE PREVIEW, mais pour chaque paire (preview, pgm) sur
    #un meme layer, renvoie en plus en preview le graphique qui occupait le
    #programme juste avant (echange complet)
    async def on_swap_click() -> None:
        await SceneButton.swap_preview_pgm()

    #invoque la fonction "sponsor_in" (CG INVOKE, voir casparcg.py) sur
    #TOUS les graphiques actuellement au programme - demande explicite de
    #l'utilisateur, bouton SPONSOR IN
    async def on_sponsor_in_click() -> None:
        await SceneButton.invoke_all_pgm("sponsor_in")

    #flex-col EXPLICITE : q-footer (Quasar) est en ligne (row) par defaut -
    #coherent avec l'ancien contenu (une seule rangee de boutons), mais il
    #faut desormais empiler VERTICALEMENT la section progression de course
    #(optionnelle, au-dessus) et la rangee de boutons (inchangee, en
    #dessous) - piege identique en esprit a 8.1 (classes par defaut a ne
    #pas perdre en modifiant .classes()), ici l'inverse : ajouter une
    #classe pour changer un comportement par defaut herite de Quasar.
    with ui.footer().classes(
        "flex-col gap-2 bg-slate-900 p-3 m-3 rounded-2xl overflow-hidden"
    ):
        race_progress_container, refresh_race_progress = _build_race_progress_section(data)
        race_progress_container.visible = config["race_progress_enabled"]

        #bascule de visibilite en direct depuis Parametres (voir
        #build_header) - un dict simple partage entre les 2 fonctions
        #(cree une seule fois par chargement de page dans main.py), le
        #meme principe que `config` : build_header s'execute AVANT
        #build_footer, mais l'appel reel ne se fait qu'au clic de
        #l'operateur sur "Enregistrer", bien apres que build_footer ait
        #eu l'occasion de peupler footer_hooks - aucun probleme d'ordre.
        def set_race_progress_visible(enabled: bool) -> None:
            race_progress_container.visible = enabled
            if enabled:
                refresh_race_progress()

        footer_hooks["set_race_progress_visible"] = set_race_progress_visible

        #rafraichissement INSTANTANE a chaque nouveau message websocket
        #complet (voir BiathDataWebsocket.on_message, meme mecanisme que
        #SceneButton._subscribe_to_new_data) + un polling ui.timer(1.0,
        #...) en filet de securite (coherent avec le reste du fichier) -
        #refresh_race_progress() lui-meme ne fait rien si le conteneur est
        #actuellement masque (voir son propre garde-fou), donc aucun cout
        #inutile quand la fonctionnalite est desactivee.
        data.on_message(refresh_race_progress)
        ui.timer(1.0, refresh_race_progress)

        with ui.row().classes("items-center justify-center gap-4 w-full"):
            ui.button(
                "ALL OUT",
                icon="stop_circle",
                color=None,
                on_click=on_all_out_click,
            ).classes(
                "border border-orange-500 bg-orange-600/20 hover:bg-orange-600/30 "
                "text-orange-400 font-bold rounded-full px-8 py-3 text-lg"
            )

            ui.button(
                "CLEAR PRW",
                icon="layers_clear",
                color=None,
                on_click=on_clear_prw_click,
            ).classes(
                "border border-yellow-500 bg-yellow-600/20 hover:bg-yellow-600/30 "
                "text-yellow-400 font-bold rounded-full px-8 py-3 text-lg"
            )

            ui.button(
                "TAKE PREVIEW",
                icon="arrow_upward",
                color=None,
                on_click=on_take_preview_click,
            ).classes(
                "border border-green-500 bg-green-600/20 hover:bg-green-600/30 "
                "text-green-400 font-bold rounded-full px-8 py-3 text-lg"
            )

            ui.button(
                "SWAP",
                icon="swap_vert",
                color=None,
                on_click=on_swap_click,
            ).classes(
                "border border-blue-500 bg-blue-600/20 hover:bg-blue-600/30 "
                "text-blue-400 font-bold rounded-full px-8 py-3 text-lg"
            )

            ui.button(
                "SPONSOR IN",
                icon="loyalty",
                color=None,
                on_click=on_sponsor_in_click,
            ).classes(
                "border border-purple-500 bg-purple-600/20 hover:bg-purple-600/30 "
                "text-purple-400 font-bold rounded-full px-8 py-3 text-lg"
            )
