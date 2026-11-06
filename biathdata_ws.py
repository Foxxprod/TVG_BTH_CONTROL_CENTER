import asyncio
import json
import re
import traceback
from collections.abc import Callable
from types import SimpleNamespace

import websockets


class BiathDataWebsocket:
    def __init__(
        self,
        ip: str,
        port: int,
        path: str = "/",
        timeout: float = 5.0,
        reconnect: bool = True,
        reconnect_delay: float = 5.0,
    ):
        self.ip = ip
        self.port = port
        self.path = path

        self.timeout = timeout
        self.reconnect = reconnect
        self.reconnect_delay = reconnect_delay

        self.ws: websockets.ClientConnection | None = None
        self.connected = False

        #dernier temps d'aller-retour PING/PONG mesure (millisecondes, via
        #les frames de controle WebSocket natives - PAS un message de
        #donnees), None tant qu'aucun ping reussi n'a encore eu lieu -
        #affiche au survol du statut WS DATA du header (voir interface.py),
        #demande explicite de l'utilisateur ("si c'est pas trop
        #complique, le ping"). Boucle separee (_ping_loop) : la boucle de
        #reception (_receive_loop) est occupee a iterer les messages
        #entrants, le ping doit tourner EN PARALLELE - la bibliotheque
        #`websockets` gere ping/pong (frames de controle) independamment
        #des frames de donnees sur la meme connexion, donc aucun conflit.
        self.last_ping_ms: float | None = None
        self._ping_interval = 5.0
        self._ping_task: asyncio.Task | None = None

        self.latest_data: dict | None = None
        self.config: SimpleNamespace | None = None
        self.event_infos: SimpleNamespace | None = None
        self.timing_data: dict[str, SimpleNamespace] = {}

        self._lock = asyncio.Lock()
        self._receive_task: asyncio.Task | None = None

        #callbacks notifies (synchrone, sans argument) a chaque nouveau
        #message COMPLET traite avec succes (voir _handle_message) - permet
        #a l'interface de se rafraichir IMMEDIATEMENT des la reception,
        #plutot que de dependre uniquement d'un polling (ex: ui.timer(1.0,
        #...) cote SceneButton, garde en plus comme filet de securite).
        #Voir on_message().
        self._listeners: list[Callable[[], None]] = []

    @property
    def url(self) -> str:
        return f"ws://{self.ip}:{self.port}{self.path}"

    #abonne callback (fonction synchrone, sans argument) aux nouveaux
    #messages complets recus - utilise par SceneButton pour rafraichir
    #instantanement les intermediaires/formats de course disponibles des
    #qu'une nouvelle donnee arrive, sans attendre le tick de polling
    #suivant (jusqu'a 1s de latence sinon)
    def on_message(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    #desabonne callback (voir on_message) - IMPORTANT : self est un
    #singleton PARTAGE par tous les clients/pages (voir main.py), alors
    #qu'un abonnement typique (ex: SceneButton) est cree A CHAQUE
    #chargement de page. Sans desabonnement explicite quand cette page se
    #ferme, chaque rechargement de l'onglet navigateur ajouterait un
    #nouvel abonnement au registre partage sans jamais retirer les
    #anciens - accumulation indefinie. Ne leve pas si callback est deja
    #absent (desabonnement redondant, sans consequence).
    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    #connexion au serveur websocket du logiciel de donnees
    async def connect(self) -> str | None:
        """Open the WebSocket connection and start the background
        receive/reconnect loop.

        The loop is started even if this first attempt fails, so a
        WebSocket server that isn't available yet at startup keeps being
        retried (every self.reconnect_delay seconds) instead of giving up.

        Returns None on success, or an error string on failure.
        """
        error: str | None = None

        async with self._lock:
            try:
                self.ws = await asyncio.wait_for(
                    websockets.connect(self.url), timeout=self.timeout
                )
            except (
                OSError,
                asyncio.TimeoutError,
                websockets.exceptions.WebSocketException,
            ) as exc:
                self.connected = False
                error = str(exc)
            else:
                self.connected = True

        if self._receive_task is None or self._receive_task.done():
            self._receive_task = asyncio.create_task(self._receive_loop())
        if self._ping_task is None or self._ping_task.done():
            self._ping_task = asyncio.create_task(self._ping_loop())

        return error

    #ping/pong NATIF WebSocket (frame de controle, pas un message de
    #donnees) toutes les self._ping_interval secondes - mesure self.
    #last_ping_ms, n'affecte jamais self.connected (deja gere par
    #_receive_loop/_reconnect : une connexion websocket coupee y ferait de
    #toute facon echouer .ping()/le pong_waiter, mais ce n'est pas ce
    #signal qui pilote la reconnexion ici, contrairement a CasparCG.ping())
    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)

            if not self.connected or self.ws is None:
                continue

            try:
                start = asyncio.get_event_loop().time()
                pong_waiter = await self.ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=self.timeout)
                self.last_ping_ms = (asyncio.get_event_loop().time() - start) * 1000
            except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException):
                self.last_ping_ms = None

    #boucle unique : recoit les messages si connecte, sinon tente une reconnexion
    #(sequentiel, jamais plusieurs tentatives de reconnexion en parallele)
    async def _receive_loop(self) -> None:
        while True:
            if self.connected:
                try:
                    async for raw_message in self.ws:
                        self._handle_message(raw_message)
                except websockets.exceptions.WebSocketException:
                    pass

                self.connected = False
            elif self.reconnect:
                await asyncio.sleep(self.reconnect_delay)
                await self._reconnect()
            else:
                return

    #ferme une eventuelle connexion existante puis retente une connexion complete
    async def _reconnect(self) -> None:
        async with self._lock:
            if self.ws is not None:
                try:
                    await self.ws.close()
                except OSError:
                    pass
                self.ws = None

            try:
                self.ws = await asyncio.wait_for(
                    websockets.connect(self.url), timeout=self.timeout
                )
            except (
                OSError,
                asyncio.TimeoutError,
                websockets.exceptions.WebSocketException,
            ):
                self.connected = False
                return

            self.connected = True

    #remplace les caracteres invalides pour un attribut python (ex: espace) par un underscore
    @staticmethod
    def _sanitize_key(key: str) -> str:
        return re.sub(r"\W", "_", key)

    #convertit un dict en SimpleNamespace en assainissant les cles
    @staticmethod
    def _to_namespace(data: dict) -> SimpleNamespace:
        return SimpleNamespace(
            **{
                BiathDataWebsocket._sanitize_key(key): value
                for key, value in data.items()
            }
        )

    #traite un message brut recu sur le websocket
    def _handle_message(self, raw_message: str | bytes) -> None:
        try:
            message = json.loads(raw_message)
            # Le serveur encode parfois le JSON deux fois (le message recu
            # est une string JSON contenant elle-meme du JSON en texte
            # echappe) : on decode une seconde fois dans ce cas.
            if isinstance(message, str):
                message = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return

        if not isinstance(message, dict):
            return

        if "config" in message and "event_infos" in message and "timing_data" in message:
            self.latest_data = message
            self.config = self._to_namespace(message["config"])
            self.event_infos = self._to_namespace(message["event_infos"])
            self.timing_data = {
                bib: self._to_namespace(athlete)
                for bib, athlete in message["timing_data"].items()
            }

            #notifie les abonnes (voir on_message) - un listener qui leve
            #une exception ne doit JAMAIS interrompre la boucle de
            #reception (les autres listeners, et la reception des
            #PROCHAINS messages, ne doivent pas en dependre)
            for listener in self._listeners:
                try:
                    listener()
                except Exception:
                    traceback.print_exc()
        # else: autre type de message, ignore pour l'instant

    #deconnexion du serveur websocket
    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None

        async with self._lock:
            if self.ws is not None:
                try:
                    await self.ws.close()
                except OSError:
                    pass

            self.ws = None
            self.connected = False

    #changement d'ip (et optionnellement de port), reconnecte si deja connecte
    async def change_ip(self, ip: str, port: int | None = None) -> str | None:
        """Update the target ip (and optionally port).

        If currently connected, disconnects first and reconnects to the
        new address. Returns None on success, or an error string on
        failure (from the reconnect attempt).
        """
        was_connected = self.connected

        if was_connected:
            await self.disconnect()

        self.ip = ip
        if port is not None:
            self.port = port

        if was_connected:
            return await self.connect()

        return None


if __name__ == "__main__":

    async def main():
        # A adapter avec l'ip/port reels du logiciel de donnees biathlon.
        data = BiathDataWebsocket("127.0.0.1", 8765)
        error = await data.connect()

        if error:
            print(f"Erreur de connexion : {error}")
            return

        print("Connecte au websocket")

        await asyncio.sleep(5)

        if data.event_infos is not None:
            print(f"Titre : {data.event_infos.RACEFORMAT }")
            print(f"Nombre d'athletes recus : {len(data.timing_data)}")
        else:
            print("Aucune donnee de chronometrage recue pour le moment")

        await data.disconnect()

    asyncio.run(main())
