# Chargement de la configuration des boutons (templates.json), lue au
# demarrage par interface.py (build_body).
#
# Chaque entree du fichier JSON reprend les parametres du constructeur
# SceneButton (voir scene_button.py : label, template, layer, cg_layer,
# extra_data, send_ip, field_ip, bib_on, field_bib, multipage_on,
# field_page, reset_page_on_play, target_on, field_target, free_texts,
# intermediate_on, intermediate_type, field_intermediate, race_formats),
# plus :
#   - "section" (obligatoire) : "prerace" / "race" / "postrace"
#   - "row"     (optionnel, defaut 1) : numero de ligne dans la section
#   - "column"  (optionnel, defaut 1) : numero de colonne dans la ligne
#
# Ajouter, modifier ou retirer un graphique ne necessite plus de toucher au
# code Python : editer templates.json suffit, y compris une fois le
# logiciel compile/livre.
#
# JSON n'acceptant pas les commentaires, templates.json commence par une
# entree de documentation ("_example": true) listant tous les champs
# possibles a titre de reference : elle est ignoree au chargement, ce n'est
# pas un vrai bouton.
#
# Une entree peut aussi representer un TITRE DE COLONNE plutot qu'un
# bouton, en marquant "_column_title": true, avec "section", "column" et
# "title" (voir load_column_titles / interface.add_column_title). Ces
# entrees sont ignorees par load_button_configs (comme "_example").
#
# templates.json peut aussi etre REECRIT par le logiciel lui-meme (pas
# seulement lu), ex: quand l'operateur ajoute une option a une combo box
# de free_text en direct (voir SceneButton._on_add_free_text_option) - voir
# load_raw_configs/save_button_configs, qui manipulent la liste JSON telle
# quelle (boutons + titres de colonne + doc), sans filtrage.
#
# BUTTONS_CONFIG_PATH pointe vers un fichier UTILISATEUR modifiable (voir
# paths.py) : en mode installe (executable compile), %APPDATA%
# \BiathControlCenter\templates.json, initialise au tout premier lancement
# a partir du templates.json "de base" embarque dans l'executable
# (ensure_user_file). En developpement, directement le templates.json du
# repo, comme avant.

import json
from pathlib import Path

from paths import ensure_user_file

BUTTONS_CONFIG_PATH = ensure_user_file("templates.json")

REQUIRED_FIELDS = {"section", "label", "template", "layer"}
COLUMN_TITLE_REQUIRED_FIELDS = {"section", "column", "title"}


#lit le JSON brut de templates.json (liste), leve une erreur claire si le
#fichier est mal forme
def _load_raw(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        configs = json.load(f)

    if not isinstance(configs, list):
        raise ValueError(f"{path} doit contenir une liste JSON de definitions de boutons")

    return configs


#lit templates.json TEL QUEL (liste brute, boutons + titres de colonne +
#doc), sans filtrage. Renvoie [] si le fichier est absent. A utiliser
#quand on doit ensuite REECRIRE le fichier (avec save_button_configs) :
#charger une seule fois ce brut et en deriver button_configs/
#column_titles (via le parametre `raw` de load_button_configs/
#load_column_titles) garantit que les modifications faites en memoire sur
#un SceneButton (ex: nouvelle option de free_text) portent bien sur les
#MEMES objets que ceux qui seront reecrits, plutot que sur une copie issue
#d'une lecture independante du fichier.
def load_raw_configs(path: Path = BUTTONS_CONFIG_PATH) -> list[dict]:
    if not path.exists():
        print(f"Aucun fichier de configuration de boutons trouve ({path})")
        return []

    return _load_raw(path)


#reecrit integralement templates.json (meme structure : boutons, titres de
#colonne et entrees de documentation confondus) - utilise apres une
#modification en memoire d'une liste obtenue via load_raw_configs
def save_button_configs(configs: list[dict], path: Path = BUTTONS_CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2, ensure_ascii=False)


#lit et valide templates.json, renvoie la liste des vraies definitions de
#boutons (les entrees de documentation "_example" et les titres de colonne
#"_column_title" sont filtres). `raw`, si fourni (voir load_raw_configs),
#evite une relecture independante du fichier.
def load_button_configs(
    path: Path = BUTTONS_CONFIG_PATH, raw: list[dict] | None = None
) -> list[dict]:
    if raw is None:
        raw = load_raw_configs(path)

    configs = [
        config for config in raw if not config.get("_example") and not config.get("_column_title")
    ]

    for config in configs:
        missing = REQUIRED_FIELDS - config.keys()
        if missing:
            raise ValueError(
                f"Definition de bouton incomplete, champ(s) manquant(s) {missing} : {config}"
            )

    return configs


#lit et valide templates.json, renvoie la liste des titres de colonne
#(entrees marquees "_column_title": true). `raw`, si fourni (voir
#load_raw_configs), evite une relecture independante du fichier.
def load_column_titles(
    path: Path = BUTTONS_CONFIG_PATH, raw: list[dict] | None = None
) -> list[dict]:
    if raw is None:
        raw = load_raw_configs(path)

    titles = [
        config for config in raw if config.get("_column_title") and not config.get("_example")
    ]

    for title in titles:
        missing = COLUMN_TITLE_REQUIRED_FIELDS - title.keys()
        if missing:
            raise ValueError(
                f"Titre de colonne incomplet, champ(s) manquant(s) {missing} : {title}"
            )

    return titles
