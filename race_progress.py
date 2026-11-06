# Logique pure (aucun NiceGUI ici, testable independamment de l'UI) pour
# la ligne de progression de course affichee dans le footer (voir
# interface.build_footer) : a partir de data.config.interorder et
# data.timing_data, calcule la position de chaque athlete le long du
# parcours (DEPART -> intermediaires -> ARRIVEE, dans l'ordre fourni par
# interorder) et identifie les "roles" a afficher sur la ligne :
#   - LEADER : l'athlete le plus avance (le plus rapide en cas d'egalite
#     de progression)
#   - 1ER <CATEGORIE> / 1ER H / 1ER D : le plus avance de chaque categorie
#     fixe (voir app_config.CATEGORIES) / de chaque sexe
#   - NOUVEAU LEADER ? : voir _find_potential_new_leader, exigence
#     explicite de l'utilisateur ("celui qui pour son dernier
#     intermediaire connu est sorti avec la position de premier")
#   - 2E / 3E : podium general (uniquement pour la vue etendue, voir
#     interface.py)
#
# Le temps ecoule de chaque athlete est calcule par DIFFERENCE avec son
# propre C_START (pas le temps brut cumule depuis minuit) : necessaire
# pour rester correct meme en depart decale (ex: format INDIVIDUAL), ou
# comparer des temps bruts entre 2 athletes n'ayant pas le meme depart
# n'aurait aucun sens.

from dataclasses import dataclass, field
from types import SimpleNamespace

from app_config import CATEGORIES

#cles synthetiques representant le depart/l'arrivee - n'existent pas
#litteralement dans data.config.interorder (qui ne liste que des cles
#C_INTER...), mais l'utilisateur veut les voir comme les 2 extremites de
#la ligne ("DEPART... ARRIVEE")
START_KEY = "_START"
FINISH_KEY = "_FINISH"

#statuts excluant un athlete du calcul de progression (voir
#biathdata_reference.txt section 3, C_STATUS) : DNS n'a jamais pris le
#depart (aucune donnee de progression valide), DNF est sorti de course
#(ne peut plus etre "en tete" de quoi que ce soit)
_EXCLUDED_STATUSES = {"DNS", "DNF"}

#prefixe de cle -> gabarit de libelle convivial pour la ligne de
#progression (le "{n}" est remplace par le numero extrait de la cle).
#INDEPENDANT de SceneButton._INTERMEDIATE_LABELS (scene_button.py) : ces
#libelles-ci sont specifiques a cette ligne (demande explicite de
#l'utilisateur : "DEPART, INTER TOUR 1, ENTREE TIR 1, SORTIE TIR 1,
#ARRIVEE"), pas a la combo box de selection d'intermediaire d'un bouton -
#les 2 usages n'ont pas a partager le meme texte. L'ordre de la liste
#compte : C_INTERINSHOOT doit etre teste AVANT C_INTERSHOOT (prefixe plus
#specifique, sinon "C_INTERINSHOOT1".startswith("C_INTERSHOOT") serait
#aussi teste a tort si l'ordre etait invertit - en realite "INSHOOT" ne
#commence pas par "SHOOT" donc pas de collision reelle ici, mais garder
#l'ordre du plus specifique au plus general reste la pratique la plus sure)
_LABEL_PREFIXES: list[tuple[str, str]] = [
    ("C_INTERINSHOOT", "ENTREE TIR {n}"),
    ("C_INTERSHOOT", "SORTIE TIR {n}"),
    ("C_INTERSLOPE", "INTER TOUR {n}"),
    ("C_RELAY", "RELAIS {n}"),
]


#libelle convivial d'un checkpoint (cle brute ou synthetique START/FINISH)
def checkpoint_label(key: str) -> str:
    if key == START_KEY:
        return "DEPART"
    if key == FINISH_KEY:
        return "ARRIVEE"

    for prefix, template in _LABEL_PREFIXES:
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            if suffix.isdigit():
                return template.format(n=suffix)

    #cle non reconnue (prefixe inattendu) : affichee telle quelle plutot
    #que de planter - mieux vaut un libelle brut moche qu'une ligne de
    #progression qui casse a cause d'un format de course inhabituel
    return key


#sequence ORDONNEE des checkpoints du parcours, DEPART et ARRIVEE inclus
#en premiere/derniere position - deduplique interorder au passage (ne
#fait pas confiance aveuglement a l'unicite d'une source externe)
def build_sequence(interorder: list[str] | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in interorder or []:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return [START_KEY, *ordered, FINISH_KEY]


#valeur BRUTE (non convertie) d'un athlete a un checkpoint donne (gere les
#cles synthetiques START/FINISH -> C_START/C_TIME)
def _raw_value(athlete: SimpleNamespace, key: str):
    if key == START_KEY:
        return getattr(athlete, "C_START", 0)
    if key == FINISH_KEY:
        return getattr(athlete, "C_TIME", 0)
    return getattr(athlete, key, 0)


#un checkpoint est considere ATTEINT si sa valeur brute est un nombre
#strictement positif (0/absent/non numerique = "non atteint", voir
#biathdata_reference.txt : "0 si non atteint")
def _is_reached(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


#temps ecoule (meme unite que les donnees brutes, tres probablement des
#millisecondes - voir biathdata_reference.txt) entre le depart de CET
#athlete et le checkpoint donne. None si le depart ou le checkpoint
#lui-meme n'est pas valide.
def _elapsed(athlete: SimpleNamespace, key: str) -> float | None:
    start = _raw_value(athlete, START_KEY)
    if not _is_reached(start):
        return None

    value = _raw_value(athlete, key)
    if key != START_KEY and not _is_reached(value):
        return None

    try:
        return float(value) - float(start)
    except (TypeError, ValueError):
        return None


def _athlete_name(athlete: SimpleNamespace) -> str:
    last = (getattr(athlete, "C_LAST_NAME", "") or "").strip()
    first = (getattr(athlete, "C_FIRST_NAME", "") or "").strip()
    name = " ".join(part for part in (first, last) if part)
    return name or "?"


#mm:ss (ou h:mm:ss au-dela d'une heure) a partir d'un temps ecoule en
#millisecondes - purement pour l'affichage (tooltip/libelle des puces)
def format_elapsed(elapsed_ms: float) -> str:
    total_seconds = max(0, int(elapsed_ms / 1000))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


@dataclass
class AthleteProgress:
    bib: str
    name: str
    category: str
    sex: int | None
    index: int  # position dans RaceProgress.sequence (0 = DEPART)
    key: str  # cle du checkpoint correspondant a `index`
    elapsed: float  # temps ecoule (ms) jusqu'a ce checkpoint
    roles: list[str] = field(default_factory=list)


@dataclass
class RaceProgress:
    sequence: list[tuple[str, str]]  # [(cle, libelle convivial), ...]
    markers: list[AthleteProgress]  # un par athlete ayant AU MOINS un role


#leader d'un groupe d'athletes : le plus avance (index le plus grand),
#temps ecoule le plus court en cas d'egalite de progression
def _leader_of(group: list[AthleteProgress]) -> AthleteProgress | None:
    if not group:
        return None
    return max(group, key=lambda p: (p.index, -p.elapsed))


#calcule l'INTEGRALITE de la progression de course actuelle. Renvoie None
#tant qu'il n'y a rien a afficher (pas encore de config.interorder, ou
#aucun athlete eligible avec une progression valide) - l'appelant doit
#alors ne rien afficher plutot que montrer une ligne vide.
def compute_race_progress(config, timing_data: dict) -> RaceProgress | None:
    interorder = list(getattr(config, "interorder", None) or []) if config else []
    if not interorder or not timing_data:
        return None

    sequence = build_sequence(interorder)

    #athletes eligibles : ont pris le depart (C_START valide), pas DNS/DNF
    eligible: dict[str, SimpleNamespace] = {}
    for bib, athlete in timing_data.items():
        status = (getattr(athlete, "C_STATUS", "") or "").upper()
        if status in _EXCLUDED_STATUSES:
            continue
        if not _is_reached(_raw_value(athlete, START_KEY)):
            continue
        eligible[bib] = athlete

    if not eligible:
        return None

    #classement (temps ecoule croissant) de chaque checkpoint, parmi TOUS
    #les athletes eligibles l'ayant atteint - y compris ceux qui l'ont
    #depasse depuis (necessaire pour _find_potential_new_leader : "le plus
    #rapide a CE checkpoint", pas seulement parmi ceux qui y sont
    #actuellement)
    ranking_at: dict[str, list[str]] = {}
    for key in sequence:
        timed: list[tuple[float, str]] = []
        for bib, athlete in eligible.items():
            elapsed = _elapsed(athlete, key)
            if elapsed is not None:
                timed.append((elapsed, bib))
        timed.sort()
        ranking_at[key] = [bib for _, bib in timed]

    #position actuelle de chaque athlete = le checkpoint le PLUS AVANCE ou
    #il figure dans un classement (sequence est dans l'ordre croissant, la
    #derniere ecriture dans la boucle l'emporte donc)
    progresses: dict[str, AthleteProgress] = {}
    for index, key in enumerate(sequence):
        for bib in ranking_at[key]:
            athlete = eligible[bib]
            progresses[bib] = AthleteProgress(
                bib=bib,
                name=_athlete_name(athlete),
                category=getattr(athlete, "C_CATEGORY", "") or "",
                sex=getattr(athlete, "C_SEX", None),
                index=index,
                key=key,
                elapsed=_elapsed(athlete, key) or 0.0,
            )

    if not progresses:
        return None

    all_progress = list(progresses.values())

    overall = _leader_of(all_progress)
    overall.roles.append("LEADER")

    #podium general (2e/3e) - seulement affiche en vue etendue (voir
    #interface.py), calcule ici de toute facon (peu couteux)
    ranked = sorted(all_progress, key=lambda p: (-p.index, p.elapsed))
    for place, p in zip((2, 3), ranked[1:3]):
        p.roles.append(f"{place}E")

    for cat in CATEGORIES:
        leader = _leader_of([p for p in all_progress if p.category == cat])
        if leader is not None:
            leader.roles.append(f"1ER {cat}")

    for sex_value, label in ((1, "H"), (2, "D")):
        leader = _leader_of([p for p in all_progress if p.sex == sex_value])
        if leader is not None:
            leader.roles.append(f"1ER {label}")

    _find_potential_new_leader(all_progress, ranking_at, overall.bib)

    markers = [p for p in all_progress if p.roles]
    return RaceProgress(
        sequence=[(key, checkpoint_label(key)) for key in sequence],
        markers=markers,
    )


#"potentiel nouveau leader" (exigence explicite de l'utilisateur) : parmi
#tous les athletes SAUF le leader general, celui qui est classe 1ER au
#classement de SON PROPRE checkpoint actuel (ranking_at[p.key][0]) - donc
#le plus rapide parmi TOUS ceux ayant atteint ce meme point, y compris des
#athletes qui l'ont depasse depuis. Signale un athlete plus rapide que
#quiconque ne l'a ete a ce point du parcours, meme s'il n'a pas encore
#rejoint le leader general - une menace potentielle sur la tete de course.
#En cas de plusieurs candidats, le plus avance est retenu (menace la plus
#immediate) ; mute `all_progress` en place (ajoute son role).
def _find_potential_new_leader(
    all_progress: list[AthleteProgress], ranking_at: dict[str, list[str]], leader_bib: str
) -> None:
    best_candidate: AthleteProgress | None = None
    for p in all_progress:
        if p.bib == leader_bib:
            continue
        ranking = ranking_at.get(p.key)
        if ranking and ranking[0] == p.bib:
            if best_candidate is None or p.index > best_candidate.index:
                best_candidate = p

    if best_candidate is not None:
        best_candidate.roles.append("NOUVEAU LEADER ?")
