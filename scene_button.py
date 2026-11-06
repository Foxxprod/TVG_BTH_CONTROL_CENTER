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

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from nicegui import ui

from biathdata_ws import BiathDataWebsocket
from casparcg import CasparCG


#pont entre le pave numerique du Stream Deck et une boite de dialogue
#ouverte a la souris (_ask_bib/_ask_target) - voir SceneButton.
#_active_streamdeck_input. Chaque champ est une callback FERMEE sur le
#widget concret de la boite (ui.input ou ui.number, formes differentes -
#d'ou cette petite abstraction commune plutot que de coupler
#streamdeck_controller.py au detail de chaque boite de dialogue).
@dataclass
class StreamDeckInputTarget:
    append_digit: Callable[[int], None]
    clear: Callable[[], None]
    submit: Callable[[], None]


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

    Preview et programme suivent la MEME regle : un (layer, cg_layer)
    CasparCG donne ne peut avoir qu'un seul occupant, que ce soit en
    preview ou au programme (chaque bouton previse sur SON PROPRE layer,
    le meme "layer" qu'il utilise au programme - voir _claim_preview).
    Plusieurs boutons DIFFERENTS (sur des layers differents) peuvent donc
    etre en preview EN MEME TEMPS : ca permet de pre-composer plusieurs
    graphiques avant de tous les envoyer au programme d'un coup (voir les
    boutons TAKE PREVIEW/SWAP du pied de page, qui agissent desormais sur
    TOUS les slots de preview actifs, pas un seul). Mettre un bouton en
    preview (ou au programme) clear/stoppe seulement ce qui occupait DEJA
    SON PROPRE (layer, cg_layer) - comme un clic sur son propre "OUT" pour
    le programme, ou reset_to_idle pour la preview - les autres slots ne
    sont pas touches.

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

    Restriction par sexe/categorie (restrict_to_sex/restrict_to_cat,
    optionnels, False par defaut) : DEUX conditions doivent etre reunies
    pour qu'un champ sexe/categorie soit envoye - le reglage PAR BOUTON
    (restrict_to_sex/restrict_to_cat dans son JSON) ET la case a cocher
    GLOBALE correspondante ("restreindre par sexe"/"par categorie",
    placee dans le corps de page via templates.json, voir interface.
    add_restrict_controls). Ni l'un ni l'autre seul ne suffit - meme un
    bouton bib_on doit explicitement activer restrict_to_sex/
    restrict_to_cat pour etre concerne (PAS de restriction automatique/
    inconditionnelle des que la case globale est cochee - exigence
    explicite de l'utilisateur, un bib_on peut tres bien ne PAS vouloir
    etre restreint meme quand la case globale est active pour d'autres
    boutons).
        - bib_on=True + restrict_to_sex/restrict_to_cat=True : PAS de
          combo box (ce bouton connait deja l'athlete selectionne) - son
          sexe/sa categorie reels (data.timing_data, C_SEX/C_CATEGORY)
          sont automatiquement ajoutes au payload (field_sex="sexe"/
          field_cat="cat" par defaut) des que la case globale
          correspondante est AUSSI active. La categorie est resolue vers
          son GROUPE (voir SceneButton._category_groups, configure dans
          Parametres) et envoyee jointe par ";" (ex: athlete "U21"
          regroupe avec SEN -> "U21;SEN"). Aucune valeur identifiee -> pas
          de restriction ("HD"/"ALL").
        - bib_on=False + restrict_to_sex/restrict_to_cat=True : affiche
          sous le bouton une combo box (Hommes/Dames/Hommes et Dames ; ou
          Toutes categories/un groupe) - MASQUEE tant que la case globale
          correspondante n'est pas active. La valeur choisie est envoyee
          telle quelle (field_sex/field_cat), poussee immediatement si
          deja affiche.
        - restrict_to_sex/restrict_to_cat=False (defaut) : ce bouton n'est
          JAMAIS concerne, quel que soit l'etat des cases globales - bib_on
          ou non.
    Activer/desactiver une case globale, ou changer le regroupement des
    categories dans Parametres, rafraichit et repousse INSTANTANEMENT
    tous les boutons CONCERNES (restrict_to_sex/restrict_to_cat=True) deja
    a l'ecran (voir set_restrict_by_sex/set_restrict_by_cat/
    set_category_groups).

    second_cg (optionnel, nom de template) declare un DEUXIEME graphique
    joue/stoppe/mis a jour EN MEME TEMPS que le principal. second_cg_layer
    est un LAYER VIDEO (comme "layer" pour le principal, PAS un cg_layer
    d'empilement) : omis, le second CG joue sur le meme layer video que
    le principal (en preview COMME au programme, les deux etant desormais
    symetriques) ; precise, il joue sur CE layer video a lui, independant
    du principal (2 producers CasparCG totalement separes, y compris en
    preview). L'index d'empilement CG interne (parametre "cg_layer" de la
    commande AMCP CG ADD) est TOUJOURS self.cg_layer + 1, jamais celui du
    principal - necessaire des que second_cg_layer est omis (les deux
    producers partagent alors le meme layer video, principal ET en
    preview) : un CG ADD vers un (adresse, cg_layer) deja occupe remplace
    silencieusement son producer, donc un cg_layer identique au principal
    ferait disparaitre celui-ci (deja rencontre en pratique). Recoit exactement
    le meme payload (bib/target/intermediaire/page/free_texts/ip) que le
    template principal a chaque play/Take/update/stop - pas de
    configuration de donnees separee. second_cg omis (None, par defaut) =
    pas de second CG. En cas d'echec du second CG (play/take/stop/update),
    le logiciel notifie l'erreur mais NE FAIT PAS echouer l'action sur le
    template principal (deja reussie a ce stade) - mieux vaut le
    graphique principal seul que rien du tout.
    """

    #registre de toutes les instances vivantes, utilise par reset_all()
    #(ex: apres un emergency all out) pour remettre tous les boutons a idle
    _instances: list["SceneButton"] = []

    #bouton qui occupe actuellement chaque (layer, cg_layer) de la
    #preview - meme principe que _active_pgm ci-dessous, la preview
    #n'etant plus un unique layer partage (voir docstring de classe)
    _active_preview: dict[tuple[int, int], "SceneButton"] = {}

    #bouton qui occupe actuellement chaque (layer, cg_layer) du programme
    _active_pgm: dict[tuple[int, int], "SceneButton"] = {}

    #dernier bouton joue/pris DEPUIS L'INTERFACE WEB (mis a jour dans
    #_play()/_take()) - le Stream Deck (voir streamdeck_controller.py) ne
    #lance plus lui-meme de graphique (demande explicite de l'utilisateur),
    #mais agit sur CELUI-CI pour mettre a jour en direct un dossard/une
    #cible ou tourner ses pages (s'il est multipage_on) : "je veux que le
    #bouton dossard et cible agissent sur la graphique qui est a l'antenne,
    #lance depuis l'interface web" / "un bouton next page et previus page
    #qui s'active pour le dernier graphique lu si il est multipage"
    _last_active: "SceneButton | None" = None

    #cible de saisie ACTIVE pour le pave numerique du Stream Deck : quand
    #une boite de dialogue ouverte a la souris (_ask_bib/_ask_target)
    #s'enregistre ici, chaque chiffre tape sur le Stream Deck est redirige
    #vers CETTE boite plutot que vers le flux dossard/cible sur le
    #graphique a l'antenne - exigence explicite : "quand j'ouvre une boite
    #de dialogue avec la souris je puisse rentrer avec le stream deck une
    #valeure dans l'interface web". None quand aucune boite compatible
    #n'est ouverte (etat par defaut, et ce sur quoi le Stream Deck retombe
    #au ferme de la boite - voir _ask_bib/_ask_target, bloc finally).
    _active_streamdeck_input: "StreamDeckInputTarget | None" = None

    #prefixe de cle correspondant a chaque intermediate_type, tous filtres
    #depuis data.config.interorder (intermediaires reellement en course,
    #voir _current_intermediate_options) - y compris "relay" : interorder
    #PEUT contenir C_RELAY1/2/3 si le format de course actuel en est un
    #(confirme par l'utilisateur - une premiere version supposait, a tort,
    #que "relay" en etait exclu et proposait une liste fixe codee en dur,
    #corrigee depuis)
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

    #etat GLOBAL des 2 cases a cocher "restreindre par sexe"/"par
    #categorie" (voir interface.add_restrict_controls) et regroupement des
    #categories (voir app_config.compute_category_groups, configure dans
    #Parametres) - partages par TOUS les boutons, comme _dark_mode.
    #_category_groups : liste de groupes (chacun une liste de categories),
    #ex: [["U15"], ["U17"], ["U19"], ["U21", "SEN"]] - vide tant que
    #interface.py ne l'a pas initialise au demarrage.
    _restrict_by_sex: bool = False
    _restrict_by_cat: bool = False
    _category_groups: list[list[str]] = []

    #active/desactive la restriction par sexe pour TOUTE l'interface :
    #rafraichit la visibilite de la combo box sexe de chaque bouton
    #restrict_to_sex (sans effet sur un bib_on, qui n'en a pas - voir
    #_build), et repousse IMMEDIATEMENT le payload (avec/sans le champ
    #sexe) de tout bouton restrict_to_sex=true deja a l'ecran (preview ou
    #programme), bib_on ou non - restrict_to_sex est desormais le SEUL
    #declencheur, y compris pour bib_on (voir docstring de classe : plus
    #de restriction automatique/inconditionnelle sur un bib_on sans ce
    #champ). Utilise par la case a cocher du corps de page (voir
    #interface.add_restrict_controls).
    @classmethod
    async def set_restrict_by_sex(cls, value: bool) -> None:
        cls._restrict_by_sex = value
        for button in list(cls._instances):
            button._refresh_restrict_visibility()
            if button._state != "idle" and button.restrict_to_sex:
                await button._update_data()

    #comme set_restrict_by_sex, pour la restriction par categorie
    @classmethod
    async def set_restrict_by_cat(cls, value: bool) -> None:
        cls._restrict_by_cat = value
        for button in list(cls._instances):
            button._refresh_restrict_visibility()
            if button._state != "idle" and button.restrict_to_cat:
                await button._update_data()

    #change le regroupement des categories pour TOUTE l'interface :
    #rafraichit les options de la combo box categorie de chaque bouton
    #restrict_to_cat SANS bib_on (voir _refresh_restrict_cat_options), et
    #repousse immediatement le payload de tout bouton restrict_to_cat=true
    #deja a l'ecran (bib_on ou non) SI la restriction par categorie est
    #actuellement active (sinon le regroupement n'a aucun effet visible,
    #inutile de repousser quoi que ce soit). Utilise par la boite de
    #dialogue Parametres du header.
    @classmethod
    async def set_category_groups(cls, groups: list[list[str]]) -> None:
        cls._category_groups = groups
        for button in list(cls._instances):
            if button.restrict_to_cat and not button.bib_on:
                button._refresh_restrict_cat_options()
            if not cls._restrict_by_cat:
                continue
            if button._state != "idle" and button.restrict_to_cat:
                await button._update_data()

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
        second_cg: str | None = None,
        second_cg_layer: int | None = None,
        restrict_to_sex: bool = False,
        field_sex: str = "sexe",
        restrict_to_cat: bool = False,
        field_cat: str = "cat",
        on_config_changed: Callable[[], None] | None = None,
    ):
        self.caspar = caspar
        self.data = data

        #client NiceGUI proprietaire de ce bouton (l'onglet navigateur qui
        #l'a construit) - conserve pour pouvoir re-entrer son contexte
        #(`with self.client:`) depuis un appelant EXTERIEUR a toute requete
        #NiceGUI, notamment le Stream Deck (voir streamdeck_controller.py) :
        #ui.notify() et consorts levent une RuntimeError si appeles depuis
        #une tache qui n'est pas rattachee a un client (pas de pile de
        #"slot" active, voir nicegui/context.py), ce qui est justement le
        #cas d'une commande declenchee par une touche physique plutot que
        #par un clic dans le navigateur.
        self.client = ui.context.client

        self.label = label
        self.template = template
        self.layer = layer
        self.cg_layer = cg_layer
        self.extra_data = extra_data or {}

        #graphique secondaire optionnel, joue/stoppe/mis a jour EN MEME
        #TEMPS que le template principal (voir _play/_take/_stop/
        #_update_data) ; None = pas de second CG. second_cg_layer est un
        #LAYER VIDEO (comme "layer" pour le principal), PAS un cg_layer
        #d'empilement - omis, il retombe sur le meme layer video que le
        #principal (self.layer) ; precise, le second CG joue sur ce layer
        #video a lui, independamment du principal (voir
        #_second_cg_video_layer). L'index d'empilement CG interne
        #(le parametre "cg_layer" de la commande AMCP CG ADD) reutilise
        #simplement self.cg_layer pour le second CG, pas de champ dedie -
        #inutile puisque les deux graphiques sont normalement sur des
        #layers video differents, donc pas de risque de collision.
        self.second_cg = second_cg
        self.second_cg_layer = second_cg_layer

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

        #restriction par sexe/categorie (voir docstring de classe) : opt-in
        #obligatoire, MEME pour bib_on (pas de restriction automatique
        #juste parce que la case globale est cochee) - bib_on=True calcule
        #directement sexe/categorie depuis l'athlete selectionne (pas de
        #combo box) ; bib_on=False affiche une combo box dediee
        self.restrict_to_sex = restrict_to_sex
        self.field_sex = field_sex
        self.restrict_to_cat = restrict_to_cat
        self.field_cat = field_cat

        #appele quand une option de free_text est ajoutee en direct (voir
        #_on_add_free_text_option), pour que l'appelant (interface.py)
        #persiste le changement dans templates.json - scene_button.py ne
        #sait pas ou/comment ecrire ce fichier, separation des
        #responsabilites oblige
        self._on_config_changed = on_config_changed

        self._state = "idle"  # "idle" | "preview" | "programme"
        self._page = 1
        #dernier payload envoye au second CG en preview, rejoue tel quel
        #vers le programme au Take (CasparCG._preview suit bien les DEUX
        #producers - indexes par (layer, cg_layer) - mais play_prw_to_pgm()
        #n'en reprend qu'UN a la fois, celui du principal demande par
        #_take() ; il faut donc suivre nous-memes celui du second CG pour
        #le rejouer separement)
        self._second_cg_payload: dict[str, str] | None = None
        self._bib: str | None = None
        self._bib_label: ui.label | None = None
        self._target: int | None = None
        self._target_label: ui.label | None = None
        self._free_text_widgets: dict[str, tuple[ui.select | None, ui.input]] = {}
        self._intermediate_select: ui.select | None = None
        self._intermediate_warning_icon: ui.icon | None = None
        self._format_warning_icon: ui.icon | None = None
        self._format_warning_tooltip: ui.tooltip | None = None
        self._sex_select: ui.select | None = None
        self._sex_row: ui.row | None = None
        self._cat_select: ui.select | None = None
        self._cat_row: ui.row | None = None
        self._box: ui.column | None = None
        self._button_container: ui.column | None = None

        self._build()
        SceneButton._instances.append(self)

    #affiche une ligne de log dans la console (stdout) pour une action liee
    #a CE bouton (play/take/stop/update/reset...) - le logiciel est
    #compile avec console=True precisement pour que ces logs restent
    #visibles (voir BiathControlCenter.spec/compiler.txt). Timestampe a la
    #seconde pres, comme les logs du serveur CasparCG (facilite la
    #correlation entre les deux quand on debogue un enchainement d'actions).
    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{self.label}] {message}")

    #remet ce bouton dans son etat de base (idle, sans dossard ni valeur
    #cible memorises), sans envoyer de commande AMCP - CasparCG est deja
    #vide au moment ou c'est utilise (apres un emergency all out)
    def reset_to_idle(self) -> None:
        was_active = self._state != "idle"

        self._state = "idle"
        self._bib = None
        self._target = None
        self._second_cg_payload = None
        key = (self.layer, self.cg_layer)
        if SceneButton._active_preview.get(key) is self:
            del SceneButton._active_preview[key]
        if SceneButton._active_pgm.get(key) is self:
            del SceneButton._active_pgm[key]
        self._refresh_appearance()

        #ne log que si le bouton n'etait pas deja idle - sinon reset_all()
        #(appele sur TOUS les boutons, meme ceux inactifs) noierait la
        #console de lignes "RESET" sans interet
        if was_active:
            self._log("RESET (remis a idle)")

    #remet TOUS les boutons existants a idle (utilise apres un emergency all out)
    @classmethod
    def reset_all(cls) -> None:
        print("=== EMERGENCY ALL OUT : remise a idle de tous les boutons ===")
        for button in cls._instances:
            button.reset_to_idle()

    #stoppe (CG STOP classique + auto-remove differe habituel, meme
    #mecanisme que le survol "OUT" de chaque bouton) tous les boutons
    #actuellement au programme, sans toucher a la preview - utilise par le
    #bouton ALL OUT du footer
    @classmethod
    async def stop_all_pgm(cls) -> None:
        print("=== ALL OUT : arret de tous les graphiques au programme ===")
        for button in list(cls._instances):
            if button._state == "programme":
                await button._stop()

    #invoque une fonction/methode exposee par le template (CG INVOKE, voir
    #casparcg.py) sur TOUS les boutons actuellement au programme, second_cg
    #compris s'il y en a - utilise par le bouton SPONSOR IN du footer
    #(fonction "sponsor_in", voir interface.build_footer), ecrit de facon
    #GENERIQUE (le nom de la fonction est un parametre, pas cable en dur)
    #pour rester reutilisable si d'autres boutons du meme type sont
    #ajoutes plus tard.
    @classmethod
    async def invoke_all_pgm(cls, function: str) -> None:
        buttons = [button for button in cls._instances if button._state == "programme"]
        if not buttons:
            ui.notify("Aucun graphique a l'antenne", type="warning")
            return

        print(f"=== INVOKE '{function}' sur {len(buttons)} graphique(s) au programme ===")
        for button in buttons:
            response = await button.caspar.invoke_template(
                "pgm", function, layer=button.layer, cg_layer=button.cg_layer
            )
            if response.is_error:
                button._log(f"INVOKE '{function}' : ERREUR - {response.message}")
                ui.notify(f"Erreur INVOKE sur '{button.label}' : {response.message}", type="negative")
            else:
                button._log(f"INVOKE '{function}' OK (layer={button.layer}, cg_layer={button.cg_layer})")

            if button.second_cg:
                second_response = await button.caspar.invoke_template(
                    "pgm",
                    function,
                    layer=button._second_cg_video_layer(),
                    cg_layer=button._second_cg_stack_layer(),
                )
                if second_response.is_error:
                    button._log(
                        f"INVOKE '{function}' second CG '{button.second_cg}' : "
                        f"ERREUR - {second_response.message}"
                    )
                    ui.notify(
                        f"Erreur INVOKE second CG sur '{button.label}' : "
                        f"{second_response.message}",
                        type="negative",
                    )

    #clear (immediat, sans animation) TOUS les slots de preview actuellement
    #occupes (cote CasparCG, layer principal + layer du second_cg s'il est
    #distinct) et remet chacun des boutons concernes a idle. Utilise par le
    #bouton CLEAR PRW du footer. Renvoie la liste des messages d'erreur
    #rencontres (vide = tout s'est bien passe) pour que l'appelant notifie.
    #
    #NOTE : ne clear QUE les layers suivis localement comme actifs (voir
    #_active_preview) - contrairement a l'ancien design (un seul layer de
    #preview partage, toujours clearable a l'aveugle), on ne peut plus
    #deviner quels layers pourraient etre desynchronises sans les connaitre.
    #En cas de desync reelle (ex: apres un redemarrage du process avec du
    #contenu deja charge cote CasparCG), utiliser ALL OUT/emergency_all_out
    #(clear() sans layer, qui vide le channel preview en entier).
    @classmethod
    async def clear_all_previews(cls) -> list[str]:
        print("=== CLEAR PRW : vidage de tous les slots de preview actifs ===")
        errors: list[str] = []

        for button in list(cls._active_preview.values()):
            response = await button.caspar.clear("prw", layer=button.layer)
            if response.is_error:
                errors.append(response.message)

            if button.second_cg:
                second_layer = button._second_cg_video_layer()
                if second_layer != button.layer:
                    second_response = await button.caspar.clear("prw", layer=second_layer)
                    if second_response.is_error:
                        errors.append(second_response.message)

            button.reset_to_idle()

        return errors

    #envoie au programme TOUS les graphiques actuellement en preview -
    #reutilise simplement le Take normal de chaque bouton qui occupe un
    #slot de preview (stop avec animation de sortie de ce qui etait deja
    #au programme sur son propre layer/cg_layer, puis play_prw_to_pgm).
    #Ne fait rien (avec notification) si rien n'est en preview. Utilise
    #par le bouton TAKE PREVIEW du footer.
    @classmethod
    async def take_all_previews(cls) -> None:
        buttons = list(cls._active_preview.values())
        if not buttons:
            ui.notify("Aucun graphique en preview", type="warning")
            return

        print(f"=== TAKE PREVIEW : envoi de {len(buttons)} graphique(s) au programme ===")
        for button in buttons:
            await button._take()

    #comme take_all_previews, mais renvoie EN PLUS en preview le graphique
    #au programme echange avec chaque preview (vrai echange de position,
    #pas juste un stop) - son payload actuel (bib/target/intermediaire/
    #page/free_texts) est capture AVANT de le stopper, pour etre rejoue a
    #l'identique en preview ensuite, second_cg compris (voir plus bas -
    #bug reel rencontre : le second_cg de l'ancien pgm_button n'etait pas
    #renvoye en preview, donc plus jamais rejoue au programme lors d'un
    #swap/Take suivant sur ce bouton). Un preview sans pgm a echanger est
    #simplement pris (comme take_all_previews). Utilise par le bouton SWAP
    #du footer.
    #
    #APPARIEMENT : un swap se fait normalement entre deux boutons
    #DIFFERENTS (types de graphiques differents, donc layers CasparCG
    #differents la plupart du temps) - apparier par (layer, cg_layer)
    #IDENTIQUE ne marcherait donc presque jamais (bug reel rencontre : le
    #graphique au pgm restait en place, jamais renvoye en preview, faute
    #de correspondance de layer). On apparie donc en 2 passes : d'abord un
    #eventuel appariement EXACT par (layer, cg_layer) (cas rare ou un
    #bouton reprend son propre slot), puis les preview et pgm restants
    #sont apparies dans l'ordre - avec un seul graphique de chaque cote
    #(usage typique), le resultat est sans ambiguite.
    @classmethod
    async def swap_preview_pgm(cls) -> None:
        preview_buttons = list(cls._active_preview.values())
        if not preview_buttons:
            ui.notify("Aucun graphique en preview", type="warning")
            return

        print(f"=== SWAP : echange de {len(preview_buttons)} graphique(s) preview/programme ===")
        remaining_pgm = list(cls._active_pgm.values())
        pairing: dict["SceneButton", "SceneButton | None"] = {}

        for preview_button in preview_buttons:
            exact = cls._active_pgm.get((preview_button.layer, preview_button.cg_layer))
            if exact is not None and exact in remaining_pgm:
                remaining_pgm.remove(exact)
                pairing[preview_button] = exact
            else:
                pairing[preview_button] = None

        for preview_button in preview_buttons:
            if pairing[preview_button] is None and remaining_pgm:
                pairing[preview_button] = remaining_pgm.pop(0)

        for preview_button in preview_buttons:
            pgm_button = pairing[preview_button]

            pgm_payload = None
            if pgm_button is not None and pgm_button is not preview_button:
                pgm_payload = await pgm_button._build_payload(ask_bib=False)
                await pgm_button._stop()

            await preview_button._take()

            if pgm_button is not None and pgm_payload is not None:
                response = await pgm_button.caspar.play_template(
                    "prw",
                    pgm_button.template,
                    data=pgm_payload,
                    layer=pgm_button.layer,
                    cg_layer=pgm_button.cg_layer,
                )
                if response.is_success:
                    pgm_button._state = "preview"
                    cls._active_preview[(pgm_button.layer, pgm_button.cg_layer)] = pgm_button
                    pgm_button._refresh_appearance()
                    pgm_button._log(
                        f"SWAP : renvoye en PREVIEW (layer={pgm_button.layer}, "
                        f"cg_layer={pgm_button.cg_layer}) OK"
                    )

                    #renvoyer aussi le second_cg de l'ancien pgm_button en
                    #preview (meme payload) - sinon il reste absent de la
                    #preview et _second_cg_payload reste vide (efface par
                    #_stop() ci-dessus), donc un futur Take/swap de ce
                    #bouton ne le rejouerait plus jamais au programme
                    if pgm_button.second_cg:
                        second_response = await pgm_button.caspar.play_template(
                            "prw",
                            pgm_button.second_cg,
                            data=pgm_payload,
                            layer=pgm_button._second_cg_video_layer(),
                            cg_layer=pgm_button._second_cg_stack_layer(),
                        )
                        if second_response.is_success:
                            pgm_button._second_cg_payload = pgm_payload
                        else:
                            pgm_button._log(
                                f"SWAP : erreur second CG en preview - {second_response.message}"
                            )
                            ui.notify(
                                f"Erreur lors du swap du second CG : {second_response.message}",
                                type="negative",
                            )
                else:
                    pgm_button._log(f"SWAP : erreur renvoi en preview - {response.message}")
                    ui.notify(
                        f"Erreur lors du swap (renvoi en preview) : {response.message}",
                        type="negative",
                    )

    #construit l'element visuel : bouton (+ fleche multipage + rectangle
    #survol), eventuel affichage/changement d'athlete (bib_on), eventuel
    #affichage/changement de valeur cible (target_on), eventuelle combo box
    #d'intermediaire (intermediate_on) et eventuels champs de saisie libre.
    #S'il y a quelque chose sous le bouton, le tout est regroupe dans une
    #meme "box" bordee (pour montrer visuellement que c'est lie) ; sinon,
    #pas de cadre (le bouton seul n'a rien a montrer comme "lie"). Les
    #icones d'alerte (connexion + format de course, voir _build_button)
    #restent a cote du BOUTON dans les deux cas, jamais de la box entiere
    #meme quand elle est agrandie par les champs en dessous. La box n'a
    #pas de largeur propre : elle se dimensionne sur son enfant le plus
    #large (le bouton, w-64) et les champs (w-full) se calent dessus, donc
    #ne peuvent jamais depasser la largeur du bouton.
    def _build(self) -> None:
        #restrict_to_sex/restrict_to_cat n'ajoutent une combo box que pour
        #les boutons SANS selection d'athlete - un bib_on n'en a pas besoin
        #(voir docstring de classe)
        has_restrict_selects = not self.bib_on and (self.restrict_to_sex or self.restrict_to_cat)

        with ui.row().classes("items-start gap-2"):
            if (
                self.bib_on
                or self.target_on
                or self.intermediate_on
                or self.free_texts
                or has_restrict_selects
            ):
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
                    if has_restrict_selects:
                        self._build_restrict_selects()
                self._refresh_box_style()
            else:
                self._build_button()

        ui.timer(1.0, self._refresh_connection_warning)
        if self.intermediate_on:
            ui.timer(1.0, self._refresh_intermediate_options)
            #en plus du polling ci-dessus (jusqu'a 1s de latence), se
            #rafraichit INSTANTANEMENT des qu'un nouveau message websocket
            #complet arrive (voir BiathDataWebsocket.on_message) - utile
            #en particulier si le logiciel de donnees demarre APRES ce
            #logiciel : le tout premier message peut alors arriver a
            #n'importe quel moment, pas forcement aligne sur un tick
            self._subscribe_to_new_data(self._refresh_intermediate_options)
        if self.race_formats is not None:
            ui.timer(1.0, self._refresh_format_availability)
            self._subscribe_to_new_data(self._refresh_format_availability)

    #abonne callback aux nouveaux messages websocket complets (voir
    #BiathDataWebsocket.on_message) et se desabonne automatiquement quand
    #CE client (cet onglet/cette session navigateur precise) est
    #definitivement ferme - sans ca, data etant un singleton PARTAGE par
    #tous les clients (voir main.py) alors que ce bouton est recree a
    #chaque chargement de page, chaque rechargement de l'onglet
    #accumulerait un abonnement supplementaire au registre partage, sans
    #jamais retirer les anciens (fuite, voir BiathDataWebsocket.
    #remove_listener)
    def _subscribe_to_new_data(self, callback: Callable[[], None]) -> None:
        self.data.on_message(callback)
        ui.context.client.on_delete(lambda: self.data.remove_listener(callback))

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
        #memorise le conteneur pour pouvoir lui ajouter/retirer une marge
        #a droite selon que les icones d'alerte sont visibles ou non (voir
        #_refresh_warning_spacing) - demande explicite de l'utilisateur :
        #"si il n'y a pas d'icone d'erreur entre les boutons, faire en
        #sorte que les boutons soient plus proches". La grille (interface.
        #build_body) garde un petit gap uniforme ; c'est CETTE marge,
        #ajoutee/retiree PAR BOUTON selon son propre etat, qui reserve (ou
        #non) la place des icones - un bouton sans aucune icone visible ne
        #pousse donc pas la colonne suivante plus loin que necessaire.
        self._button_container = ui.column().classes("relative group inline-flex gap-0 w-64")
        with self._button_container:
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

            #icones d'alerte (connexion + format de course indisponible)
            #construites ICI (dans le meme conteneur "relative" que le
            #bouton, pas dans la box englobante) pour se positionner PAR
            #RAPPORT AU BOUTON SEUL : a droite, centrees sur SA hauteur a
            #lui, meme si la box englobante contient d'autres champs (bib/
            #cible/intermediaire/free_texts) qui l'agrandissent en dessous
            #- correction explicite demandee : l'icone de connexion,
            #placee auparavant en simple voisine flex (items-start) dans
            #_build(), restait pincee en HAUT d'une box agrandie au lieu
            #de suivre le bouton. Regroupees dans un seul conteneur absolu
            #commun pour ne jamais se chevaucher meme si les deux sont
            #visibles a la fois (ex: deconnecte ET mauvais format de course).
            with ui.row().classes(
                "absolute left-full top-1/2 -translate-y-1/2 ml-2 items-center gap-2"
            ):
                self._build_connection_warning()
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
                        #supprime l'option ACTUELLEMENT SELECTIONNEE dans la
                        #combo box (pas un dialogue de gestion separe avec
                        #une liste - reutilise la selection deja faite par
                        #l'utilisateur, plus rapide) - demande explicite :
                        #pouvoir supprimer une entree enregistree
                        remove_icon = ui.icon("remove_circle").classes(
                            "text-slate-500 hover:text-red-600 cursor-pointer text-lg"
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
                    remove_icon.on(
                        "click",
                        lambda _, ft=free_text, sel=select_widget: (
                            self._on_remove_free_text_option(ft, sel)
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

    #supprime DEFINITIVEMENT une option enregistree d'un free_text a combo
    #box (demande explicite de l'utilisateur) - agit sur l'option
    #actuellement selectionnee dans select_widget. Confirmation demandee
    #avant suppression (action peu reversible - contrairement a l'ajout,
    #qui ne peut pas faire perdre de donnee) via un petit dialogue, meme
    #style que _on_add_free_text_option.
    async def _on_remove_free_text_option(
        self, free_text: dict, select_widget: ui.select
    ) -> None:
        value = select_widget.value
        if not value:
            ui.notify("Selectionnez d'abord une option a supprimer", type="warning")
            return

        with ui.dialog() as dialog, ui.card():
            ui.label("Supprimer cette option ?").classes("text-base font-bold")
            ui.label(f"'{value}' sera definitivement retiree de la liste.").classes(
                "text-sm text-slate-500"
            )
            with ui.row():
                ui.button("Annuler", on_click=lambda: dialog.submit(False))
                ui.button(
                    "Supprimer", color="red", on_click=lambda: dialog.submit(True)
                )

        confirmed = await dialog
        if not confirmed:
            return

        options = free_text.setdefault("options", [])
        if value in options:
            options.remove(value)

        select_widget.options = list(options)
        select_widget.value = None
        select_widget.update()

        if self._on_config_changed is not None:
            self._on_config_changed()

        ui.notify(f"Option '{value}' supprimee", type="positive")

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
    #cle (utilisee par CasparCG), affiche le label. "relay" utilise le
    #MEME filtrage dynamique que "shoot_out"/"slope", pas de cas
    #particulier - voir section "Bouton intermediaire" de
    #PROJECT_CONTEXT.txt : une liste FIXE C_RELAY1..3 codee en dur a deja
    #ete essayee et rejetee (interorder peut reellement contenir
    #C_RELAY1/2/3 en course de relais, contrairement a la supposition
    #initiale) - a ne pas reintroduire.
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

    #construit, sous le bouton, les combo box de restriction sexe/
    #categorie (restrict_to_sex/restrict_to_cat) - uniquement appelee pour
    #les boutons SANS selection d'athlete (voir _build/docstring de
    #classe). Chaque combo box est masquee tant que la case a cocher
    #globale correspondante n'est pas active (voir
    #_refresh_restrict_visibility, appelee en fin de methode), pour ne pas
    #encombrer l'interface d'un reglage momentanement sans effet. "dense
    #options-dense" (props Quasar) comme les autres combo box du bouton.
    def _build_restrict_selects(self) -> None:
        if self.restrict_to_sex:
            self._sex_row = ui.row().classes("w-full items-center gap-1")
            with self._sex_row:
                self._sex_select = (
                    ui.select(
                        options={"HD": "Hommes et Dames", "H": "Hommes", "D": "Dames"},
                        value="HD",
                        label="Sexe",
                    )
                    .classes("flex-1")
                    .props("dense options-dense")
                )
            self._sex_select.on_value_change(self._on_restrict_select_change)

        if self.restrict_to_cat:
            self._cat_row = ui.row().classes("w-full items-center gap-1")
            with self._cat_row:
                self._cat_select = (
                    ui.select(
                        options=self._category_group_options(),
                        value="ALL",
                        label="Categorie",
                    )
                    .classes("flex-1")
                    .props("dense options-dense")
                )
            self._cat_select.on_value_change(self._on_restrict_select_change)

        self._refresh_restrict_visibility()

    #pousse la nouvelle valeur choisie si le graphique est deja affiche
    async def _on_restrict_select_change(self, _event) -> None:
        if self._state != "idle":
            await self._update_data()

    #montre/masque les combo box sexe/categorie selon l'etat GLOBAL
    #courant des 2 cases a cocher (voir set_restrict_by_sex/
    #set_restrict_by_cat) - PAS selon un etat propre a ce bouton, elles
    #suivent toutes le meme interrupteur partage
    def _refresh_restrict_visibility(self) -> None:
        if self._sex_row is not None:
            self._sex_row.visible = SceneButton._restrict_by_sex
        if self._cat_row is not None:
            self._cat_row.visible = SceneButton._restrict_by_cat

    #options de la combo box categorie : "ALL" (aucune restriction) + un
    #groupe par entree de SceneButton._category_groups (valeur envoyee au
    #template = categories du groupe jointes par ";", ex: "U21;SEN" ;
    #libelle affiche = jointes par " + ", ex: "U21 + SEN")
    def _category_group_options(self) -> dict[str, str]:
        options = {"ALL": "Toutes categories"}
        for group in SceneButton._category_groups:
            options[";".join(group)] = " + ".join(group)
        return options

    #resynchronise les options de la combo box categorie depuis
    #SceneButton._category_groups (peut changer en direct, voir
    #set_category_groups) - meme principe que _refresh_intermediate_options :
    #preserve la selection actuelle si toujours valide, la reinitialise a
    #"ALL" sinon
    def _refresh_restrict_cat_options(self) -> None:
        if self._cat_select is None:
            return

        options = self._category_group_options()
        if options != self._cat_select.options:
            current_value = self._cat_select.value
            self._cat_select.options = options
            if current_value not in options:
                self._cat_select.value = "ALL"
            self._cat_select.update()

    #valeur du champ sexe pour l'athlete du dossard donne, a partir de
    #data.timing_data[bib].C_SEX (1=homme, 2=femme - confirme par
    #l'utilisateur, voir biathdata_reference.txt). "HD" (pas de
    #restriction) si l'athlete ou son sexe est introuvable - mieux vaut ne
    #pas filtrer que filtrer sur une valeur devinee a tort.
    def _athlete_sex_value(self, bib: str) -> str:
        athlete = self.data.timing_data.get(bib)
        if athlete is None:
            return "HD"

        sex = getattr(athlete, "C_SEX", None)
        if sex in (1, "1"):
            return "H"
        if sex in (2, "2"):
            return "D"
        return "HD"

    #valeur du champ categorie pour l'athlete du dossard donne, a partir
    #de data.timing_data[bib].C_CATEGORY, resolue vers son GROUPE de
    #categories (voir SceneButton._category_groups) - ex: athlete "U21"
    #regroupe avec SEN -> "U21;SEN". "ALL" (pas de restriction) si
    #l'athlete ou sa categorie est introuvable ; categorie brute seule si
    #elle n'appartient a aucun groupe connu (ex: valeur inattendue dans
    #les donnees recues).
    def _athlete_cat_value(self, bib: str) -> str:
        athlete = self.data.timing_data.get(bib)
        if athlete is None:
            return "ALL"

        category = getattr(athlete, "C_CATEGORY", None)
        if not category:
            return "ALL"

        for group in SceneButton._category_groups:
            if category in group:
                return ";".join(group)

        return category

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
    #pas dans race_formats. Le positionnement (absolu, centre verticalement
    #sur le bouton) est porte par le conteneur commun dans _build_button(),
    #pas par cette icone elle-meme (voir son commentaire) - ne garder ici
    #que le style propre a l'icone.
    def _build_format_warning(self) -> None:
        self._format_warning_icon = ui.icon("block").classes(
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

        self._refresh_warning_spacing()
        self._refresh_appearance()

    #icone d'alerte affichee a droite du bouton en cas de deconnexion - le
    #positionnement (absolu, centre verticalement sur le bouton) est porte
    #par le conteneur commun dans _build_button(), voir son commentaire
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

        self._refresh_warning_spacing()

    #reserve (ou libere) la place des icones d'alerte a droite du bouton :
    #une marge fixe si AU MOINS UNE des 2 icones (connexion/format) est
    #actuellement visible, aucune marge sinon - evite que la grille
    #(interface.build_body, gap uniforme et modeste) ait a reserver cette
    #place pour TOUS les boutons meme quand ils n'ont jamais d'icone.
    #Appelee par les 2 fonctions qui changent la visibilite d'une icone
    #(_refresh_format_availability/_refresh_connection_warning), jamais
    #l'inverse - chacune ne connait que SON PROPRE etat, celle-ci combine
    #les deux.
    def _refresh_warning_spacing(self) -> None:
        if self._button_container is None:
            return

        any_icon_visible = (
            self._warning_icon is not None and self._warning_icon.visible
        ) or (
            self._format_warning_icon is not None and self._format_warning_icon.visible
        )

        if any_icon_visible:
            self._button_container.classes(add="mr-20", remove="mr-0")
        else:
            self._button_container.classes(add="mr-0", remove="mr-20")

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

    #revient directement a la page 1 - meme structure que _on_next_page/
    #_on_prev_page (aucune fleche dediee cote interface web pour ce saut
    #direct, exigence explicite du Stream Deck uniquement, voir
    #streamdeck_controller.py : "un bouton go to page 1")
    async def _goto_first_page(self) -> None:
        if not self._is_format_available():
            return
        if self._page == 1:
            return
        self._page = 1
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
            #bib_on n'a pas de combo box sexe/categorie dediee, il calcule
            #directement depuis l'athlete selectionne (voir docstring de
            #classe et _athlete_sex_value/_athlete_cat_value) - MAIS
            #seulement si restrict_to_sex/restrict_to_cat est active dans
            #le JSON de ce bouton (comme pour un bouton sans athlete), en
            #plus de la case a cocher globale : bib_on n'a plus de
            #restriction automatique inconditionnelle, il faut l'activer
            #explicitement bouton par bouton.
            if self.restrict_to_sex and SceneButton._restrict_by_sex:
                payload[self.field_sex] = self._athlete_sex_value(self._bib)
            if self.restrict_to_cat and SceneButton._restrict_by_cat:
                payload[self.field_cat] = self._athlete_cat_value(self._bib)

        if self.target_on and self._target is not None:
            payload[self.field_target] = str(self._target)

        for field, (select_widget, input_widget) in self._free_text_widgets.items():
            value = (select_widget.value if select_widget else None) or input_widget.value
            payload[field] = str(value) if value else ""

        #sans selection d'athlete (bib_on) : combo box dediee, uniquement
        #si sa case a cocher globale correspondante est active (voir
        #_refresh_restrict_visibility - la combo box peut exister mais
        #etre masquee/inactive)
        if not self.bib_on:
            if self.restrict_to_sex and SceneButton._restrict_by_sex and self._sex_select is not None:
                payload[self.field_sex] = self._sex_select.value or "HD"
            if self.restrict_to_cat and SceneButton._restrict_by_cat and self._cat_select is not None:
                payload[self.field_cat] = self._cat_select.value or "ALL"

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
            self._log(f"PLAY {channel.upper()} : ERREUR - {response.message}")
            ui.notify(f"Erreur lors du play : {response.message}", type="negative")
            return

        self._log(
            f"PLAY {'PREVIEW' if channel == 'prw' else 'PROGRAMME'} "
            f"(template={self.template}, layer={self.layer}, cg_layer={self.cg_layer}) "
            f"OK - donnees : {payload}"
        )

        await self._play_second_cg(channel, payload)

        self._state = "preview" if channel == "prw" else "programme"
        if channel == "prw":
            SceneButton._active_preview[(self.layer, self.cg_layer)] = self
        else:
            SceneButton._active_pgm[(self.layer, self.cg_layer)] = self
        #dernier bouton joue/pris DEPUIS L'INTERFACE WEB (voir aussi
        #_take() plus bas) - le Stream Deck agit sur CE bouton pour ses
        #propres actions DOSSARD/CIBLE (mise a jour d'un graphique deja a
        #l'antenne) et la pagination (GOTO PAGE 1/PRECEDENTE/SUIVANTE), a
        #la place de lancer lui-meme des graphiques (voir
        #streamdeck_controller.py - demande explicite de l'utilisateur :
        #"je ne veux pas les lancer avec le stream deck")
        SceneButton._last_active = self
        self._refresh_appearance()

        if self.target_on:
            target = await self._ask_target()
            if target is not None:
                self._target = target
                self._refresh_appearance()
                await self._update_data()

    #force le (layer, cg_layer) de preview vise a n'avoir qu'un seul
    #occupant : remet a idle le bouton qui l'occupait avant (s'il y en a
    #un), puis clear ce layer de preview cote CasparCG (inconditionnel,
    #meme si le suivi local est desynchronise, ex: apres un redemarrage du
    #process) - les AUTRES slots de preview (d'autres boutons, sur
    #d'autres layers) ne sont pas touches
    async def _claim_preview(self) -> None:
        previous = SceneButton._active_preview.get((self.layer, self.cg_layer))
        if previous is not None and previous is not self:
            previous.reset_to_idle()

        await self.caspar.clear("prw", layer=self.layer)

    #force le (layer, cg_layer) programme vise a n'avoir qu'un seul
    #occupant : stoppe (comme un clic sur son propre "OUT") le bouton qui
    #l'occupait avant, s'il s'agit d'un autre bouton
    async def _claim_pgm_slot(self) -> None:
        previous = SceneButton._active_pgm.get((self.layer, self.cg_layer))
        if previous is not None and previous is not self:
            await previous._stop()

    #layer video cible du second CG : self.second_cg_layer s'il est
    #precise, sinon le meme layer que le principal (self.layer). S'applique
    #IDENTIQUEMENT en preview et au programme (les deux sont symetriques,
    #voir docstring de classe) : precise, le second CG previse/joue sur son
    #propre layer, totalement independant de celui du principal.
    def _second_cg_video_layer(self) -> int:
        return self.second_cg_layer if self.second_cg_layer is not None else self.layer

    #index d'empilement CG du second CG : self.cg_layer + 1, TOUJOURS
    #different du cg_layer principal (self.cg_layer). Necessaire des que
    #second_cg_layer est omis (par defaut) : principal et second se
    #retrouvent alors sur le MEME layer video (self.layer), en preview
    #COMME au programme - un CG ADD vers un (adresse, cg_layer) deja
    #occupe remplace silencieusement son producer, donc un cg_layer
    #identique au principal ferait disparaitre celui-ci. Reste sans risque
    #(mais harmless) quand second_cg_layer est precise sur un layer video
    #different, ou les deux adresses ne se recoupent de toute facon jamais.
    def _second_cg_stack_layer(self) -> int:
        return self.cg_layer + 1

    #joue le second CG (si configure, voir second_cg/second_cg_layer),
    #avec le meme payload que le principal. En echec,
    #notifie mais NE FAIT PAS echouer le play principal (deja reussi a ce
    #stade) - mieux vaut le graphique principal seul que rien du tout. En
    #preview, memorise le payload envoye pour le rejouer a l'identique
    #vers le programme au Take (voir _take) : CasparCG._preview suit bien
    #ce producer aussi (indexe par son propre (layer, cg_layer)), mais
    #play_prw_to_pgm() ne reprend qu'UN (layer, cg_layer) a la fois, celui
    #qu'on lui demande - il faut donc rejouer le second CG nous-memes en
    #plus.
    async def _play_second_cg(self, channel: str, payload: dict[str, str]) -> None:
        if not self.second_cg:
            return

        response = await self.caspar.play_template(
            channel,
            self.second_cg,
            data=payload,
            layer=self._second_cg_video_layer(),
            cg_layer=self._second_cg_stack_layer(),
        )

        if response.is_error:
            self._log(f"PLAY {channel.upper()} second CG '{self.second_cg}' : ERREUR - {response.message}")
            ui.notify(f"Erreur lors du play du second CG : {response.message}", type="negative")
            return

        self._log(
            f"PLAY {'PREVIEW' if channel == 'prw' else 'PROGRAMME'} second CG "
            f"'{self.second_cg}' (layer={self._second_cg_video_layer()}, "
            f"cg_layer={self._second_cg_stack_layer()}) OK"
        )

        if channel == "prw":
            self._second_cg_payload = payload

    async def _take(self) -> None:
        await self._claim_pgm_slot()

        response = await self.caspar.play_prw_to_pgm(self.layer, cg_layer=self.cg_layer)

        if response.is_error:
            self._log(f"TAKE : ERREUR - {response.message}")
            ui.notify(f"Erreur lors du take : {response.message}", type="negative")
            return

        self._log(
            f"TAKE (preview -> programme, layer={self.layer}, cg_layer={self.cg_layer}) OK"
        )

        #play_prw_to_pgm() ci-dessus n'a repris que le cg_layer principal
        #(cg_layer=self.cg_layer) : le second CG doit etre rejoue a la
        #main, avec le payload memorise lors du dernier play/update en
        #preview (voir _play_second_cg/_update_data)
        if self.second_cg and self._second_cg_payload is not None:
            second_response = await self.caspar.play_template(
                "pgm",
                self.second_cg,
                data=self._second_cg_payload,
                layer=self._second_cg_video_layer(),
                cg_layer=self._second_cg_stack_layer(),
            )
            if second_response.is_error:
                self._log(f"TAKE second CG '{self.second_cg}' : ERREUR - {second_response.message}")
                ui.notify(
                    f"Erreur lors du take du second CG : {second_response.message}",
                    type="negative",
                )
            else:
                self._log(
                    f"TAKE second CG '{self.second_cg}' (layer={self._second_cg_video_layer()}, "
                    f"cg_layer={self._second_cg_stack_layer()}) OK"
                )

            #si le second CG previse sur son PROPRE layer (second_cg_layer
            #different du principal), le clear ci-dessus (dans
            #play_prw_to_pgm, qui ne cible que self.layer) ne l'a pas
            #efface : le clearer nous-memes pour ne pas le laisser visible
            #en preview alors qu'il est deja repris au programme
            second_video_layer = self._second_cg_video_layer()
            if second_video_layer != self.layer:
                await self.caspar.clear("prw", layer=second_video_layer)

        self._state = "programme"
        SceneButton._active_pgm[(self.layer, self.cg_layer)] = self
        SceneButton._last_active = self
        key = (self.layer, self.cg_layer)
        if SceneButton._active_preview.get(key) is self:
            del SceneButton._active_preview[key]
        self._refresh_appearance()

    async def _stop(self) -> None:
        response = await self.caspar.stop("pgm", layer=self.layer, cg_layer=self.cg_layer)

        if response.is_error:
            self._log(f"STOP : ERREUR - {response.message}")
            ui.notify(f"Erreur lors du stop : {response.message}", type="negative")
            return

        self._log(f"STOP (programme, layer={self.layer}, cg_layer={self.cg_layer}) OK")

        #stoppe (avec animation de sortie) le second CG en meme temps que
        #le principal - au mieux, ne fait pas echouer le stop principal
        if self.second_cg:
            second_response = await self.caspar.stop(
                "pgm", layer=self._second_cg_video_layer(), cg_layer=self._second_cg_stack_layer()
            )
            if second_response.is_error:
                self._log(f"STOP second CG '{self.second_cg}' : ERREUR - {second_response.message}")
                ui.notify(
                    f"Erreur lors du stop du second CG : {second_response.message}",
                    type="negative",
                )
            else:
                self._log(f"STOP second CG '{self.second_cg}' OK")

        self._state = "idle"
        self._bib = None
        self._target = None
        self._second_cg_payload = None
        if SceneButton._active_pgm.get((self.layer, self.cg_layer)) is self:
            del SceneButton._active_pgm[(self.layer, self.cg_layer)]
        self._refresh_appearance()

    #renvoie les donnees actuelles (page/bib/target/intermediaire/ip/free_texts)
    #au graphique deja en cours, et au second CG le cas echeant (meme
    #payload, memorise a nouveau si en preview pour un futur Take)
    async def _update_data(self) -> None:
        channel = "prw" if self._state == "preview" else "pgm"

        payload = await self._build_payload(ask_bib=False)
        if payload is None:
            return

        response = await self.caspar.update_template(
            channel, payload, layer=self.layer, cg_layer=self.cg_layer
        )

        if response.is_error:
            self._log(f"UPDATE {channel.upper()} : ERREUR - {response.message}")
            ui.notify(f"Erreur lors de la mise a jour : {response.message}", type="negative")
        else:
            self._log(f"UPDATE {channel.upper()} OK - donnees : {payload}")

        if self.second_cg:
            second_response = await self.caspar.update_template(
                channel,
                payload,
                layer=self._second_cg_video_layer(),
                cg_layer=self._second_cg_stack_layer(),
            )
            if second_response.is_error:
                self._log(
                    f"UPDATE {channel.upper()} second CG '{self.second_cg}' : "
                    f"ERREUR - {second_response.message}"
                )
                ui.notify(
                    f"Erreur lors de la mise a jour du second CG : {second_response.message}",
                    type="negative",
                )
            if channel == "prw":
                self._second_cg_payload = payload

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

        #enregistre cette boite comme cible du pave numerique du Stream
        #Deck (voir StreamDeckInputTarget/_active_streamdeck_input plus
        #haut) : un chiffre tape sur le Stream Deck s'ajoute a la
        #recherche (set_value(), pas une simple affectation, pour bien
        #redeclencher on_change/refresh() - meme piege que le "+"/"-" de
        #free_text, voir _on_add_free_text_option), EFFACER la vide,
        #VALIDER equivaut a confirm_selected(). Desenregistree dans TOUS
        #les cas de fermeture (finally) - jamais laissee active sur une
        #boite qui n'existe plus.
        SceneButton._active_streamdeck_input = StreamDeckInputTarget(
            append_digit=lambda digit: search_input.set_value((search_input.value or "") + str(digit)),
            clear=lambda: search_input.set_value(""),
            submit=confirm_selected,
        )
        try:
            return await dialog
        finally:
            SceneButton._active_streamdeck_input = None

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

        #meme principe d'enregistrement que _ask_bib ci-dessus (voir son
        #commentaire) - le buffer recommence a 0 des qu'il atteindrait 3
        #chiffres (la cible est bornee a 30, jamais besoin de plus de 2)
        def sd_append_digit(digit: int) -> None:
            current = "" if number.value is None else str(int(number.value))
            current = str(digit) if len(current) >= 2 else current + str(digit)
            number.set_value(int(current))

        SceneButton._active_streamdeck_input = StreamDeckInputTarget(
            append_digit=sd_append_digit,
            clear=lambda: number.set_value(None),
            submit=lambda: dialog.submit(number.value),
        )
        try:
            value = await dialog
        finally:
            SceneButton._active_streamdeck_input = None

        if value is None:
            return None

        return int(value)
