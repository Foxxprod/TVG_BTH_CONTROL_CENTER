# Chargement de la configuration des boutons (templates.json), lue au
# demarrage par interface.py (build_body).
#
# Chaque entree du fichier JSON reprend les parametres du constructeur
# SceneButton (voir scene_button.py : label, template, layer, cg_layer,
# extra_data, send_ip, field_ip, bib_on, field_bib, multipage_on,
# field_page, reset_page_on_play, target_on, field_target, free_texts,
# intermediate_on, intermediate_type, field_intermediate, race_formats,
# second_cg, second_cg_layer, second_layer, restrict_to_sex, field_sex,
# restrict_to_cat, field_cat), plus :
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
# Une entree peut aussi representer le CONTENEUR DES CASES A COCHER
# GLOBALES "restreindre par sexe"/"par categorie", en marquant
# "_restrict_controls": true, avec "section" et "column" - voir
# load_restrict_controls / interface.add_restrict_controls. PAS de "row" :
# ce conteneur est TOUJOURS place tout en haut de la colonne choisie
# (row=1 force, ignore silencieusement s'il est quand meme present dans
# l'entree), juste sous le titre - exigence explicite de l'utilisateur.
# Peut cohabiter avec un bouton dans la meme case (ils s'empilent, ce
# conteneur toujours en premier/en haut). Ces entrees sont ignorees par
# load_button_configs/load_column_titles (comme "_example").
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

from paths import FROZEN, ensure_user_file, resource_path

BUTTONS_CONFIG_PATH = ensure_user_file("templates.json")

REQUIRED_FIELDS = {"section", "label", "template", "layer"}
COLUMN_TITLE_REQUIRED_FIELDS = {"section", "column", "title"}
RESTRICT_CONTROLS_REQUIRED_FIELDS = {"section", "column"}


#identifie une entree de templates.json de facon STABLE, independamment
#du reste de son contenu (voir merge_bundled_templates ci-dessous) : le
#simple "label" ne suffit pas (2 boutons differents peuvent le partager),
#le triplet (template, layer, cg_layer) est ce qui est REELLEMENT envoye
#a CasparCG et identifie donc un bouton reel de facon fiable. Les entrees
#"_column_title"/"_restrict_controls" (pas de template/layer) sont
#identifiees par (section, column) a la place ; "_example" est un
#singleton (une seule entree de ce type a jamais du sens).
def _entry_identity(entry: dict) -> tuple:
    if entry.get("_example"):
        return ("_example",)
    if entry.get("_column_title"):
        return ("_column_title", entry.get("section"), entry.get("column"))
    if entry.get("_restrict_controls"):
        return ("_restrict_controls", entry.get("section"), entry.get("column"))
    return ("_button", entry.get("template"), entry.get("layer"), entry.get("cg_layer", 1))


#complete (AJOUTE UNIQUEMENT, ne retire ni n'ecrase jamais rien) la copie
#UTILISATEUR de templates.json avec les entrees NOUVELLES du templates.json
#EMBARQUE dans l'executable (celui livre avec CETTE version du logiciel).
#
#PROBLEME REEL RESOLU : en mode installe, ensure_user_file() ne copie le
#templates.json embarque QU'AU TOUT PREMIER lancement (jamais ensuite,
#pour ne pas ecraser les options de free_text ajoutees en direct par
#l'operateur - voir SceneButton._on_add_free_text_option). Consequence
#concrete rencontree par l'utilisateur : recompiler/redeployer une
#version plus recente avec de NOUVEAUX BOUTONS ajoutes a templates.json
#ne les faisait jamais apparaitre - la copie utilisateur restait figee
#sur l'etat du tout premier lancement ("pourquoi quand je compile le
#logiciel je n'ai pas tous les boutons qui apparaissent ?").
#
#NE RETIRE JAMAIS une entree presente cote utilisateur mais absente cote
#embarque (un bouton retire dans une future version ne doit jamais faire
#disparaitre un bouton personnalise ajoute par l'operateur lui-meme cote
#utilisateur) - purement additif, jamais destructif, jamais d'ecrasement
#d'une entree existante (memes champs "figes" que la version embarquee ou
#non - ses eventuelles options de free_text ajoutees en direct restent
#intactes).
def merge_bundled_templates(bundled: list[dict], user: list[dict]) -> tuple[list[dict], bool]:
    existing_identities = {_entry_identity(entry) for entry in user}
    merged = list(user)
    changed = False

    for entry in bundled:
        identity = _entry_identity(entry)
        if identity not in existing_identities:
            merged.append(entry)
            existing_identities.add(identity)
            changed = True

    return merged, changed


#execute la fusion ci-dessus UNE SEULE FOIS au demarrage, UNIQUEMENT en
#mode installe (FROZEN) - en developpement, resource_path() et
#user_data_path() pointent vers le MEME fichier (voir paths.py), une
#fusion n'y aurait aucun sens. Jamais fatal : un templates.json embarque
#manquant/invalide ne doit jamais empecher le logiciel de demarrer, il
#continue simplement avec la copie utilisateur telle quelle.
def _merge_bundled_templates_on_startup() -> None:
    if not FROZEN:
        return

    bundled_path = resource_path("templates.json")

    try:
        bundled = _load_raw(bundled_path)
        user = _load_raw(BUTTONS_CONFIG_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        return

    merged, changed = merge_bundled_templates(bundled, user)
    if changed:
        save_button_configs(merged)


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


#une entree est une "vraie" definition de bouton si elle n'est marquee ni
#"_example", ni "_column_title", ni "_restrict_controls"
def _is_real_button(config: dict) -> bool:
    return not (
        config.get("_example") or config.get("_column_title") or config.get("_restrict_controls")
    )


#lit et valide templates.json, renvoie la liste des vraies definitions de
#boutons (les entrees de documentation "_example", les titres de colonne
#"_column_title" et le conteneur "_restrict_controls" sont filtres). `raw`,
#si fourni (voir load_raw_configs), evite une relecture independante du
#fichier.
def load_button_configs(
    path: Path = BUTTONS_CONFIG_PATH, raw: list[dict] | None = None
) -> list[dict]:
    if raw is None:
        raw = load_raw_configs(path)

    configs = [config for config in raw if _is_real_button(config)]

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


#lit et valide templates.json, renvoie la liste des conteneurs de cases a
#cocher globales (entrees marquees "_restrict_controls": true). `raw`, si
#fourni (voir load_raw_configs), evite une relecture independante du
#fichier.
def load_restrict_controls(
    path: Path = BUTTONS_CONFIG_PATH, raw: list[dict] | None = None
) -> list[dict]:
    if raw is None:
        raw = load_raw_configs(path)

    controls = [
        config
        for config in raw
        if config.get("_restrict_controls") and not config.get("_example")
    ]

    for control in controls:
        missing = RESTRICT_CONTROLS_REQUIRED_FIELDS - control.keys()
        if missing:
            raise ValueError(
                f"Conteneur restrict_controls incomplet, champ(s) manquant(s) {missing} : {control}"
            )

    return controls


#execute UNE SEULE FOIS, a l'import de ce module (donc au tout premier
#chargement de page - voir interface.build_body) - APRES la definition de
#toutes les fonctions ci-dessus, dont _merge_bundled_templates_on_startup
#a besoin (_load_raw/save_button_configs).
_merge_bundled_templates_on_startup()
