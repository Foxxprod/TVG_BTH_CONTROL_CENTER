# Chemins de fichiers, compatibles a la fois avec une execution en mode
# developpement (python main.py depuis le repo) ET en mode installe
# (executable compile via PyInstaller/auto-py-to-exe, voir BiathControlCenter
# .spec et PROJECT_CONTEXT.txt).
#
# Deux categories de fichiers, deux fonctions :
#
#   - resource_path() : ressources EN LECTURE SEULE embarquees dans
#     l'executable (logo, templates.json "de base" fourni avec le
#     logiciel). En mode installe, PyInstaller les extrait dans un
#     dossier temporaire expose via sys._MEIPASS - un chemin different a
#     chaque lancement, jamais a utiliser pour ecrire quoi que ce soit.
#
#   - user_data_path() : fichiers UTILISATEUR modifiables par le logiciel
#     lui-meme (config.json, templates.json une fois copie - voir
#     ensure_user_file). En mode installe, %APPDATA%\BiathControlCenter
#     (zone ecrivable sans droits administrateur, contrairement au
#     dossier d'installation dans Program Files). En developpement, la
#     racine du projet - les fichiers restent a cote du code, pratique
#     pour les editer/versionner directement (templates.json est commite,
#     config.json est dans .gitignore).
#
# En mode installe, le tout premier lancement n'a pas encore de fichier
# utilisateur : ensure_user_file() le copie alors depuis la ressource
# embarquee du meme nom (le templates.json "de base" livre avec le
# logiciel), sans jamais ecraser une copie utilisateur deja existante.

import shutil
import sys
from pathlib import Path

#True uniquement dans un executable compile par PyInstaller (attribut
#ajoute par le bootloader, absent en execution normale)
FROZEN = getattr(sys, "frozen", False)


def resource_path(*parts: str) -> Path:
    if FROZEN:
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent

    return base.joinpath(*parts)


def user_data_path(*parts: str) -> Path:
    if FROZEN:
        import os

        base = Path(os.environ.get("APPDATA", Path.home())) / "BiathControlCenter"
        base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(__file__).parent

    return base.joinpath(*parts)


#garantit qu'un fichier utilisateur existe, en le copiant depuis la
#ressource embarquee du meme nom s'il est absent (ex: tout premier
#lancement en mode installe). Ne touche jamais a un fichier utilisateur
#deja present. En developpement, le fichier vit deja directement dans le
#repo (resource_path == user_data_path), rien a copier.
def ensure_user_file(filename: str) -> Path:
    target = user_data_path(filename)

    if FROZEN and not target.exists():
        source = resource_path(filename)
        if source.exists():
            shutil.copy(source, target)

    return target
