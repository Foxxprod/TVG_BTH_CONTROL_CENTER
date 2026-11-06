from nicegui import app, ui

from app_config import load_config
from biathdata_ws import BiathDataWebsocket
from casparcg import CasparCG
from interface import build_body, build_footer, build_header
from paths import resource_path
from streamdeck_controller import start_streamdeck

#icone de l'onglet navigateur (favicon NiceGUI - PAS la meme chose que
#l'icone du fichier .exe, definie separement dans BiathControlCenter.spec
#et gravee dans l'executable au moment de la compilation). Doit etre une
#ressource embarquee accessible A L'EXECUTION (voir paths.py), d'ou le
#besoin de l'ajouter aussi aux `datas` du .spec, en plus du parametre
#icon= de EXE(). Optionnelle : si le fichier n'existe pas (ex: pas encore
#ajoute au repo), NiceGUI retombe sur son favicon par defaut plutot que
#de planter au demarrage.
ICON_PATH = resource_path("icon.ico")

# Valeurs de connexion/reglages chargees depuis config.json (voir
# app_config.py), avec les defauts de DEFAULT_CONFIG si le fichier est 
# absent ou incomplet. Modifiables en direct depuis la boite de dialogue
# Parametres du header, sans redemarrer le logiciel.
config = load_config()

caspar = CasparCG(
    config["caspar_ip"],
    config["caspar_port"],
    channel_programm=config["caspar_channel_programme"],
    channel_preview=config["caspar_channel_preview"],
)
data = BiathDataWebsocket(config["websocket_ip"], config["websocket_port"])


@ui.page("/")
def index() -> None:
    #dict simple partage entre build_header et build_footer (recree pour
    #CHAQUE chargement de page, comme `config` d'ailleurs) - permet a la
    #case a cocher "Afficher la progression de la course" du header de
    #basculer en direct la visibilite de la section correspondante du
    #footer sans les coupler directement (voir build_header/build_footer)
    footer_hooks: dict = {}
    build_header(caspar, data, config, footer_hooks)
    build_body(caspar, data, config)
    build_footer(caspar, data, config, footer_hooks)


#connexion a casparcg et au websocket au demarrage du serveur nicegui
@app.on_startup
async def startup() -> None:
    caspar_error = await caspar.connect()
    print(f"CasparCG connect -> {'OK' if not caspar_error else caspar_error}")

    ws_error = await data.connect()
    print(f"WebSocket connect -> {'OK' if not ws_error else ws_error}")

    #Stream Deck (voir streamdeck_controller.py) : optionnel, ne bloque
    #jamais le demarrage si aucun peripherique n'est branche/detectable.
    #Demarre APRES caspar/data (utilises immediatement pour le premier
    #rendu des touches - etat preview/programme, RACEFORMAT courant).
    start_streamdeck(caspar, data)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="Biath Control Center",
        port=8081,
        reload=False,
        favicon=ICON_PATH if ICON_PATH.exists() else None,
    )
