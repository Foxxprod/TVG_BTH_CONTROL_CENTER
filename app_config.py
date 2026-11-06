# Persistance de la configuration utilisateur (config.json) : theme clair/
# sombre, titre du logiciel, adresse/port du serveur de donnees WebSocket,
# adresse/port CasparCG et numeros de channel preview/programme, etat des
# cases a cocher globales restrict_by_sex/restrict_by_cat et regroupement
# des categories (voir CATEGORIES/normalize_category_groups plus bas).
#
# Lue une fois au demarrage (main.py) dans un dict simple partage par
# reference avec interface.build_header, qui le modifie et le sauvegarde
# depuis la boite de dialogue Parametres. Reecrit par le logiciel a chaque
# changement de reglage.
#
# CONFIG_PATH pointe vers un fichier UTILISATEUR modifiable (voir
# paths.py) : en mode installe (executable compile), %APPDATA%
# \BiathControlCenter\config.json. En developpement, directement le
# config.json du repo (ignore par git), comme avant. Pas besoin de
# ensure_user_file() ici (contrairement a templates.json) : load_config()
# gere deja nativement un fichier absent via DEFAULT_CONFIG.

import json
from pathlib import Path

from paths import user_data_path

CONFIG_PATH = user_data_path("config.json")

#categories biathlon FIXES (pas configurables - contrairement au
#regroupement entre elles, voir normalize_category_groups ci-dessous),
#dans l'ordre d'age croissant. Utilisees par le systeme de restriction
#par categorie (SceneButton restrict_to_cat/bib_on, voir scene_button.py)
CATEGORIES = ["U15", "U17", "U19", "U21", "SEN"]

DEFAULT_CONFIG = {
    "dark_mode": False,
    "software_title": "BIATHLON - CONTROL CENTER",
    "websocket_ip": "192.168.1.36",
    "websocket_port": 8765,
    "caspar_ip": "127.0.0.1",
    "caspar_port": 5250,
    "caspar_channel_preview": 1,
    "caspar_channel_programme": 2,
    #etat des 2 cases a cocher globales "restreindre par sexe"/"par
    #categorie" (placees dans le corps de page via templates.json, voir
    #interface.add_restrict_controls) - persiste comme le reste, contrairement
    #a l'etat en direct (preview/programme) de chaque bouton qui lui ne
    #survit jamais a un redemarrage
    "restrict_by_sex": False,
    "restrict_by_cat": False,
    #regroupement des CATEGORIES entre elles (ex: U21+SEN courent ensemble),
    #une liste de groupes (chacun une liste de categories) - edite en
    #glisser-deposer dans la boite de dialogue Parametres (voir build_header/
    #normalize_category_groups), appliquee et persistee INSTANTANEMENT a
    #chaque deplacement (pas besoin de cliquer "Enregistrer", contrairement
    #au reste de cette boite de dialogue - une manipulation directe comme
    #le drag-and-drop n'appelle pas un "formulaire" a valider). Chaque
    #categorie seule dans son propre groupe par defaut (aucun regroupement,
    #comportement inchange par rapport a avant cette fonctionnalite).
    "category_groups": [[cat] for cat in CATEGORIES],
    #affiche/masque la ligne de progression de course du footer (voir
    #interface._build_race_progress_section) - False par defaut : purement
    #additif et sans risque pour le reste du logiciel, mais un nouvel
    #affichage ne doit pas apparaitre "par surprise" a la premiere mise a
    #jour sans que l'operateur l'ait active lui-meme.
    "race_progress_enabled": False,
}


#garantit que `groups` est une partition VALIDE des CATEGORIES fixes -
#chacune apparait dans exactement UN groupe, ni plus ni moins. Necessaire
#car category_groups est une structure LIBRE (liste de listes editee en
#glisser-deposer, potentiellement aussi modifiee/corrompue a la main dans
#config.json) : une categorie manquante devient son propre groupe (ordre
#de CATEGORIES), un doublon ou une categorie inconnue est ignore. Utilisee
#a chaque lecture (demarrage, ouverture des Parametres) plutot que de
#faire confiance aveuglement au contenu du fichier.
def normalize_category_groups(groups) -> list[list[str]]:
    seen: set[str] = set()
    result: list[list[str]] = []

    for group in groups or []:
        #boucle explicite (pas une comprehension + seen.update() apres
        #coup) : un doublon DANS LE MEME groupe doit aussi etre filtre,
        #pas seulement un doublon entre 2 groupes differents - seen doit
        #donc se mettre a jour ENTREE PAR ENTREE, bug reel rencontre en
        #testant cette fonction (["U21","SEN","U21"] gardait le doublon)
        cleaned = []
        for cat in group:
            if cat in CATEGORIES and cat not in seen:
                cleaned.append(cat)
                seen.add(cat)
        if cleaned:
            result.append(cleaned)

    result.extend([cat] for cat in CATEGORIES if cat not in seen)

    return result


#migre l'ANCIEN format de regroupement des categories (10 cases a cocher
#par PAIRE, "category_group_links": {"CAT_A|CAT_B": bool}, remplace par le
#glisser-deposer - voir normalize_category_groups) vers le nouveau
#("category_groups": liste de groupes) via une union-find classique
#(A-B et B-C coches -> A/B/C dans le meme groupe, meme sans A-C coche).
#Necessaire pour ne pas perdre silencieusement un regroupement deja
#configure par l'utilisateur (config.json reel deja rencontre avec
#"U15|U21": true) lors du passage a la nouvelle UI.
def _migrate_category_group_links(links: dict) -> list[list[str]]:
    parent = {cat: cat for cat in CATEGORIES}

    def find(cat: str) -> str:
        while parent[cat] != cat:
            cat = parent[cat]
        return cat

    for pair_key, linked in links.items():
        if not linked:
            continue
        cat_a, _, cat_b = pair_key.partition("|")
        if cat_a not in parent or cat_b not in parent:
            continue
        root_a, root_b = find(cat_a), find(cat_b)
        if root_a != root_b:
            parent[root_a] = root_b

    groups: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        groups.setdefault(find(cat), []).append(cat)

    return list(groups.values())


#lit config.json et complete les champs manquants avec les valeurs par
#defaut (fichier absent ou incomplet, ex: toute premiere execution)
def load_config(path: Path = CONFIG_PATH) -> dict:
    config = dict(DEFAULT_CONFIG)
    on_disk: dict = {}

    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            on_disk = json.load(f)
        config.update(on_disk)

    #migration a la volee de l'ancien format de regroupement des
    #categories (voir _migrate_category_group_links) - seulement si le
    #FICHIER SUR DISQUE contient encore l'ancien format et pas le nouveau
    #(pas `config`, qui a toujours "category_groups" via DEFAULT_CONFIG
    #meme si le fichier ne l'a pas - piege reel rencontre en testant : la
    #condition ne se declenchait jamais avec `config`, toujours vrai).
    #Si le fichier a deja "category_groups" (deja migre/edite via le
    #glisser-deposer), il gagne deja via config.update() ci-dessus - ne
    #jamais ecraser un regroupement plus recent avec une migration perimee.
    if "category_group_links" in on_disk and "category_groups" not in on_disk:
        config["category_groups"] = _migrate_category_group_links(on_disk["category_group_links"])
    config.pop("category_group_links", None)

    return config


#reecrit integralement config.json (le cree si absent)
def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
