from datetime import datetime

from nicegui import ui

from app_config import save_config
from biathdata_ws import BiathDataWebsocket
from buttons_config import (
    load_button_configs,
    load_column_titles,
    load_raw_configs,
    save_button_configs,
)
from casparcg import CasparCG
from paths import resource_path
from scene_button import SceneButton

#ressource EN LECTURE SEULE embarquee dans l'executable en mode installe
#(voir paths.py) - jamais modifiee par le logiciel, contrairement a
#templates.json/config.json
LOGO_PATH = resource_path("assets", "foxxprod_logo.png")


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
#(bordure gauche), hauteur pleine, jamais sur 2 lignes.
def _build_status_box(title: str) -> tuple[ui.row, ui.icon, ui.label]:
    with ui.column().classes(
        "h-full w-32 justify-between border-l border-slate-700 gap-0"
    ):
        with ui.row().classes("flex-1 w-full items-center justify-center px-3"):
            ui.label(title).classes(
                "text-sm font-bold tracking-wide text-slate-300 whitespace-nowrap"
            )

        with ui.row().classes(
            replace="flex-1 w-full flex items-center justify-center gap-1 flex-nowrap"
        ) as status_row:
            icon = ui.icon("wifi_off").classes("text-base")
            status_label = ui.label("DECONNECTE").classes("text-xs font-bold whitespace-nowrap")

    return status_row, icon, status_label


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
#lentement quand deconnecte
def _apply_status(row: ui.row, icon: ui.icon, label: ui.label, connected: bool) -> None:
    base = "flex-1 w-full flex items-center justify-center gap-1 flex-nowrap "

    if connected:
        row.classes(replace=base + "bg-green-600/20 text-green-400")
    else:
        row.classes(replace=base + "bg-red-600/20 text-red-400 animate-pulse")

    icon.name = "wifi" if connected else "wifi_off"
    label.text = "CONNECTE" if connected else "DECONNECTE"


#construit la barre d'outils en haut de la page
def build_header(caspar: CasparCG, data: BiathDataWebsocket, config: dict) -> None:
    #declenche l'emergency all out et notifie le resultat
    async def on_emergency_click() -> None:
        prw_response, pgm_response = await caspar.emergency_all_out()
        SceneButton.reset_all()

        if prw_response.is_error or pgm_response.is_error:
            details = "; ".join(
                response.message
                for response in (prw_response, pgm_response)
                if response.is_error
            )
            ui.notify(f"Erreur lors de l'emergency all out : {details}", type="negative")
        else:
            ui.notify("Emergency all out effectue", type="warning")

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

        save_config(config)

        dark_mode.set_value(config["dark_mode"])
        apply_body_background()
        SceneButton.set_dark_mode(config["dark_mode"])
        software_title_label.text = config["software_title"]

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

    #boite de dialogue Parametres : theme, titre du logiciel, adresse/port
    #du serveur de donnees et de CasparCG, numeros de channel CasparCG.
    #Construite une seule fois (pas a chaque ouverture, contrairement aux
    #boites de dialogue ephemeres de scene_button.py) puisqu'elle vit dans
    #le header, present en permanence.
    with ui.dialog() as settings_dialog, ui.card().classes("gap-3 w-[28rem]"):
        ui.label("Parametres").classes("text-lg font-bold")

        dark_switch = ui.switch("Mode sombre", value=config["dark_mode"])

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

        with ui.row().classes("justify-end gap-2 w-full mt-2"):
            ui.button("Annuler", on_click=settings_dialog.close)
            ui.button("Enregistrer", on_click=on_settings_save).classes(
                "bg-slate-800 text-white"
            )

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

    with ui.header().classes(
        "items-stretch justify-between bg-slate-900 text-white p-0 "
        "m-3 rounded-2xl overflow-hidden"
    ):
        with ui.row().classes("items-stretch gap-0"):
            with ui.row().classes("items-center px-4 py-3"):
                ui.image(LOGO_PATH).classes("h-10 w-16")

            with ui.row().classes("h-full items-center bg-white px-4"):
                software_title_label = ui.label(config["software_title"]).classes(
                    "text-lg font-bold text-slate-900 whitespace-nowrap"
                )

            with ui.row().classes("items-center gap-1 px-4 py-3"):
                title_label = ui.label().classes("text-sm text-slate-300")
                ui.label("-").classes("text-sm text-slate-300")
                event_name_label = ui.label().classes("text-sm font-bold text-slate-300")

        with ui.row().classes("items-stretch gap-0"):
            with ui.row().classes("items-center gap-2 px-4 py-3"):
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

            caspar_row, caspar_icon, caspar_label = _build_status_box("CASPAR CG")
            ws_row, ws_icon, ws_label = _build_status_box("WS DATA")

            with ui.row().classes("h-full items-stretch border-l border-slate-700 bg-white gap-0"):
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

        _apply_status(caspar_row, caspar_icon, caspar_label, caspar.connected)
        _apply_status(ws_row, ws_icon, ws_label, data.connected)

        clock_label.text = datetime.now().strftime("%H:%M:%S")
        race_clock_label.text = format_race_clock(data)

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
def build_body(caspar: CasparCG, data: BiathDataWebsocket):
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

    #ajoute un SceneButton dans la case (row, column) de la grille du
    #conteneur "section" ("prerace" / "race" / "postrace"), en creant la
    #case si necessaire ; plusieurs boutons sur la meme case s'empilent
    #verticalement dans cette case. +1 sur grid-row : la ligne 1 de la
    #grille est reservee aux titres de colonne (add_column_title), row=1
    #cote JSON/appelant correspond donc a grid-row 2.
    def add_button(
        section: str, row: int, column: int, **scene_button_kwargs
    ) -> SceneButton:
        if section not in section_grids:
            known = ", ".join(key for key, _ in SECTIONS)
            raise ValueError(f"Section inconnue '{section}', attendu l'une de : {known}")

        cells = section_cells[section]
        cell_key = (row, column)

        if cell_key not in cells:
            with section_grids[section]:
                cells[cell_key] = ui.column().classes("gap-2").style(
                    f"grid-row: {row + 1}; grid-column: {column};"
                )

        with cells[cell_key]:
            return SceneButton(
                caspar, data, on_config_changed=persist_templates, **scene_button_kwargs
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

    for config in load_button_configs(raw=raw_configs):
        kwargs = dict(config)
        section = kwargs.pop("section")
        row = kwargs.pop("row", 1)
        column = kwargs.pop("column", 1)
        add_button(section, row, column, **kwargs)

    for title_config in load_column_titles(raw=raw_configs):
        add_column_title(
            title_config["section"], title_config["column"], title_config["title"]
        )

    return add_button


#construit le pied de page : actions globales independantes de quel(s)
#bouton(s) ont envoye ce qui est actuellement a l'antenne/en preview (utile
#si l'operateur a perdu le fil de ce qui est affiche)
def build_footer(caspar: CasparCG, data: BiathDataWebsocket) -> None:
    #stoppe (CG STOP, meme mecanisme que le survol "OUT" de chaque bouton,
    #avec l'auto-remove differe habituel) tous les graphiques actuellement
    #au programme, sans toucher a la preview
    async def on_all_out_click() -> None:
        await SceneButton.stop_all_pgm()
        ui.notify("ALL OUT : tous les graphiques au programme ont ete stoppes", type="warning")

    #clear (immediat, sans animation) la preview et resynchronise le
    #bouton qui l'occupait, meme mecanisme que _claim_preview
    async def on_clear_prw_click() -> None:
        response = await caspar.clear("prw", layer=1)
        SceneButton.reset_preview_button()

        if response.is_error:
            ui.notify(f"Erreur lors du clear preview : {response.message}", type="negative")
        else:
            ui.notify("Preview clear", type="info")

    #envoie au programme le graphique actuellement en preview (Take du
    #bouton qui l'occupe : stop avec animation de sortie de ce qui etait
    #deja au programme sur le meme layer/cg_layer, puis joue la preview)
    async def on_take_preview_click() -> None:
        await SceneButton.take_current_preview()

    #meme chose que TAKE PREVIEW, mais renvoie en plus en preview le
    #graphique qui occupait le programme juste avant (echange complet)
    async def on_swap_click() -> None:
        await SceneButton.swap_preview_pgm()

    with ui.footer().classes(
        "items-center justify-center gap-4 bg-slate-900 p-3 "
        "m-3 rounded-2xl overflow-hidden"
    ):
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
