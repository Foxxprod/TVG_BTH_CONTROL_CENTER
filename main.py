from nicegui import app, ui

from biathdata_ws import BiathDataWebsocket
from casparcg import CasparCG
from interface import build_body, build_header

# A adapter avec les vraies valeurs de connexion.
caspar = CasparCG("127.0.0.1", 5250, channel_programm=1, channel_preview=2)
data = BiathDataWebsocket("127.0.0.1", 8765)


@ui.page("/")
def index() -> None:
    build_header(caspar, data)
    build_body(caspar, data)


#connexion a casparcg et au websocket au demarrage du serveur nicegui
@app.on_startup
async def startup() -> None:
    caspar_error = await caspar.connect()
    print(f"CasparCG connect -> {'OK' if not caspar_error else caspar_error}")

    ws_error = await data.connect()
    print(f"WebSocket connect -> {'OK' if not ws_error else ws_error}")


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Biath Control Center", port=8081, reload=False)
