# PyInstaller spec – one-folder Windows/macOS/Linux build with the web UI,
# model weights and sample scenes bundled so the app runs fully offline.
#   pip install pyinstaller
#   pyinstaller packaging/depthwizard.spec --noconfirm
# Put the fine-tuned checkpoint in models/da2-gamus-full before building.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hidden = [], [], []
for pkg in ("transformers", "rasterio", "safetensors", "tokenizers"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hidden += h
datas += [("../web", "web"), ("../models", "models"), ("../data/jobs", "data/jobs")]

a = Analysis(["../run.py"], pathex=[".."], binaries=binaries, datas=datas,
             hiddenimports=hidden + ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
                                     "uvicorn.lifespan.on", "app.server"],
             excludes=["matplotlib", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DepthWizard", console=True, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name="DepthWizard")
