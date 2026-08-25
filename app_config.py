# Persistance de la configuration utilisateur (config.json) : theme clair/
# sombre, titre du logiciel, adresse/port du serveur de donnees WebSocket,
# adresse/port CasparCG et numeros de channel preview/programme.
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

DEFAULT_CONFIG = {
    "dark_mode": False,
    "software_title": "BIATHLON - CONTROL CENTER",
    "websocket_ip": "192.168.1.36",
    "websocket_port": 8765,
    "caspar_ip": "127.0.0.1",
    "caspar_port": 5250,
    "caspar_channel_preview": 1,
    "caspar_channel_programme": 2,
}


#lit config.json et complete les champs manquants avec les valeurs par
#defaut (fichier absent ou incomplet, ex: toute premiere execution)
def load_config(path: Path = CONFIG_PATH) -> dict:
    config = dict(DEFAULT_CONFIG)

    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            config.update(json.load(f))

    return config


#reecrit integralement config.json (le cree si absent)
def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
