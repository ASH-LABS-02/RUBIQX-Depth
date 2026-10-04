"""Bounded transactional calibration snapshots; failed edits roll back."""
from contextlib import contextmanager
import json
from pathlib import Path
import time
import uuid
import zipfile

ROOT_FILES = ["dsm.tif", "rdsm.tif", "dtm.tif", "ndsm.tif", "uncertainty.tif", "ensemble_spread.tif",
              "meta.json", "metrics.json", "dsm_raw.tif", "ndsm_raw.tif", "uncertainty_raw.tif", "ensemble_spread_raw.tif"]
MAX_BYTES = 256 * 1024 * 1024

class CalibrationHistoryFull(ValueError):
    pass


def _files(folder):
    return ROOT_FILES + [p.relative_to(folder).as_posix() for directory in ("viewer", "gcp_backup")
                        for p in (folder / directory).rglob("*") if p.is_file() and not p.is_symlink()]


def _restore(folder, archive):
    with zipfile.ZipFile(archive) as z:
        receipt = json.loads(z.read("receipt.json"))
        present = set(receipt["files"])
        for name in present:
            target = (folder / name).resolve()
            if folder.resolve() not in target.parents or name == "receipt.json" or name not in z.namelist():
                raise ValueError("Invalid calibration snapshot path")
        if z.testzip() is not None:
            raise ValueError("Corrupt calibration snapshot")
        for name in set(_files(folder)) - present:
            (folder / name).unlink(missing_ok=True)
        for name in present:
            target = (folder / name).resolve()
            if folder.resolve() not in target.parents or name == "receipt.json":
                raise ValueError("Invalid calibration snapshot path")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))


@contextmanager
def calibration_edit(folder):
    folder = Path(folder)
    files = [name for name in _files(folder) if (folder / name).is_file()]
    if sum((folder / name).stat().st_size for name in files) > MAX_BYTES:
        raise CalibrationHistoryFull("Calibration history exceeds 256 MB per edit; crop the scene before interactive calibration")
    history = folder / "calibration_history"
    history.mkdir(exist_ok=True)
    archive = history / f"{time.time_ns()}-{uuid.uuid4().hex[:6]}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        z.writestr("receipt.json", json.dumps({"created": time.time(), "files": files}))
        for name in files:
            z.write(folder / name, name)
    try:
        yield
    except Exception:
        _restore(folder, archive)
        archive.unlink()
        raise
    for stale in sorted(history.glob("*.zip"))[:-4]:
        stale.unlink()


def undo_calibration(folder):
    archives = sorted((Path(folder) / "calibration_history").glob("*.zip"))
    if not archives:
        raise ValueError("No calibration edit to undo")
    _restore(Path(folder), archives[-1])
    archives[-1].unlink()
    return {"ok": True, "remaining": len(archives) - 1}
