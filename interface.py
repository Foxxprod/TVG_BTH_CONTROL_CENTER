from datetime import datetime
from pathlib import Path

from nicegui import ui

from biathdata_ws import BiathDataWebsocket
from casparcg import CasparCG
from scene_button import SceneButton

LOGO_PATH = Path(__file__).parent / "assets" / "foxxprod_logo.png"


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
def build_header(caspar: CasparCG, data: BiathDataWebsocket) -> None:
    #declenche l'emergency all out et notifie le resultat
    async def on_emergency_click() -> None:
        prw_response, pgm_response = await caspar.emergency_all_out()

        if prw_response.is_error or pgm_response.is_error:
            details = "; ".join(
                response.message
                for response in (prw_response, pgm_response)
                if response.is_error
            )
            ui.notify(f"Erreur lors de l'emergency all out : {details}", type="negative")
        else:
            ui.notify("Emergency all out effectue", type="warning")

    #fond de page presque blanc, legerement casse
    ui.query("body").classes(replace="bg-slate-50")

    with ui.header().classes(
        "items-stretch justify-between bg-slate-900 text-white p-0 "
        "m-3 rounded-2xl overflow-hidden"
    ):
        with ui.row().classes("items-stretch gap-0"):
            with ui.row().classes("items-center px-4 py-3"):
                ui.image(LOGO_PATH).classes("h-10 w-16")

            with ui.row().classes("h-full items-center bg-white px-4"):
                ui.label("BIATHLON - CONTROL CENTER").classes(
                    "text-lg font-bold text-slate-900 whitespace-nowrap"
                )

            with ui.row().classes("items-center gap-1 px-4 py-3"):
                title_label = ui.label().classes("text-sm text-slate-300")
                ui.label("-").classes("text-sm text-slate-300")
                event_name_label = ui.label().classes("text-sm font-bold text-slate-300")

        with ui.row().classes("items-stretch gap-0"):
            with ui.row().classes("items-center px-4 py-3"):
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

    #rafraichit tout le contenu de la barre d'outils
    def update_header() -> None:
        title_label.text = data.event_infos.TITLE if data.event_infos else ""
        event_name_label.text = data.event_infos.EVENT_NAME if data.event_infos else ""

        _apply_status(caspar_row, caspar_icon, caspar_label, caspar.connected)
        _apply_status(ws_row, ws_icon, ws_label, data.connected)

        clock_label.text = datetime.now().strftime("%H:%M:%S")
        race_clock_label.text = format_race_clock(data)

    ui.timer(1.0, update_header)


#construit le corps de la page (boutons de scenes)
def build_body(caspar: CasparCG, data: BiathDataWebsocket) -> None:
    with ui.row().classes("p-6 gap-4"):
        SceneButton(
            caspar,
            data,
            label="TEST",
            template="test",
            layer=20,
            send_ip=True,
            bib_on=True,
            multipage_on=True,
            target_on=True,
        )
