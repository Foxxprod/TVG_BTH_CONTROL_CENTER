# Exemple d'utilisation :
#
#   caspar = CasparCG("127.0.0.1", 5250, channel_programm=1, channel_preview=2)
#   error = await caspar.connect()
#
#   if error:
#       print(f"Erreur de connexion : {error}")
#   else:
#       response = await caspar.send_command("INFO")
#
#       if response.is_error:
#           print(f"Erreur : {response.message}")
#       else:
#           print(response.message)
#           for line in response.data:
#               print(line)
#
#       await caspar.disconnect()

import asyncio
from dataclasses import dataclass, field

#PRINCIPE GENERAL :

# CHANNEL 1 = PREVIEW
# CHANNEL 2 = PROGRAM
#
#On peut envoyer directement une scene au programme en utilisant la fonction play_template
#Sinon on peut egalement envoyer une scene en preview puis la jouer sur le programme avec la fonction play_prw_to_pgm
#
#

@dataclass
class AMCPResponse:
    code: int
    message: str
    data: list[str] = field(default_factory=list)

    @property
    def is_info(self) -> bool:
        """1xx: informational, not a final result."""
        return 100 <= self.code < 200

    @property
    def is_success(self) -> bool:
        """2xx: command executed successfully."""
        return 200 <= self.code < 400

    @property
    def is_error(self) -> bool:
        """4xx/5xx from CasparCG, or 0 for a local/network failure."""
        return self.code == 0 or self.code >= 400


class CasparCG:
    def __init__(
        self,
        ip: str,
        port: int,
        channel_programm: int,
        channel_preview: int,
        timeout: float = 5.0,
        reconnect: bool = True,
        reconnect_delay: float = 5.0,
    ):
        self.ip = ip
        self.port = port
        self.channel_programm = channel_programm
        self.channel_preview = channel_preview

        self.timeout = timeout
        self.reconnect = reconnect
        self.reconnect_delay = reconnect_delay

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.connected = False

        self._lock = asyncio.Lock()
        #un bouton avec second_cg envoie DEUX producers en preview (meme
        #layer video partage channel_preview-1, cg_layer differents - voir
        #SceneButton._second_cg_stack_layer). _preview doit donc suivre
        #plusieurs producers a la fois, indexes par cg_layer, plutot qu'un
        #seul slot ecrase par le dernier play() - sinon le play du second
        #CG (juste apres celui du principal) effacait le suivi du
        #principal, et play_prw_to_pgm() rejouait le SECOND CG au take a
        #la place du principal (bug reel rencontre : le principal
        #disparaissait completement au take en passant par la preview).
        self._preview: dict[int, dict] = {}

        self._ping_interval = 5.0
        self._ping_task: asyncio.Task | None = None
        #dernier temps d'aller-retour PING/PONG mesure (millisecondes),
        #None tant qu'aucun ping reussi n'a encore eu lieu - affiche au
        #survol du statut CASPAR CG du header (voir interface.py), demande
        #explicite de l'utilisateur
        self.last_ping_ms: float | None = None

        self._remove_delay = 5.0
        self._pending_removals: dict[tuple[str, int], asyncio.Task] = {}

    # =====================================================================
    # FONCTIONS INTERNES / TECHNIQUES (protocole AMCP, pas destinees a etre
    # appelees directement depuis le reste du logiciel)
    # =====================================================================

    #verifie que la connexion amcp est toujours active, mesure au passage
    #le temps d'aller-retour (self.last_ping_ms) - affiche au survol du
    #statut CASPAR CG du header, voir interface.py
    async def ping(self) -> bool:
        """Send a PING and update self.connected accordingly.

        PING is a special AMCP command: it replies with the raw string
        "PONG" instead of a standard "XXX ..." status line, so it is
        handled separately from send_command/_read_response.
        """
        if self.reader is None or self.writer is None:
            self.connected = False
            return False

        async with self._lock:
            start = asyncio.get_event_loop().time()
            try:
                self.writer.write(b"PING\r\n")
                await asyncio.wait_for(self.writer.drain(), timeout=self.timeout)

                response = await asyncio.wait_for(
                    self.reader.readline(), timeout=self.timeout
                )
            except (OSError, asyncio.TimeoutError):
                self.connected = False
                self.last_ping_ms = None
                return False

            self.connected = response.decode().strip() == "PONG"
            self.last_ping_ms = (
                (asyncio.get_event_loop().time() - start) * 1000 if self.connected else None
            )
            return self.connected

    #boucle unique : ping periodique si connecte, sinon tente une reconnexion
    #(sequentiel, jamais plusieurs tentatives de reconnexion en parallele)
    async def _ping_loop(self) -> None:
        while True:
            if self.connected:
                await asyncio.sleep(self._ping_interval)
                await self.ping()
            elif self.reconnect:
                await asyncio.sleep(self.reconnect_delay)
                await self._reconnect()
            else:
                return

    #ferme une eventuelle connexion existante puis retente une connexion complete
    async def _reconnect(self) -> None:
        async with self._lock:
            if self.writer is not None:
                try:
                    self.writer.close()
                except OSError:
                    pass
                self.reader = None
                self.writer = None

            try:
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.ip, self.port),
                    timeout=self.timeout,
                )
            except (OSError, asyncio.TimeoutError):
                self.connected = False
                return

        await self.ping()

    #envoyer une commande amcp a casparcg
    async def send_command(self, command: str) -> AMCPResponse:
        """Send a raw AMCP command and return its parsed response."""
        if not self.connected or self.reader is None or self.writer is None:
            return AMCPResponse(code=0, message="Not connected", data=[])

        async with self._lock:
            try:
                self.writer.write(f"{command}\r\n".encode("utf-8"))
                await asyncio.wait_for(self.writer.drain(), timeout=self.timeout)
                return await self._read_response()
            except (OSError, asyncio.TimeoutError) as exc:
                self.connected = False
                return AMCPResponse(code=0, message=str(exc), data=[])

    #lecture de la reponse du serveur
    async def _read_response(self) -> AMCPResponse:
        """Read and parse a single AMCP response.

        Response code convention (confirme dans le code source officiel
        de CasparCG, src/protocol/amcp/AMCPCommandsImpl.cpp, commentaire
        d'en-tete du fichier) :
            202 [command] OK  -> execute, AUCUNE ligne supplementaire.
            201 [command] OK  -> execute, PLUS UNE SEULE ligne de donnee
                                  (PAS terminee par une ligne vide - une
                                  seule ligne, point final).
            200 [command] OK  -> execute, PLUSIEURS lignes de donnees,
                                  terminees par une ligne vide.

        BUG REEL CORRIGE ICI : cette fonction ne geraiat QUE le cas 200
        (boucle jusqu'a une ligne vide) - toute commande renvoyant 201
        (ex: CG INVOKE, voir invoke_template ci-dessous - "201 CG OK\r\n"
        + le resultat + "\r\n", 2 lignes) laissait sa ligne de donnee NON
        LUE dans le tampon TCP. La commande AMCP suivante lisait alors
        cette ligne perimee au lieu de sa propre reponse, desynchronisant
        TOUT le reste des echanges - jusqu'a ce que le PING periodique de
        _ping_loop lise cette meme ligne perimee au lieu de "PONG" et
        declare la connexion perdue (symptome reellement observe par
        l'utilisateur : deconnexion de CasparCG quelques secondes apres
        avoir invoque une fonction de template, ex: le bouton SPONSOR IN).
        Toutes les commandes CG ADD/PLAY/STOP/NEXT/REMOVE/CLEAR/UPDATE
        deja utilisees ailleurs dans ce fichier renvoient 202 (aucune
        ligne supplementaire) - ce bug etait donc reste latent jusqu'a
        l'introduction du tout premier appel a une commande 201
        (invoke_template).
        """
        line = await asyncio.wait_for(self.reader.readline(), timeout=self.timeout)
        decoded = line.decode("utf-8").strip()

        code_str, _, message = decoded.partition(" ")
        code = int(code_str) if code_str.isdigit() else 0

        data: list[str] = []

        if code == 200:
            while True:
                data_line = await asyncio.wait_for(
                    self.reader.readline(), timeout=self.timeout
                )
                if data_line in (b"\r\n", b"\n", b""):
                    break
                data.append(data_line.decode("utf-8").rstrip("\r\n"))
        elif code == 201:
            data_line = await asyncio.wait_for(self.reader.readline(), timeout=self.timeout)
            data.append(data_line.decode("utf-8").rstrip("\r\n"))

        return AMCPResponse(code=code, message=message, data=data)

    #conversion de str comme prw et pgm en numero de channel
    def _channel_number(self, channel: str) -> int:
        if channel == "prw":
            return self.channel_preview

        if channel == "pgm":
            return self.channel_programm

        raise ValueError(f"Unknown channel '{channel}', expected 'prw' or 'pgm'")

    #foramt d'adresse du channel et layer ex 12 pgm = 12-2
    def format_address(self, channel: str, layer: int | None = None) -> str:
        """Build a "channel-layer" address from a "prw"/"pgm" alias.

        Both "prw" and "pgm" require an explicit layer. "prw" no longer
        forces every button onto a single shared layer 1 - each button
        previews on its own layer (the same "layer" it uses on "pgm"),
        so several buttons can be pre-composed in preview at once (see
        SceneButton._active_preview / _claim_preview).
        """
        channel_number = self._channel_number(channel)

        if layer is None:
            raise ValueError(f"layer is required for '{channel}'")

        return f"{channel_number}-{layer}"

    @staticmethod
    def _escape_amcp_string(value: str) -> str:
        """Escape a value to embed it inside a quoted AMCP parameter."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _escape_xml(value: str) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    #convertir un dictionnnaire en xml pour casparcg
    @staticmethod
    def format_template_data(data: dict[str, str]) -> str:
        """Turn a key/value dict into CasparCG's template data XML.

        Produces the classic <templateData>/<componentData> format:
            <templateData>
                <componentData id="key">
                    <data id="text" value="value"/>
                </componentData>
            </templateData>
        """
        fields = "".join(
            f'<componentData id="{CasparCG._escape_xml(key)}">'
            f'<data id="text" value="{CasparCG._escape_xml(value)}"/>'
            f"</componentData>"
            for key, value in data.items()
        )
        return f"<templateData>{fields}</templateData>"

    def _schedule_remove(
        self, channel: str, layer: int | None, cg_layer: int, address: str
    ) -> None:
        key = (address, cg_layer)

        existing = self._pending_removals.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

        self._pending_removals[key] = asyncio.create_task(
            self._delayed_remove(channel, layer, cg_layer, key)
        )

    async def _delayed_remove(
        self, channel: str, layer: int | None, cg_layer: int, key: tuple[str, int]
    ) -> None:
        try:
            await asyncio.sleep(self._remove_delay)
        except asyncio.CancelledError:
            return

        self._pending_removals.pop(key, None)
        await self.remove_template(channel, layer, cg_layer)

    # =====================================================================
    # FONCTIONS A UTILISER DEPUIS LE LOGICIEL
    #
    #   connect()          - se connecter a CasparCG
    #   disconnect()       - se deconnecter de CasparCG
    #   change_connection() - changer ip/port/channels et reconnecter aussitot
    #   play_template()    - charger/jouer un graphique (preview ou pgm)
    #   update_template()  - mettre a jour les donnees d'un graphique deja charge
    #   remove_template()  - retirer completement un graphique d'un layer
    #   invoke_template()  - invoquer une fonction exposee par un graphique deja charge
    #   stop()             - arreter un graphique (remove automatique 5s apres)
    #   clear()            - forcer le clear d'un layer ou d'un channel entier
    #   play_prw_to_pgm()  - jouer au programme le graphique actuellement en preview
    #   emergency_all_out() - clear complet des deux channels (preview + programme)
    # =====================================================================

    #connexion au serveur casparCG
    async def connect(self) -> str | None:
        """Open the AMCP connection, verify it with a PING, and start the
        background ping/reconnect loop.

        The loop is started even if this first attempt fails, so a
        CasparCG that isn't available yet at startup keeps being retried
        (every self.reconnect_delay seconds) instead of giving up.

        Returns None on success, or an error string on failure.
        """
        error: str | None = None

        async with self._lock:
            try:
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.ip, self.port),
                    timeout=self.timeout,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                self.connected = False
                error = str(exc)

        if error is None and not await self.ping():
            error = "No PONG received from CasparCG"

        if self._ping_task is None or self._ping_task.done():
            self._ping_task = asyncio.create_task(self._ping_loop())

        return error

    #deconnexion de casparCG
    async def disconnect(self) -> None:
        """Send BYE and close the AMCP connection."""
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

        for task in self._pending_removals.values():
            task.cancel()
        self._pending_removals.clear()

        async with self._lock:
            if self.writer is not None:
                try:
                    self.writer.write(b"BYE\r\n")
                    await asyncio.wait_for(self.writer.drain(), timeout=self.timeout)
                except (OSError, asyncio.TimeoutError):
                    pass

                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except OSError:
                    pass

            self.reader = None
            self.writer = None
            self.connected = False

    #change l'adresse/les channels et reconnecte immediatement avec les
    #nouvelles valeurs (utilise par la boite de dialogue Parametres, pour
    #appliquer un changement d'IP/port/channel sans redemarrer le logiciel)
    async def change_connection(
        self,
        ip: str | None = None,
        port: int | None = None,
        channel_preview: int | None = None,
        channel_programm: int | None = None,
    ) -> str | None:
        """Update connection settings and reconnect right away.

        Returns None on success, or an error string on failure (from the
        reconnect attempt) - same contract as connect().
        """
        await self.disconnect()

        if ip is not None:
            self.ip = ip
        if port is not None:
            self.port = port
        if channel_preview is not None:
            self.channel_preview = channel_preview
        if channel_programm is not None:
            self.channel_programm = channel_programm

        return await self.connect()

    #play un template sur le channel et layer donné avec les données fournies
    async def play_template(
        self,
        channel: str,
        template: str,
        data: dict[str, str] | None = None,
        layer: int | None = None,
        cg_layer: int = 1,
        play_on_load: bool = True,
    ) -> AMCPResponse:
        """Add (and by default play) a graphic template via CG ADD.

        channel/layer are formatted with format_address: both "prw" and
        "pgm" require an explicit layer.
        cg_layer is CasparCG's own CG stacking layer within that video
        layer (independent from the video layer itself).
        """
        address = self.format_address(channel, layer)
        template_escaped = self._escape_amcp_string(template)

        command = (
            f'CG {address} ADD {cg_layer} "{template_escaped}" {int(play_on_load)}'
        )

        if data:
            xml = self.format_template_data(data)
            command += f' "{self._escape_amcp_string(xml)}"'

        response = await self.send_command(command)

        if response.is_success:
            # A new graphic was just loaded on this layer: any pending
            # auto-remove scheduled from a previous stop() no longer applies.
            pending = self._pending_removals.pop((address, cg_layer), None)
            if pending is not None and not pending.done():
                pending.cancel()

        if channel == "prw" and response.is_success:
            #indexe par (layer, cg_layer) - un layer video de preview par
            #bouton (voir format_address), plus le cg_layer pour separer
            #le producer principal de celui d'un eventuel second_cg quand
            #les deux partagent le meme layer video (second_cg_layer omis)
            self._preview[(layer, cg_layer)] = {
                "template": template,
                "data": data,
                "cg_layer": cg_layer,
                "play_on_load": play_on_load,
            }

        return response

    #met a jour les donnees d'un template deja charge sur le channel et layer donné
    async def update_template(
        self,
        channel: str,
        data: dict[str, str],
        layer: int | None = None,
        cg_layer: int = 1,
    ) -> AMCPResponse:
        """Update the data of an already-loaded template via CG UPDATE."""
        address = self.format_address(channel, layer)
        xml = self.format_template_data(data)
        command = f'CG {address} UPDATE {cg_layer} "{self._escape_amcp_string(xml)}"'

        response = await self.send_command(command)

        if channel == "prw" and response.is_success and (layer, cg_layer) in self._preview:
            self._preview[(layer, cg_layer)]["data"] = data

        return response

    #retire completement un template (producer) sur le channel et layer donné
    async def remove_template(
        self, channel: str, layer: int | None = None, cg_layer: int = 1
    ) -> AMCPResponse:
        """Completely remove the template producer via CG REMOVE."""
        address = self.format_address(channel, layer)
        return await self.send_command(f"CG {address} REMOVE {cg_layer}")

    #invoque une fonction/methode exposee par le template deja charge sur
    #le channel/layer/cg_layer donne (ex: une fonction JS d'un template
    #HTML declenchant une animation - "sponsor_in" pour le bouton SPONSOR
    #IN du footer, voir interface.py/scene_button.py)
    async def invoke_template(
        self, channel: str, function: str, layer: int | None = None, cg_layer: int = 1
    ) -> AMCPResponse:
        """Invoke a template-exposed method via CG INVOKE.

        Syntax per the official AMCP protocol docs (server/docs/source/
        amcp/template-graphics-commands.rst) :
            CG [channel]-[layer] INVOKE [cg_layer] [method]
        with a documented example of "CG 1-1 INVOKE 1 start_intro" - the
        method name is a BARE, UNQUOTED token (unlike CG ADD/UPDATE's XML
        payload), so it must not contain whitespace.

        Does not add/remove/replace the producer - it only calls a
        function the currently loaded template already exposes (ex: a
        named animation trigger). Nothing happens (CasparCG itself
        answers with an error) if no producer is loaded on this
        channel/layer/cg_layer, or if the template doesn't expose this
        function.
        """
        address = self.format_address(channel, layer)
        return await self.send_command(f"CG {address} INVOKE {cg_layer} {function}")

    #stop un template et programme son remove automatique 5s plus tard
    async def stop(
        self, channel: str, layer: int | None = None, cg_layer: int = 1
    ) -> AMCPResponse:
        """Stop the graphic on a given channel/cg_layer via CG STOP.

        Schedules an automatic CG REMOVE self._remove_delay seconds later,
        unless a new graphic is loaded on the same channel/layer/cg_layer
        in the meantime (see play_template), in which case it is cancelled.
        """
        address = self.format_address(channel, layer)
        response = await self.send_command(f"CG {address} STOP {cg_layer}")

        if response.is_success:
            self._schedule_remove(channel, layer, cg_layer, address)

        return response

    #clear un layer ou un channel entier
    async def clear(self, channel: str, layer: int | None = None) -> AMCPResponse:
        """Force-clear a layer via the generic CLEAR command.

        Unlike stop()/CG CLEAR, this removes ANY producer on the layer
        (video, image, graphic...), not just CG templates. If layer is
        not given, the whole channel is cleared instead.
        """
        channel_number = self._channel_number(channel)
        address = str(channel_number) if layer is None else f"{channel_number}-{layer}"

        response = await self.send_command(f"CLEAR {address}")

        if channel == "prw" and response.is_success:
            if layer is None:
                #tout le channel preview a ete vide (ex: emergency_all_out)
                self._preview = {}
            else:
                #un seul layer video de preview vide (peut porter plusieurs
                #cg_layer, ex: template principal + second_cg d'un meme
                #bouton) - ne pas toucher au suivi des AUTRES layers de
                #preview, occupes par d'autres boutons
                for key in [k for k in self._preview if k[0] == layer]:
                    del self._preview[key]

        return response

    #action d'urgence : clear complet des deux channels (preview et programme)
    async def emergency_all_out(self) -> tuple[AMCPResponse, AMCPResponse]:
        """Force-clear both the preview and programme channels entirely."""
        prw_response = await self.clear("prw")
        pgm_response = await self.clear("pgm")

        return prw_response, pgm_response

    #permet d'envoyer le graphique actuellement au prw directement au pgm et de le clear sur le prw
    async def play_prw_to_pgm(self, layer: int, cg_layer: int = 1) -> AMCPResponse:
        """Play the graphic currently in preview to the programme, then
        clear it immediately on the preview.

        Replays the same template/data that was last sent to preview via
        play_template, onto the programme channel/layer given. (layer,
        cg_layer) together select WHICH preview producer to take - they
        must match what that producer was originally sent with (see
        SceneButton._take, which reuses its own layer/cg_layer for both
        the preview lookup and the programme destination). Unlike stop(),
        clear() removes the preview producer right away, with no stop
        animation and no delayed auto-remove.
        """
        preview = self._preview.get((layer, cg_layer))
        if preview is None:
            return AMCPResponse(code=0, message="Nothing in preview to take", data=[])

        response = await self.play_template(
            "pgm",
            preview["template"],
            data=preview["data"],
            layer=layer,
            cg_layer=cg_layer,
            play_on_load=preview["play_on_load"],
        )

        if response.is_success:
            await self.clear("prw", layer=layer)

        return response


if __name__ == "__main__":

    async def main():
        caspar = CasparCG("127.0.0.1", 5250, 1, 2)
        error = await caspar.connect()

        if error:
            print(f"Erreur de connexion : {error}")
        else:
            print("Connecte a CasparCG")

            response = await caspar.send_command("INFO")

            if response.is_error:
                print(f"Erreur INFO : {response.message}")
            else:
                print(f"{response.message}")
                for line in response.data:
                    print(line)

            await caspar.disconnect()

            print(caspar.format_template_data({"ip": "127.0.0.1", "bib": "15"}))

    asyncio.run(main())
