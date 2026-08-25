# -*- mode: python ; coding: utf-8 -*-
#
# Specification PyInstaller pour compiler Biath Control Center en
# executable Windows. auto-py-to-exe est une interface graphique par-
# dessus PyInstaller : le plus simple et le plus reproductible reste de
# compiler directement ce fichier en ligne de commande :
#
#     pyinstaller BiathControlCenter.spec
#
# L'executable et ses fichiers annexes sont generes dans dist/BiathControlCenter/
# (mode "onedir" : demarrage plus rapide qu'un exe "onefile" qui doit se
# re-extraire dans un dossier temporaire a chaque lancement - preferable
# pour un logiciel utilise en direct pendant une diffusion).
#
# Voir paths.py pour la distinction ressources embarquees (lecture seule,
# assets/ + templates.json "de base") / fichiers utilisateur modifiables
# (%APPDATA%\BiathControlCenter, crees/copies au premier lancement).
#
# icon.ico (racine du projet, a cote de ce .spec) DOIT EXISTER pour que la
# compilation reussisse - utilise a DEUX endroits distincts :
#   - icon=... plus bas (EXE) : icone du fichier .exe, gravee dans
#     l'executable au moment de la compilation.
#   - datas (juste en dessous) : copie embarquee accessible A
#     L'EXECUTION, lue par main.py (resource_path("icon.ico")) pour
#     servir de favicon dans l'onglet du navigateur - un besoin distinct
#     du precedent, PyInstaller ne rend pas l'icone .exe accessible
#     comme un fichier normal une fois compilee.
# Doit etre au format .ico (PyInstaller/Windows n'acceptent pas un .png
# directement pour l'icone d'un .exe).
#
# console=True est un choix delibere : main.py affiche l'etat de connexion
# CasparCG/WebSocket via print() au demarrage, et NiceGUI/asyncio peuvent
# logger des erreurs sur stdout/stderr en cours d'utilisation (voir par
# exemple l'erreur ConnectionResetError deja rencontree en developpement).
# En mode "windowed" (console=False), sys.stdout/stderr valent None dans
# un executable PyInstaller sans console - un print() ou un traceback peut
# alors lever une exception au lieu de s'afficher, ce qui est pire que
# d'avoir une fenetre de console en plus. A repasser a False seulement si
# le logiciel est juge stable et que cette diagnostic-ability n'est plus
# necessaire (et alors verifier qu'aucun print()/traceback ne peut plus
# etre declenche).

from PyInstaller.utils.hooks import collect_all

datas = [
    ("assets", "assets"),
    ("templates.json", "."),
    ("icon.ico", "."),
]
binaries = []
hiddenimports = []

# NiceGUI embarque ses propres ressources statiques (JS/CSS/polices/
# favicon) qui doivent etre incluses telles quelles dans l'executable,
# sinon l'interface ne se charge pas une fois compile (piege documente
# par NiceGUI elle-meme pour tout packaging PyInstaller/Nuitka).
nicegui_datas, nicegui_binaries, nicegui_hiddenimports = collect_all("nicegui")
datas += nicegui_datas
binaries += nicegui_binaries
hiddenimports += nicegui_hiddenimports

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BiathControlCenter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BiathControlCenter",
)
