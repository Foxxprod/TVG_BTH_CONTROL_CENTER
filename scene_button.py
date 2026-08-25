# Exemple d'utilisation :
#
#   SceneButton(
#       caspar, data,
#       label="DOSSARD",
#       template="dossard_template",
#       layer=20,
#       send_ip=True,
#       bib_on=True,
#       multipage_on=True,
#       target_on=True,
#   )

from nicegui import ui

from biathdata_ws import BiathDataWebsocket
from casparcg import CasparCG


class SceneButton:
    """Bouton representant un graphique CasparCG configurable.

    Comportement au clic (corps du bouton) :
        - idle      -> joue le template en preview
        - preview   -> Take vers le programme (play_prw_to_pgm)
        - programme -> ne fait rien (utiliser le survol "OUT" pour stop)

    Au survol, un petit rectangle apparait au-dessus du bouton :
        - "IN"  (idle/preview) -> envoie directement au programme
        - "OUT" (programme)    -> stop le graphique
    """

    def __init__(
        self,
        caspar: CasparCG,
        data: BiathDataWebsocket,
        label: str,
        template: str,
        layer: int,
        cg_layer: int = 1,
        extra_data: dict[str, str] | None = None,
        send_ip: bool = False,
        field_ip: str = "ip",
        bib_on: bool = False,
        field_bib: str = "bib",
        multipage_on: bool = False,
        field_page: str = "page",
        target_on: bool = False,
        field_target: str = "target",
    ):
        self.caspar = caspar
        self.data = data

        self.label = label
        self.template = template
        self.layer = layer
        self.cg_layer = cg_layer
        self.extra_data = extra_data or {}

        self.send_ip = send_ip
        self.field_ip = field_ip

        self.bib_on = bib_on
        self.field_bib = field_bib

        self.multipage_on = multipage_on
        self.field_page = field_page

        self.target_on = target_on
        self.field_target = field_target

        self._state = "idle"  # "idle" | "preview" | "programme"
        self._page = 1
        self._bib: str | None = None

        self._build()

    #construit l'element visuel (bouton + fleche multipage + rectangle survol
    #+ icone d'alerte de connexion)
    def _build(self) -> None:
        with ui.row().classes("items-center gap-2"):
            self._build_button()
            self._build_connection_warning()

        ui.timer(1.0, self._refresh_connection_warning)

    def _build_button(self) -> None:
        with ui.column().classes("relative group inline-flex gap-0 w-40"):
            self._hover_label = ui.label("IN").classes(
                "absolute bottom-full left-1/2 -translate-x-1/2 "
                "opacity-0 group-hover:opacity-100 pointer-events-none "
                "group-hover:pointer-events-auto transition-opacity "
                "bg-slate-800 text-white text-sm font-bold rounded-full px-4 py-1.5 "
                "cursor-pointer border border-slate-600 whitespace-nowrap z-10"
            )
            self._hover_label.on("click", self._on_hover_click)

            with ui.row().classes(
                "w-full items-stretch border rounded-full overflow-hidden gap-0 border-slate-600"
            ):
                if self.multipage_on:
                    prev_area = ui.row().classes(
                        "items-center justify-center px-2 bg-slate-800 "
                        "hover:bg-slate-700 cursor-pointer border-r border-slate-600"
                    )
                    with prev_area:
                        ui.icon("chevron_left").classes("text-white text-base")
                    prev_area.on("click", self._on_prev_page)

                self._main_area = ui.row().classes(
                    "flex-1 items-center justify-center px-2 py-2 cursor-pointer"
                )
                with self._main_area:
                    ui.label(self.label).classes(
                        "text-sm font-bold text-white text-center whitespace-nowrap"
                    )
                self._main_area.on("click", self._on_main_click)

                if self.multipage_on:
                    next_area = ui.row().classes(
                        "items-center justify-center px-2 bg-slate-800 "
                        "hover:bg-slate-700 cursor-pointer border-l border-slate-600"
                    )
                    with next_area:
                        ui.icon("chevron_right").classes("text-white text-base")
                    next_area.on("click", self._on_next_page)

        self._refresh_appearance()

    #icone d'alerte affichee a droite du bouton en cas de deconnexion
    def _build_connection_warning(self) -> None:
        self._warning_icon = ui.icon("warning").classes(
            "text-red-500 text-xl cursor-help"
        )
        with self._warning_icon:
            self._warning_tooltip = ui.tooltip("").classes(
                "bg-red-600 text-white font-bold"
            )

        self._warning_icon.visible = False

    #verifie l'etat de connexion caspar/websocket et met a jour l'icone
    def _refresh_connection_warning(self) -> None:
        problems = []
        if not self.caspar.connected:
            problems.append("CasparCG deconnecte")
        if not self.data.connected:
            problems.append("WebSocket deconnecte")

        self._warning_icon.visible = bool(problems)
        self._warning_tooltip.text = " / ".join(problems)

    #couleur du corps du bouton selon l'etat, texte IN/OUT du survol
    def _refresh_appearance(self) -> None:
        colors = {
            "idle": "bg-slate-700 hover:bg-slate-600",
            "preview": "bg-yellow-600 hover:bg-yellow-500",
            "programme": "bg-red-600 hover:bg-red-500",
        }
        self._main_area.classes(
            replace="flex-1 items-center justify-center px-2 py-2 cursor-pointer "
            + colors[self._state]
        )
        self._hover_label.text = "OUT" if self._state == "programme" else "IN"

    async def _on_main_click(self) -> None:
        if self._state == "idle":
            await self._play("prw")
        elif self._state == "preview":
            await self._take()

    async def _on_hover_click(self) -> None:
        if self._state == "programme":
            await self._stop()
        else:
            await self._play("pgm")

    async def _on_next_page(self) -> None:
        self._page += 1
        if self._state != "idle":
            await self._update_data()

    async def _on_prev_page(self) -> None:
        if self._page <= 1:
            return
        self._page -= 1
        if self._state != "idle":
            await self._update_data()

    #assemble les donnees a envoyer (ip, bib, page) selon les attributs actives
    async def _build_payload(self, ask_bib: bool) -> dict[str, str] | None:
        payload = dict(self.extra_data)

        if self.send_ip:
            payload[self.field_ip] = self.data.ip

        if self.multipage_on:
            payload[self.field_page] = str(self._page)

        if ask_bib and self.bib_on:
            bib = await self._ask_bib()
            if bib is None:
                return None
            self._bib = bib

        if self.bib_on and self._bib is not None:
            payload[self.field_bib] = self._bib

        return payload

    async def _play(self, channel: str) -> None:
        payload = await self._build_payload(ask_bib=True)
        if payload is None:
            return

        response = await self.caspar.play_template(
            channel, self.template, data=payload, layer=self.layer, cg_layer=self.cg_layer
        )

        if response.is_error:
            ui.notify(f"Erreur lors du play : {response.message}", type="negative")
            return

        self._state = "preview" if channel == "prw" else "programme"
        self._refresh_appearance()

        if self.target_on:
            await self._ask_target(channel)

    async def _take(self) -> None:
        response = await self.caspar.play_prw_to_pgm(self.layer)

        if response.is_error:
            ui.notify(f"Erreur lors du take : {response.message}", type="negative")
            return

        self._state = "programme"
        self._refresh_appearance()

    async def _stop(self) -> None:
        response = await self.caspar.stop("pgm", layer=self.layer, cg_layer=self.cg_layer)

        if response.is_error:
            ui.notify(f"Erreur lors du stop : {response.message}", type="negative")
            return

        self._state = "idle"
        self._bib = None
        self._refresh_appearance()

    #renvoie les donnees actuelles (page/bib/ip) au graphique deja en cours
    async def _update_data(self) -> None:
        channel = "prw" if self._state == "preview" else "pgm"
        layer = None if channel == "prw" else self.layer

        payload = await self._build_payload(ask_bib=False)
        if payload is None:
            return

        await self.caspar.update_template(channel, payload, layer=layer, cg_layer=self.cg_layer)

    #boite de dialogue de selection de dossard, basee sur les donnees en direct
    async def _ask_bib(self) -> str | None:
        def label_for(bib: str, athlete) -> str:
            last_name = getattr(athlete, "C_LAST_NAME", "")
            first_name = getattr(athlete, "C_FIRST_NAME", "")
            return f"{bib} - {last_name} {first_name}"

        athletes = sorted(
            self.data.timing_data.items(), key=lambda item: int(item[0])
        )

        first_match: list[str | None] = [None]

        with ui.dialog() as dialog, ui.card():
            ui.label("Selectionner un dossard").classes("text-base font-bold")

            results = ui.column().classes("gap-1 max-h-64 overflow-auto")

            #filtre la liste des athletes, surligne et memorise le premier
            #resultat (uniquement si une recherche a ete saisie), et permet
            #de cliquer directement sur n'importe quel athlete de la liste
            def refresh(search: str) -> None:
                search = search.strip().lower()
                results.clear()
                first_match[0] = None

                for bib, athlete in athletes:
                    text = label_for(bib, athlete).lower()
                    if search and search not in text:
                        continue

                    is_first = bool(search) and first_match[0] is None
                    if is_first:
                        first_match[0] = bib

                    with results:
                        item = ui.label(label_for(bib, athlete)).classes(
                            "text-sm rounded px-2 py-0.5 cursor-pointer hover:bg-slate-700 "
                            + ("bg-blue-600 text-white font-bold" if is_first else "")
                        )
                        item.on("click", lambda _, b=bib: dialog.submit(b))

            search_input = (
                ui.input(
                    placeholder="Nom ou dossard...",
                    on_change=lambda e: refresh(e.value or ""),
                )
                .classes("w-64")
                .props("autofocus")
            )
            search_input.on("keydown.enter", lambda: dialog.submit(first_match[0]))

            refresh("")

            with ui.row():
                ui.button("Annuler", on_click=lambda: dialog.submit(None))
                ui.button(
                    "Valider", on_click=lambda: dialog.submit(first_match[0])
                )

        #assure le focus meme si l'autofocus du navigateur n'a pas suffit
        ui.timer(0.1, lambda: search_input.run_method("focus"), once=True)

        return await dialog

    #boite de dialogue pour saisir une valeur target entre 1 et 30
    async def _ask_target(self, channel: str) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Valeur target (1-30)").classes("text-base font-bold")
            number = ui.number(min=1, max=30, step=1, value=1).classes("w-32")
            with ui.row():
                ui.button("Annuler", on_click=lambda: dialog.submit(None))
                ui.button("Valider", on_click=lambda: dialog.submit(number.value))

        value = await dialog

        if value is None:
            return

        layer = None if channel == "prw" else self.layer
        payload = {self.field_target: str(int(value))}

        await self.caspar.update_template(channel, payload, layer=layer, cg_layer=self.cg_layer)
