# Backend HID pour python-elgato-streamdeck (bibliotheque officielle,
# package pip "streamdeck") base sur le module `hid` (cython-hidapi,
# package pip "hidapi" de Trezor) plutot que sur le transport "libusb"
# fourni par la bibliotheque elle-meme.
#
# POURQUOI : le transport "libusb" de python-elgato-streamdeck charge un
# hidapi.dll NATIF via ctypes (voir StreamDeck/Transport/LibUSBHIDAPI.py) -
# aucun package pip Windows ne fournit ce .dll (contrairement a Linux/macOS,
# ou le systeme en fournit deja un). Sans lui, DeviceManager() leve
# ProbeError("Probe failed to find any functional HID backend.") des la
# construction, meme avec un Stream Deck reellement branche. Le module
# `hid` (pip install hidapi, PAS le meme package que "streamdeck"),
# en revanche, EMBARQUE hidapi directement dans son extension Cython
# compilee (hid.cp313-win_amd64.pyd) et fonctionne immediatement sans
# DLL externe - confirme par une enumeration HID reelle detectant un
# Stream Deck XL connecte (VID Elgato 0x0FD9, PID XL 0x008F).
#
# Implemente donc l'interface StreamDeck.Transport.Transport.Transport
# (memes methodes que StreamDeck.Transport.LibUSBHIDAPI.LibUSBHIDAPI, dont
# le code sert de reference pour la semantique exacte) par-dessus le module
# `hid` : notamment read()/read_feature() qui doivent rester NON BLOQUANTS
# (set_nonblocking(1) applique une fois a l'ouverture) et renvoyer None
# plutot qu'une valeur vide quand rien n'est disponible, comme le fait
# LibUSBHIDAPI.Device.read.
from __future__ import annotations

import hid as _hid

from StreamDeck.Transport.Transport import Transport, TransportError


class HidapiCythonTransport(Transport):
    class Device(Transport.Device):
        def __init__(self, device_info: dict):
            self.device_info = device_info
            self._device: _hid.device | None = None

        def open(self) -> None:
            if self._device is not None:
                return

            device = _hid.device()
            device.open_path(self.device_info["path"])
            device.set_nonblocking(1)
            self._device = device

        def close(self) -> None:
            if self._device is not None:
                self._device.close()
                self._device = None

        def is_open(self) -> bool:
            return self._device is not None

        def connected(self) -> bool:
            return any(
                d["path"] == self.device_info["path"]
                for d in _hid.enumerate(
                    self.device_info["vendor_id"], self.device_info["product_id"]
                )
            )

        def vendor_id(self) -> int:
            return self.device_info["vendor_id"]

        def product_id(self) -> int:
            return self.device_info["product_id"]

        def path(self) -> str:
            return self.device_info["path"]

        def write_feature(self, payload: bytes) -> int:
            if self._device is None:
                raise TransportError("No HID device.")
            return self._device.send_feature_report(payload)

        def read_feature(self, report_id: int, length: int) -> bytes:
            if self._device is None:
                raise TransportError("No HID device.")
            return bytes(self._device.get_feature_report(report_id, length))

        def write(self, payload: bytes) -> int:
            if self._device is None:
                raise TransportError("No HID device.")
            return self._device.write(payload)

        def read(self, length: int) -> bytes | None:
            if self._device is None:
                raise TransportError("No HID device.")
            data = self._device.read(length)
            if not data:
                return None
            return bytes(data)

    @staticmethod
    def probe() -> None:
        #le module `hid` est deja importe avec succes en tete de fichier -
        #rien a verifier de plus (contrairement a LibUSBHIDAPI.probe(), qui
        #doit charger dynamiquement une DLL a cet instant precis)
        pass

    def enumerate(self, vid: int, pid: int) -> list["HidapiCythonTransport.Device"]:
        return [
            HidapiCythonTransport.Device(info)
            for info in _hid.enumerate(vid or 0, pid or 0)
        ]


#cree un DeviceManager utilisant HidapiCythonTransport (voir plus haut)
#plutot que "libusb" (hidapi.dll natif absent de ce systeme) ou "dummy".
#Passe par transport="dummy" a la construction (Dummy.probe() ne fait
#rien, aucun risque materiel) puis remplace l'attribut `transport` -
#DeviceManager.enumerate() reste inchange (table PID -> classe de
#peripherique officielle d'Elgato, voir StreamDeck/DeviceManager.py), seule
#la maniere de PARLER au peripherique change.
def get_device_manager():
    from StreamDeck.DeviceManager import DeviceManager

    manager = DeviceManager(transport="dummy")
    manager.transport = HidapiCythonTransport()
    return manager
