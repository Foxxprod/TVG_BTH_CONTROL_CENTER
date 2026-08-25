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

from typing import Callable

from nicegui import ui

from biathdata_ws import BiathDataWebsocket
from casparcg import CasparCG


class SceneButton:
    """Bouton representant un graphique CasparCG configurable.

    Comportement au clic (corps du bouton) :
        - idle      -> joue le template en preview
        - preview   -> Take vers le programme (play_prw_to_pgm)
        - programme -> ne fait rien (utiliser le bouton "OUT" pour stop)

    Au survol, un petit rectangle apparait au-dessus du bouton :
        - "IN"  (idle)     -> envoie directement au programme
        - "IN"  (preview)  -> Take vers le programme (comme un clic sur le
          corps du bouton en preview)
        - "OUT" (programme) -> stop le graphique ; contrairement aux autres
          etats, ce bouton reste visible en permanence (pas besoin de
          survoler) puisque le graphique est a l'antenne.

    Un seul graphique peut occuper la preview a la fois (elle correspond a
    un unique layer CasparCG, partage par tous les boutons). Mettre un
    nouveau bouton en preview clear donc de force ce qui s'y trouvait deja
    et remet a idle le bouton qui l'occupait.

    De meme sur le programme : un (layer, cg_layer) CasparCG donne ne peut
    avoir qu'un seul occupant. Si un bouton est envoye au programme (play
    direct ou Take) sur le meme (layer, cg_layer) qu'un bouton deja a
    l'antenne, ce dernier est stoppe (comme un clic sur son propre "OUT")
    avant que le nouveau ne soit charge, et son etat repasse a idle.

    free_texts (optionnel) declare une ou plusieurs zones de saisie libre
    affichees sous le bouton, ex :
        free_texts=[
            {"field": "quote"},
            {"field": "sponsor", "label": "Sponsor", "options": ["A", "B"]},
        ]
    Chaque entree a un "field" (cle envoyee au template), un "label"
    optionnel (affiche sur le champ, defaut = field), et des "options"
    optionnelles. Sans "options" : une seule ligne, simple champ texte
    libre. Avec "options" : DEUX lignes sont affichees - une combo box
    (choix parmi les options) et un champ texte libre en dessous ;
    selectionner une valeur dans la combo box vide le texte libre, et
    inversement taper dans le texte libre deselectionne la combo box (les
    deux sont mutuellement exclusifs, un seul alimente la valeur envoyee).
    La valeur courante de chaque champ est envoyee a chaque play/update.
    Les champs sont grises (non modifiables) tant que le bouton n'est pas
    idle (evite de changer la valeur pendant que le graphique est a
    l'ecran).

    bib_on=True affiche aussi sous le bouton l'athlete actuellement
    selectionne (ou "Aucun athlete selectionne") et une petite icone pour
    le changer, qui rouvre la boite de dialogue de selection a tout moment
    (pas seulement au premier play) ; si le graphique est deja affiche, le
    changement est pousse immediatement via update_template.

    target_on=True affiche de la meme maniere sous le bouton la valeur
    cible actuelle (ou "Aucune valeur cible", 1-30) avec deux icones : un
    crayon pour la changer (rouvre la boite de dialogue de saisie a tout
    moment, pas seulement au premier play) et une poubelle pour la remettre
    a 0 directement, sans boite de dialogue. Si le graphique est deja
    affiche, tout changement est pousse immediatement via update_template.
    La boite de dialogue de saisie valide sur Entree, annule sur Echap, et
    recoit le focus automatiquement (utile pour enchainer directement
    dessus juste apres avoir renseigne le dossard, si bib_on est aussi
    actif).

    intermediate_on=True declare un bouton "intermediaire" (ex: split de
    tir, de piste, ou de relais) : une combo box apparait sous le bouton,
    listant les intermediaires disponibles pour intermediate_type :
        - "shoot_out" -> C_INTERSHOOT* ACTUELLEMENT en course (filtre
          depuis data.config.interorder, rafraichi chaque seconde)
        - "slope"     -> C_INTERSLOPE* ACTUELLEMENT en course (idem)
        - "relay"     -> C_RELAY1/2/3, liste fixe (pas filtree depuis
          interorder, ce n'est pas un C_INTER...)
    La valeur choisie est envoyee au template dans field_intermediate
    (defaut "timekey"), poussee immediatement via update_template si le
    graphique est deja affiche. Tant qu'aucun intermediaire n'est
    selectionne, ce bouton ne peut pas etre lance (play direct ou Take) ni
    mis a jour : _build_payload bloque et notifie l'operateur.

    race_formats (optionnel, ex: ["PURSUIT"]) restreint ce bouton a une
    liste de formats de course (compares a data.event_infos.RACEFORMAT) :
    si le format actuel n'y figure pas, le bouton est grise et
    inutilisable (main click/hover IN-OUT/fleches multipage sans effet),
    avec une icone d'avertissement a cote expliquant pourquoi. Omis (None,
    par defaut) = actif pour tous les formats de course. Verifie et
    rafraichi chaque seconde (le format peut n'etre connu qu'apres coup,
    le websocket se connectant apres la construction de l'interface).
    """

    #registre de toutes les instances vivantes, utilise par reset_all()
    #(ex: apres un emergency all out) pour remettre tous les boutons a idle
    _instances: list["SceneButton"] = []

    #bouton qui occupe actuellement la preview (un seul a la fois, la
    #preview etant un unique layer CasparCG partage entre tous les boutons)
    _active_preview: "SceneButton | None" = None

    #bouton qui occupe actuellement chaque (layer, cg_layer) du programme
    _active_pgm: dict[tuple[int, int], "SceneButton"] = {}

    #prefixe de cle correspondant a chaque intermediate_type. "shoot_out"
    #et "slope" sont filtres depuis data.config.interorder (intermediaires
    #reellement en course) ; "relay" n'y figure pas (ce n'est pas un
    #C_INTER..., voir biathdata_reference.txt section 3) et propose donc
    #toujours une liste fixe C_RELAY1..3 (voir _current_intermediate_options)
    _INTERMEDIATE_PREFIXES = {
        "shoot_out": "C_INTERSHOOT",
        "slope": "C_INTERSLOPE",
        "relay": "C_RELAY",
    }

    #gabarit de nom d'affichage convivial pour chaque prefixe (le "{n}" est
    #remplace par le numero extrait de la cle, ex: "C_INTERSLOPE3" -> "Tour 3")
    _INTERMEDIATE_LABELS = {
        "C_INTERSLOPE": "Tour {n}",
        "C_INTERSHOOT": "Sortie tir {n}",
        "C_RELAY": "Relais {n}",
    }

    #theme courant, partage par tous les boutons (voir set_dark_mode).
    #Autres modules (ex: interface.build_body pour les cartes de section)
    #peuvent s'abonner au changement via on_dark_mode_change plutot que de
    #dupliquer leur propre etat sombre/clair.
    _dark_mode: bool = False
    _dark_mode_listeners: list[Callable[[bool], None]] = []

    #change le theme pour TOUS les boutons existants et deja construits
    #(rafraichit box + apparence de chacun), et previent les abonnes
    #externes (ex: cartes de section). Utilise par la boite de dialogue
    #Parametres du header.
    @classmethod
    def set_dark_mode(cls, dark: bool) -> None:
        cls._dark_mode = dark
        for button in cls._instances:
            button._refresh_box_style()
            button._refresh_appearance()
        for callback in cls._dark_mode_listeners:
            callback(dark)

    #enregistre un callback appele a chaque changement de theme (voir
    #set_dark_mode) - utilise par interface.build_body pour les cartes de
    #section, qui ne sont pas des SceneButton mais doivent suivre le meme
    #theme
    @classmethod
    def on_dark_mode_change(cls, callback: Callable[[bool], None]) -> None:
        cls._dark_mode_listeners.append(callback)

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
        reset_page_on_play: bool = False,
        target_on: bool = False,
        field_target: str = "target",
        free_texts: list[dict[str, object]] | None = None,
        intermediate_on: bool = False,
        intermediate_type: str = "shoot_out",
        field_intermediate: str = "timekey",
        race_formats: list[str] | None = None,
        on_config_changed: Callable[[], None] | None = None,
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
        self.reset_page_on_play = reset_page_on_play

        self.target_on = target_on
        self.field_target = field_target

        self.free_texts = free_texts or []

        self.intermediate_on = intermediate_on
        self.intermediate_type = intermediate_type
        self.field_intermediate = field_intermediate

        #None = actif pour tous les formats de course, sinon liste des
        #RACEFORMAT (data.event_infos.RACEFORMAT) pour lesquels ce bouton
        #est utilisable
        self.race_formats = race_formats

        #appele quand une option de free_text est ajoutee en direct (voir
        #_on_add_free_text_option), pour que l'appelant (interface.py)
        #persiste le changement dans templates.json - scene_button.py ne
        #sait pas ou/comment ecrire ce fichier, separation des
        #responsabilites oblige
        self._on_config_changed = on_config_changed

        self._state = "idle"  # "idle" | "preview" | "programme"
        self._page = 1
        self._bib: str | None = None
        self._bib_label: ui.label | None = None
        self._target: int | None = None
        self._target_label: ui.label | None = None
        self._free_text_widgets: dict[str, tuple[ui.select | None, ui.input]] = {}
        self._intermediate_select: ui.select | None = None
        self._intermediate_warning_icon: ui.icon | None = None
        self._format_warning_icon: ui.icon | None = None
        self._format_warning_tooltip: ui.tooltip | None = None
        self._box: ui.column | None = None

        self._build()
        SceneButton._instances.append(self)

    #remet ce bouton dans son etat de base (idle, sans dossard ni valeur
    #cible memorises), sans envoyer de commande AMCP - CasparCG est deja
    #vide au moment ou c'est utilise (apres un emergency all out)
    def reset_to_idle(self) -> None:
        self._state = "idle"
        self._bib = None
        self._target = None
        if SceneButton._active_preview is self:
            SceneButton._active_preview = None
        if SceneButton._active_pgm.get((self.layer, self.cg_layer)) is self:
            del SceneButton._active_pgm[(self.layer, self.cg_layer)]
        self._refresh_appearance()

    #remet TOUS les boutons existants a idle (utilise apres un emergency all out)
    @classmethod
    def reset_all(cls) -> None:
        for button in cls._instances:
            button.reset_to_idle()

    #stoppe (CG STOP classique + auto-remove differe habituel, meme
    #mecanisme que le survol "OUT" de chaque bouton) tous les boutons
    #actuellement au programme, sans toucher a la preview - utilise par le
    #bouton ALL OUT du footer
    @classmethod
    async def stop_all_pgm(cls) -> None:
        for button in list(cls._instances):
            if button._state == "programme":
                await button._stop()

    #remet a idle le bouton qui occupe actuellement la preview, sans
    #envoyer de commande AMCP - utilise juste apres un clear direct de la
    #preview fait ailleurs (ex: bouton CLEAR PRW du footer, qui appelle
    #caspar.clear() lui-meme puis resynchronise l'affichage via cette
    #methode)
    @classmethod
    def reset_preview_button(cls) -> None:
        if cls._active_preview is not None:
            cls._active_preview.reset_to_idle()

    #envoie au programme le graphique actuellement en preview - reutilise
    #simplement le Take normal du bouton qui l'occupe (stop avec animation
    #de sortie de ce qui etait deja au programme sur le meme layer/
    #cg_layer, puis play_prw_to_pgm). Ne fait rien (avec notification) si
    #rien n'est en preview. Utilise par le bouton TAKE PREVIEW du footer.
    @classmethod
    async def take_current_preview(cls) -> None:
        button = cls._active_preview
        if button is None:
            ui.notify("Aucun graphique en preview", type="warning")
            return

        await button._take()

    #comme take_current_preview, mais renvoie EN PLUS en preview le
    #graphique qui occupait le programme juste avant (vrai echange de
    #position, pas juste un stop). Son payload actuel (bib/target/
    #intermediaire/page/free_texts) est capture AVANT de le stopper, pour
    #etre rejoue a l'identique en preview ensuite. Utilise par le bouton
    #SWAP du footer.
    #
    #IMPORTANT : le bouton au programme a echanger n'est PAS force cherche
    #uniquement sur le (layer, cg_layer) du bouton en preview. Un Take
    #normal (_claim_pgm_slot) ne stoppe que ce qui occupe EXACTEMENT ce
    #meme (layer, cg_layer) - correct pour un Take simple, mais insuffisant
    #ici : preview et programme utilisent tres souvent des layers
    #CasparCG differents (chaque type de graphique a son propre layer).
    #Si on ne cherchait le pgm a echanger qu'au layer du bouton preview,
    #le vrai occupant du programme (sur un autre layer) n'etait ni stoppe
    #ni jamais renvoye en preview : il restait affiche, et le bouton
    #preview venait juste s'ajouter a cote sur son propre layer (bug reel
    #rencontre et corrige). On cherche donc explicitement le (layer,
    #cg_layer) du bouton preview EN PRIORITE (cas le plus propre, meme
    #layer), sinon n'importe quel autre bouton actuellement au programme
    #(l'appli est concue pour un seul graphique a l'antenne a la fois), et
    #on le stoppe nous-memes AVANT le take (au lieu de compter sur
    #_claim_pgm_slot, qui ne le trouverait pas si son layer differe).
    @classmethod
    async def swap_preview_pgm(cls) -> None:
        preview_button = cls._active_preview
        if preview_button is None:
            ui.notify("Aucun graphique en preview", type="warning")
            return

        pgm_button = cls._active_pgm.get((preview_button.layer, preview_button.cg_layer))
        if pgm_button is None:
            others = [b for b in cls._active_pgm.values() if b is not preview_button]
            if others:
                pgm_button = others[0]

        pgm_payload = None
        if pgm_button is not None:
            pgm_payload = await pgm_button._build_payload(ask_bib=False)
            await pgm_button._stop()

        await preview_button._take()

        if pgm_button is not None and pgm_payload is not None:
            response = await pgm_button.caspar.play_template(
                "prw", pgm_button.template, data=pgm_payload, cg_layer=pgm_button.cg_layer
            )
            if response.is_success:
                pgm_button._state = "preview"
                cls._active_preview = pgm_button
                pgm_button._refresh_appearance()

    #construit l'element visuel : bouton (+ fleche multipage + rectangle
    #survol), eventuel affichage/changement d'athlete (bib_on), eventuel
    #affichage/changement de valeur cible (target_on), eventuelle combo box
    #d'intermediaire (intermediate_on) et eventuels champs de saisie libre.
    #S'il y a quelque chose sous le bouton, le tout est regroupe dans une
    #meme "box" bordee (pour montrer visuellement que c'est lie) ; sinon,
    #pas de cadre (le bouton seul n'a rien a montrer comme "lie"). L'icone
    #d'alerte de connexion reste a cote dans les deux cas. La box n'a pas
    #de largeur propre : elle se dimensionne sur son enfant le plus large
    #(le bouton, w-64) et les champs (w-full) se calent dessus, donc ne
    #peuvent jamais depasser la largeur du bouton.
    def _build(self) -> None:
        with ui.row().classes("items-start gap-2"):
            if self.bib_on or self.target_on or self.intermediate_on or self.free_texts:
                self._box = ui.column()
                with self._box:
                    self._build_button()
                    if self.bib_on:
                        self._build_bib_display()
                    if self.target_on:
                        self._build_target_display()
                    if self.intermediate_on:
                        self._build_intermediate_select()
                    if self.free_texts:
                        self._build_free_text_inputs()
                self._refresh_box_style()
            else:
                self._build_button()

            self._build_connection_warning()

        ui.timer(1.0, self._refresh_connection_warning)
        if self.intermediate_on:
            ui.timer(1.0, self._refresh_intermediate_options)
        if self.race_formats is not None:
            ui.timer(1.0, self._refresh_format_availability)

    #texte gris "attenue" (labels bib/target sous le bouton), adapte au theme
    def _muted_text_color(self) -> str:
        return "text-slate-300" if SceneButton._dark_mode else "text-slate-600"

    #met a jour la couleur de la box englobante (bib/target/intermediaire/
    #free_texts) selon le theme courant
    def _refresh_box_style(self) -> None:
        if self._box is None:
            return

        if SceneButton._dark_mode:
            self._box.classes(
                replace="border border-slate-700 rounded-xl bg-slate-800 p-2 gap-2"
            )
        else:
            self._box.classes(
                replace="border border-slate-300 rounded-xl bg-slate-50 p-2 gap-2"
            )

    def _build_button(self) -> None:
        with ui.column().classes("relative group inline-flex gap-0 w-64"):
            self._hover_label = ui.label("IN")
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
                    self._label_widget = ui.label(self._label_text()).classes(
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

            #icone "interdit" construite ICI (dans le meme conteneur
            #"relative" que le bouton, pas dans la box englobante) pour
            #qu'elle se positionne par rapport au bouton seul : a droite,
            #centree sur sa hauteur, meme si la box contient d'autres
            #champs (bib/intermediaire/free_texts) plus bas
            if self.race_formats is not None:
                self._build_format_warning()

        self._refresh_appearance()

    #texte affiche dans le corps du bouton (avec le numero de page si multipage_on)
    def _label_text(self) -> str:
        if self.multipage_on:
            return f"{self.label} ({self._page})"
        return self.label

    #construit les champs de saisie libre affiches sous le bouton (un par
    #entree de self.free_texts), en w-full pour se caler sur la largeur de
    #la box (donc du bouton, voir _build). Sans "options" : un seul
    #ui.input. Avec "options" : une combo box (ui.select, valeurs limitees
    #a la liste) ET un ui.input texte libre en dessous, mutuellement
    #exclusifs (choisir l'un vide l'autre) - un select seul ne permet pas
    #de texte hors liste, d'ou les deux champs separes plutot qu'un widget
    #hybride
    def _build_free_text_inputs(self) -> None:
        with ui.column().classes("w-full gap-1"):
            for free_text in self.free_texts:
                field = free_text["field"]
                label = free_text.get("label", field)
                options = free_text.get("options")

                if options:
                    with ui.row().classes("w-full items-center gap-1"):
                        select_widget = ui.select(
                            options=list(options), value=None, label=label
                        ).classes("flex-1")

                        add_icon = ui.icon("add_circle").classes(
                            "text-slate-500 hover:text-green-600 cursor-pointer text-lg"
                        )

                    input_widget = ui.input(label="Texte libre").classes("w-full")

                    #cle sur des arguments par defaut (pas une reference
                    #directe aux variables de boucle), sinon tous les
                    #free_texts d'un meme bouton finiraient par pointer sur
                    #la DERNIERE iteration de la boucle (piege classique de
                    #fermeture Python sur variable de boucle)
                    add_icon.on(
                        "click",
                        lambda _, ft=free_text, sel=select_widget, inp=input_widget: (
                            self._on_add_free_text_option(ft, sel, inp)
                        ),
                    )

                    select_widget.on_value_change(
                        lambda e, inp=input_widget: inp.set_value(None) if e.value else None
                    )
                    input_widget.on_value_change(
                        lambda e, sel=select_widget: sel.set_value(None) if e.value else None
                    )

                    self._free_text_widgets[field] = (select_widget, input_widget)
                else:
                    input_widget = ui.input(label=label).classes("w-full")
                    self._free_text_widgets[field] = (None, input_widget)

    #ouvre une petite boite de dialogue pour ajouter une nouvelle option a
    #un free_text a combo box, EN DIRECT : ajoutee a la liste d'options du
    #ui.select (utilisable immediatement, selectionnee automatiquement) ET
    #a free_text["options"] - le meme dict que celui charge depuis
    #templates.json (voir interface.build_body), donc persistee dans le
    #fichier via self._on_config_changed si fourni (sinon la nouvelle
    #option ne survit qu'a la session en cours)
    async def _on_add_free_text_option(
        self, free_text: dict, select_widget: ui.select, input_widget: ui.input
    ) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Nouvelle option").classes("text-base font-bold")
            new_option_input = (
                ui.input(label=free_text.get("label", free_text["field"]))
                .classes("w-64")
                .props("autofocus")
            )
            new_option_input.on("keydown.enter", lambda: dialog.submit(new_option_input.value))
            new_option_input.on("keydown.escape", lambda: dialog.submit(None))

            with ui.row():
                ui.button("Annuler", on_click=lambda: dialog.submit(None))
                ui.button("Ajouter", on_click=lambda: dialog.submit(new_option_input.value))

        ui.timer(0.1, lambda: new_option_input.run_method("focus"), once=True)

        value = await dialog

        if not value:
            return

        options = free_text.setdefault("options", [])
        if value not in options:
            options.append(value)

        select_widget.options = list(options)
        select_widget.value = value
        select_widget.update()
        input_widget.set_value(None)

        if self._on_config_changed is not None:
            self._on_config_changed()

        ui.notify(f"Option '{value}' ajoutee", type="positive")

    #nom affiche pour un athlete : nom + prenom, avec C_NAME colle juste
    #apres (sans espace - champ "nom d'affichage alternatif", voir
    #biathdata_reference.txt section 3, souvent vide)
    @staticmethod
    def _athlete_name(athlete) -> str:
        last_name = getattr(athlete, "C_LAST_NAME", "")
        first_name = getattr(athlete, "C_FIRST_NAME", "")
        name = getattr(athlete, "C_NAME", "")
        return f"{last_name} {first_name}{name}"

    #texte affiche sous le bouton pour l'athlete actuellement selectionne
    def _bib_display_text(self) -> str:
        if self._bib is None:
            return "Aucun athlete selectionne"

        athlete = self.data.timing_data.get(self._bib)
        if athlete is None:
            return f"Dossard {self._bib}"

        return f"{self._bib} - {self._athlete_name(athlete)}"

    #affiche l'athlete actuellement selectionne (bib_on) + une icone pour
    #le changer, qui rouvre la boite de dialogue de selection a tout
    #moment (pas seulement au premier play)
    def _build_bib_display(self) -> None:
        with ui.row().classes("w-full items-center justify-between gap-1"):
            self._bib_label = ui.label(self._bib_display_text()).classes(
                f"text-xs {self._muted_text_color()} truncate"
            )
            change_icon = ui.icon("edit").classes(
                "text-slate-500 hover:text-slate-800 cursor-pointer text-base"
            )
            change_icon.on("click", self._on_change_bib_click)

    #rouvre la boite de dialogue de selection de dossard, met a jour
    #l'affichage et pousse la nouvelle valeur si le graphique est deja
    #affiche (preview ou programme)
    async def _on_change_bib_click(self) -> None:
        bib = await self._ask_bib()
        if bib is None:
            return

        self._bib = bib
        self._refresh_appearance()

        if self._state != "idle":
            await self._update_data()

    #texte affiche sous le bouton pour la valeur cible actuellement choisie
    def _target_display_text(self) -> str:
        if self._target is None:
            return "Aucune valeur cible"
        return f"Target : {self._target}"

    #affiche la valeur cible actuelle (target_on) + une icone pour la
    #changer (rouvre la boite de dialogue de saisie a tout moment, pas
    #seulement au premier play) et une icone poubelle pour la remettre a 0
    #directement, sans boite de dialogue
    def _build_target_display(self) -> None:
        with ui.row().classes("w-full items-center justify-between gap-1"):
            self._target_label = ui.label(self._target_display_text()).classes(
                f"text-xs {self._muted_text_color()} truncate"
            )
            with ui.row().classes("items-center gap-1"):
                reset_icon = ui.icon("delete").classes(
                    "text-slate-500 hover:text-red-600 cursor-pointer text-base"
                )
                reset_icon.on("click", self._on_reset_target_click)

                change_icon = ui.icon("edit").classes(
                    "text-slate-500 hover:text-slate-800 cursor-pointer text-base"
                )
                change_icon.on("click", self._on_change_target_click)

    #rouvre la boite de dialogue de saisie de la valeur cible, met a jour
    #l'affichage et pousse la nouvelle valeur si le graphique est deja
    #affiche (preview ou programme)
    async def _on_change_target_click(self) -> None:
        target = await self._ask_target()
        if target is None:
            return

        self._target = target
        self._refresh_appearance()

        if self._state != "idle":
            await self._update_data()

    #remet la valeur cible a 0 directement (sans boite de dialogue, donc
    #hors des bornes 1-30 de _ask_target - sert de valeur "desactivee"),
    #pousse la nouvelle valeur si le graphique est deja affiche
    async def _on_reset_target_click(self) -> None:
        self._target = 0
        self._refresh_appearance()

        if self._state != "idle":
            await self._update_data()

    #nom d'affichage convivial pour un intermediaire ("C_INTERSLOPE3" ->
    #"Tour 3", "C_INTERSHOOT2" -> "Sortie tir 2"), ou le nom brut si le
    #prefixe/suffixe n'est pas reconnu (garde-fou, ne devrait pas arriver
    #vu que name vient deja d'un filtre par prefixe)
    def _intermediate_label(self, name: str) -> str:
        prefix = self._INTERMEDIATE_PREFIXES.get(self.intermediate_type)
        template = self._INTERMEDIATE_LABELS.get(prefix) if prefix else None
        if not template or not name.startswith(prefix):
            return name

        suffix = name[len(prefix):]
        return template.format(n=suffix) if suffix.isdigit() else name

    #intermediaires actuellement pertinents pour la course, filtres par
    #intermediate_type, a partir de data.config.interorder (config recue
    #en direct via le websocket - contient aussi les cles C_RELAY1/2/3 si
    #et seulement si le format de course actuel en est un ; vide tant
    #qu'aucun message complet n'est encore arrive, ou si le type est
    #inconnu). dict {cle brute: label convivial} - ui.select envoie la
    #cle (utilisee par CasparCG), affiche le label.
    def _current_intermediate_options(self) -> dict[str, str]:
        prefix = self._INTERMEDIATE_PREFIXES.get(self.intermediate_type)
        if prefix is None or self.data.config is None:
            return {}

        interorder = getattr(self.data.config, "interorder", None) or []
        return {
            name: self._intermediate_label(name)
            for name in interorder
            if name.startswith(prefix)
        }

    #combo box listant les intermediaires actuellement en course pour
    #intermediate_type ; pousse immediatement la nouvelle valeur si le
    #graphique est deja affiche (preview ou programme). "dense
    #options-dense" (props Quasar) reduisent l'espacement du champ ET des
    #lignes du menu deroulant. Une icone d'avertissement apparait a cote
    #et le champ passe en rouge (color=negative, prop Quasar dediee a
    #l'etat d'erreur) tant qu'aucun intermediaire n'est choisi.
    def _build_intermediate_select(self) -> None:
        with ui.row().classes("w-full items-center gap-1"):
            self._intermediate_select = (
                ui.select(
                    options=self._current_intermediate_options(),
                    value=None,
                    label="Intermediaire",
                )
                .classes("flex-1")
                .props("dense options-dense")
            )

            self._intermediate_warning_icon = ui.icon("warning").classes(
                "text-red-500 text-base cursor-help"
            )
            with self._intermediate_warning_icon:
                ui.tooltip("Aucun intermediaire selectionne").classes(
                    "bg-red-600 text-white font-bold"
                )

        self._intermediate_select.on_value_change(self._on_intermediate_change)
        self._refresh_intermediate_warning()

    async def _on_intermediate_change(self, _event) -> None:
        self._refresh_intermediate_warning()
        if self._state != "idle":
            await self._update_data()

    #met le champ en rouge (color=negative) et affiche l'icone
    #d'avertissement tant qu'aucun intermediaire n'est selectionne
    def _refresh_intermediate_warning(self) -> None:
        if self._intermediate_select is None:
            return

        missing = not self._intermediate_select.value

        if missing:
            self._intermediate_select.props("color=negative")
        else:
            self._intermediate_select.props(remove="color")

        if self._intermediate_warning_icon is not None:
            self._intermediate_warning_icon.visible = missing

    #resynchronise la liste d'options de la combo box intermediaire depuis
    #data.config.interorder (peut n'apparaitre qu'apres coup, le websocket
    #se connectant apres la construction de l'interface). Preserve la
    #selection actuelle si elle est toujours valide, la reinitialise sinon.
    def _refresh_intermediate_options(self) -> None:
        if self._intermediate_select is None:
            return

        options = self._current_intermediate_options()
        if options != self._intermediate_select.options:
            current_value = self._intermediate_select.value
            self._intermediate_select.options = options
            if current_value not in options:
                self._intermediate_select.value = None
            self._intermediate_select.update()

        self._refresh_intermediate_warning()

    #ce bouton est-il utilisable pour le format de course actuel ?
    #race_formats=None -> toujours True (actif pour tous les formats)
    def _is_format_available(self) -> bool:
        if self.race_formats is None:
            return True

        current_format = (
            getattr(self.data.event_infos, "RACEFORMAT", None)
            if self.data.event_infos is not None
            else None
        )
        return current_format in self.race_formats

    #icone d'avertissement affichee quand le format de course actuel n'est
    #pas dans race_formats. Positionnee en absolu (left-full = juste apres
    #le bord droit du conteneur "relative" du bouton, top-1/2
    #-translate-y-1/2 = centree verticalement dessus) pour rester alignee
    #sur le bouton lui-meme, pas sur la box englobante (qui peut etre plus
    #haute si bib_on/intermediate_on/free_texts ajoutent des champs)
    def _build_format_warning(self) -> None:
        self._format_warning_icon = ui.icon("block").classes(
            "absolute left-full top-1/2 -translate-y-1/2 ml-2 "
            "text-slate-500 text-xl cursor-help"
        )
        with self._format_warning_icon:
            self._format_warning_tooltip = ui.tooltip("").classes(
                "bg-slate-700 text-white font-bold"
            )

        self._format_warning_icon.visible = False

    #verifie chaque seconde si le format de course actuel autorise ce
    #bouton, met a jour l'icone d'avertissement et l'aspect du bouton en
    #consequence (le format peut n'etre connu qu'apres coup, le websocket
    #se connectant apres la construction de l'interface)
    def _refresh_format_availability(self) -> None:
        available = self._is_format_available()

        self._format_warning_icon.visible = not available
        current_format = (
            getattr(self.data.event_infos, "RACEFORMAT", None)
            if self.data.event_infos is not None
            else None
        )
        self._format_warning_tooltip.text = (
            f"Indisponible pour ce format de course ({current_format or 'inconnu'})"
        )

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

    #couleur du corps du bouton selon l'etat (adaptee au theme pour idle,
    #qui doit rester distinguable de la box - bg-slate-700 sur une box
    #bg-slate-800 en sombre serait trop proche ; preview/programme restent
    #identiques dans les deux themes, deja assez satures pour contraster
    #partout), texte IN/OUT du survol, visibilite permanente du survol
    #"OUT" quand le graphique est a l'antenne, grisage complet si le
    #format de course actuel est exclu (race_formats)
    def _refresh_appearance(self) -> None:
        colors = {
            "idle": (
                "bg-slate-600 hover:bg-slate-500"
                if SceneButton._dark_mode
                else "bg-slate-700 hover:bg-slate-600"
            ),
            "preview": "bg-yellow-600 hover:bg-yellow-500",
            "programme": "bg-red-600 hover:bg-red-500",
        }

        format_available = self._is_format_available()

        if format_available:
            self._main_area.classes(
                replace="flex-1 items-center justify-center px-2 py-2 cursor-pointer "
                + colors[self._state]
            )
            self._label_widget.classes(
                replace="text-sm font-bold text-white text-center whitespace-nowrap"
            )
        else:
            disabled_bg = "bg-slate-700" if SceneButton._dark_mode else "bg-slate-300"
            disabled_text = "text-slate-400" if SceneButton._dark_mode else "text-slate-500"
            self._main_area.classes(
                replace="flex-1 items-center justify-center px-2 py-2 "
                f"cursor-not-allowed {disabled_bg}"
            )
            self._label_widget.classes(
                replace=f"text-sm font-bold {disabled_text} text-center whitespace-nowrap"
            )

        self._label_widget.text = self._label_text()

        if self._bib_label is not None:
            self._bib_label.text = self._bib_display_text()
            self._bib_label.classes(replace=f"text-xs {self._muted_text_color()} truncate")

        if self._target_label is not None:
            self._target_label.text = self._target_display_text()
            self._target_label.classes(replace=f"text-xs {self._muted_text_color()} truncate")

        on_air = self._state == "programme"
        self._hover_label.text = "OUT" if on_air else "IN"
        if not format_available:
            visibility = "opacity-0 pointer-events-none"
        elif on_air:
            visibility = "opacity-100 pointer-events-auto"
        else:
            visibility = (
                "opacity-0 group-hover:opacity-100 pointer-events-none "
                "group-hover:pointer-events-auto"
            )
        self._hover_label.classes(
            replace="absolute bottom-full left-1/2 -translate-x-1/2 transition-opacity "
            "bg-slate-800 text-white text-sm font-bold rounded-full px-4 py-1.5 "
            "cursor-pointer border border-slate-600 whitespace-nowrap z-10 "
            + visibility
        )

        #champs de saisie libre non modifiables tant que le graphique n'est
        #pas idle (deja envoye, ne pas changer la valeur sous le pied)
        for select_widget, input_widget in self._free_text_widgets.values():
            for widget in (select_widget, input_widget):
                if widget is None:
                    continue
                if self._state == "idle":
                    widget.enable()
                else:
                    widget.disable()

    async def _on_main_click(self) -> None:
        if not self._is_format_available():
            return
        if self._state == "idle":
            await self._play("prw")
        elif self._state == "preview":
            await self._take()

    async def _on_hover_click(self) -> None:
        if not self._is_format_available():
            return
        if self._state == "programme":
            await self._stop()
        elif self._state == "preview":
            await self._take()
        else:
            await self._play("pgm")

    async def _on_next_page(self) -> None:
        if not self._is_format_available():
            return
        self._page += 1
        self._refresh_appearance()
        if self._state != "idle":
            await self._update_data()

    async def _on_prev_page(self) -> None:
        if not self._is_format_available():
            return
        if self._page <= 1:
            return
        self._page -= 1
        self._refresh_appearance()
        if self._state != "idle":
            await self._update_data()

    #assemble les donnees a envoyer (ip, bib, page) selon les attributs
    #actives. Si intermediate_on et qu'aucun intermediaire n'est choisi,
    #bloque des le debut (renvoie None, notifie l'operateur) - AVANT de
    #demander le dossard (_ask_bib peut etre long : recherche/selection
    #dans la liste), pour ne pas faire perdre ce travail a l'operateur
    #s'il s'avere ensuite qu'aucun intermediaire n'a ete choisi.
    async def _build_payload(self, ask_bib: bool) -> dict[str, str] | None:
        payload = dict(self.extra_data)

        if self.intermediate_on:
            if self._intermediate_select is None or not self._intermediate_select.value:
                ui.notify(
                    "Selectionnez un intermediaire avant de lancer ce graphique",
                    type="warning",
                )
                return None
            payload[self.field_intermediate] = self._intermediate_select.value

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

        if self.target_on and self._target is not None:
            payload[self.field_target] = str(self._target)

        for field, (select_widget, input_widget) in self._free_text_widgets.items():
            value = (select_widget.value if select_widget else None) or input_widget.value
            payload[field] = str(value) if value else ""

        return payload

    async def _play(self, channel: str) -> None:
        if self.multipage_on and self.reset_page_on_play:
            self._page = 1

        payload = await self._build_payload(ask_bib=True)
        if payload is None:
            return

        if channel == "prw":
            await self._claim_preview()
        else:
            await self._claim_pgm_slot()

        response = await self.caspar.play_template(
            channel, self.template, data=payload, layer=self.layer, cg_layer=self.cg_layer
        )

        if response.is_error:
            ui.notify(f"Erreur lors du play : {response.message}", type="negative")
            return

        self._state = "preview" if channel == "prw" else "programme"
        if channel == "prw":
            SceneButton._active_preview = self
        else:
            SceneButton._active_pgm[(self.layer, self.cg_layer)] = self
        self._refresh_appearance()

        if self.target_on:
            target = await self._ask_target()
            if target is not None:
                self._target = target
                self._refresh_appearance()
                await self._update_data()

    #force la preview a n'avoir qu'un seul occupant : remet a idle le
    #bouton qui l'occupait avant (s'il y en a un), puis clear le layer de
    #preview cote CasparCG (inconditionnel, meme si le suivi local est
    #desynchronise, ex: apres un redemarrage du process)
    async def _claim_preview(self) -> None:
        previous = SceneButton._active_preview
        if previous is not None and previous is not self:
            previous.reset_to_idle()

        await self.caspar.clear("prw", layer=1)

    #force le (layer, cg_layer) programme vise a n'avoir qu'un seul
    #occupant : stoppe (comme un clic sur son propre "OUT") le bouton qui
    #l'occupait avant, s'il s'agit d'un autre bouton
    async def _claim_pgm_slot(self) -> None:
        previous = SceneButton._active_pgm.get((self.layer, self.cg_layer))
        if previous is not None and previous is not self:
            await previous._stop()

    async def _take(self) -> None:
        await self._claim_pgm_slot()

        response = await self.caspar.play_prw_to_pgm(self.layer)

        if response.is_error:
            ui.notify(f"Erreur lors du take : {response.message}", type="negative")
            return

        self._state = "programme"
        SceneButton._active_pgm[(self.layer, self.cg_layer)] = self
        if SceneButton._active_preview is self:
            SceneButton._active_preview = None
        self._refresh_appearance()

    async def _stop(self) -> None:
        response = await self.caspar.stop("pgm", layer=self.layer, cg_layer=self.cg_layer)

        if response.is_error:
            ui.notify(f"Erreur lors du stop : {response.message}", type="negative")
            return

        self._state = "idle"
        self._bib = None
        self._target = None
        if SceneButton._active_pgm.get((self.layer, self.cg_layer)) is self:
            del SceneButton._active_pgm[(self.layer, self.cg_layer)]
        self._refresh_appearance()

    #renvoie les donnees actuelles (page/bib/target/intermediaire/ip/free_texts)
    #au graphique deja en cours
    async def _update_data(self) -> None:
        channel = "prw" if self._state == "preview" else "pgm"
        layer = None if channel == "prw" else self.layer

        payload = await self._build_payload(ask_bib=False)
        if payload is None:
            return

        await self.caspar.update_template(channel, payload, layer=layer, cg_layer=self.cg_layer)

    #boite de dialogue de selection de dossard, basee sur les donnees en
    #direct. Navigation clavier : fleches haut/bas deplacent le
    #surlignage dans la liste FILTREE actuelle, Entree valide le
    #surlignage courant (comme le bouton "Valider"). Chaque ligne reste
    #aussi cliquable individuellement.
    async def _ask_bib(self) -> str | None:
        def label_for(bib: str, athlete) -> str:
            return f"{bib} - {self._athlete_name(athlete)}"

        athletes = sorted(
            self.data.timing_data.items(), key=lambda item: int(item[0])
        )

        #liste filtree courante et index surligne dedans (-1 = rien
        #surligne, uniquement quand la recherche est vide)
        filtered: list[tuple[str, object]] = []
        selected_index = [-1]

        with ui.dialog() as dialog, ui.card():
            ui.label("Selectionner un dossard").classes("text-base font-bold")

            results = ui.column().classes("gap-1 max-h-64 overflow-auto")

            #redessine la liste filtree en surlignant selected_index[0]
            def render() -> None:
                results.clear()
                with results:
                    for index, (bib, athlete) in enumerate(filtered):
                        is_selected = index == selected_index[0]
                        item = ui.label(label_for(bib, athlete)).classes(
                            "text-sm rounded px-2 py-0.5 cursor-pointer hover:bg-slate-700 "
                            + ("bg-blue-600 text-white font-bold" if is_selected else "")
                        )
                        item.on("click", lambda _, b=bib: dialog.submit(b))

            #recalcule la liste filtree depuis la recherche, surligne le
            #premier resultat (uniquement si une recherche a ete saisie)
            def refresh(search: str) -> None:
                nonlocal filtered
                search = search.strip().lower()
                filtered = [
                    (bib, athlete)
                    for bib, athlete in athletes
                    if not search or search in label_for(bib, athlete).lower()
                ]
                selected_index[0] = 0 if search and filtered else -1
                render()

            #deplace le surlignage de delta (+1/-1) dans la liste filtree
            #actuelle, avec retour au debut/fin (liste circulaire)
            def move_selection(delta: int) -> None:
                if not filtered:
                    return
                if selected_index[0] == -1:
                    selected_index[0] = 0
                else:
                    selected_index[0] = (selected_index[0] + delta) % len(filtered)
                render()

            #valide l'athlete actuellement surligne, si il y en a un
            def confirm_selected() -> None:
                if 0 <= selected_index[0] < len(filtered):
                    dialog.submit(filtered[selected_index[0]][0])

            search_input = (
                ui.input(
                    placeholder="Nom ou dossard...",
                    on_change=lambda e: refresh(e.value or ""),
                )
                .classes("w-64")
                .props("autofocus")
            )
            search_input.on("keydown.enter", confirm_selected)
            search_input.on("keydown.up.prevent", lambda: move_selection(-1))
            search_input.on("keydown.down.prevent", lambda: move_selection(1))

            refresh("")

            with ui.row():
                ui.button("Annuler", on_click=lambda: dialog.submit(None))
                ui.button("Valider", on_click=confirm_selected)

        #assure le focus meme si l'autofocus du navigateur n'a pas suffit
        ui.timer(0.1, lambda: search_input.run_method("focus"), once=True)

        return await dialog

    #boite de dialogue pour saisir une valeur cible entre 1 et 30, renvoie
    #la valeur choisie ou None si annule. Ne pousse rien elle-meme cote
    #CasparCG (contrairement a l'ancienne version) : c'est a l'appelant de
    #decider (_play au premier play, _on_change_target_click ensuite).
    #Entree valide, Echap annule ; le focus est fiabilise comme pour
    #_ask_bib, utile pour enchainer directement dessus juste apres la
    #fermeture de la boite de dialogue dossard (bib_on + target_on)
    async def _ask_target(self) -> int | None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Valeur target (1-30)").classes("text-base font-bold")
            #toujours vide a l'ouverture, meme si une valeur est deja
            #memorisee (self._target) - pas de pre-remplissage voulu
            number = (
                ui.number(min=1, max=30, step=1, value=None)
                .classes("w-32")
                .props("autofocus")
            )
            number.on("keydown.enter", lambda: dialog.submit(number.value))
            number.on("keydown.escape", lambda: dialog.submit(None))

            with ui.row():
                ui.button("Annuler", on_click=lambda: dialog.submit(None))
                ui.button("Valider", on_click=lambda: dialog.submit(number.value))

        #assure le focus meme si l'autofocus du navigateur n'a pas suffit
        ui.timer(0.1, lambda: number.run_method("focus"), once=True)

        value = await dialog

        if value is None:
            return None

        return int(value)
