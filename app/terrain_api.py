"""Bounded on-demand terrain/texture windows and exact coordinate probes."""
import io
import json

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window
from PIL import Image
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from depthwizard.coordinates import lonlat


class CoordinateQuery(BaseModel):
    points: list[tuple[float, float]] = Field(min_length=1, max_length=128)


def create_terrain_router(resolve_scene, scene_lock):
    router = APIRouter()

    @router.post("/api/scenes/{job_id}/coordinates")
    def coordinates(job_id: str, body: CoordinateQuery):
        folder = resolve_scene(job_id)
        with scene_lock:
            meta = json.loads((folder / "viewer/meta.json").read_text())
        try:
            points = lonlat(meta, body.points)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"points": points, "method": "source affine + PROJ CRS transform", "crs": "EPSG:4326"}

    @router.get("/api/scenes/{job_id}/tiles/{level}/{col}/{row}/{kind}")
    def tile(job_id: str, level: int, col: int, row: int, kind: str,
             size: int = Query(129, ge=33, le=257)):
        folder = resolve_scene(job_id)
        if not 0 <= level <= 7 or not 0 <= col < 2**level or not 0 <= row < 2**level or kind not in ("height", "texture"):
            raise HTTPException(422, "Invalid terrain tile")
        source = folder / "dsm.tif"
        if not source.is_file():
            raise HTTPException(409, "Terrain streaming requires a metric DSM raster")
        n = 2**level
        if kind == "height":
            with scene_lock, rasterio.open(source) as src:
                # Adjacent tiles share their source pixel centres.
                dx, dy = (src.width - 1) / n / (size - 1), (src.height - 1) / n / (size - 1)
                window = Window(col * (src.width - 1) / n + .5 - dx / 2,
                                row * (src.height - 1) / n + .5 - dy / 2, dx * size, dy * size)
                heights = src.read(1, window=window, out_shape=(size, size), masked=True,
                                   boundless=True, resampling=Resampling.bilinear).filled(np.nan).astype("<f4")
            return Response(heights.tobytes(), media_type="application/octet-stream",
                            headers={"X-Grid-W": str(size), "X-Grid-H": str(size)})
        # Saved optical texture is already bounded to 4096 px; do not decode the full upload per tile.
        with scene_lock, Image.open(folder / "viewer/texture.jpg") as image:
            w, h = image.size
            patch = image.crop((col * w / n, row * h / n, (col + 1) * w / n, (row + 1) * h / n))
            patch = patch.resize((min(512, max(32, patch.width)), min(512, max(32, patch.height))), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            patch.save(output, "JPEG", quality=90)
        return Response(output.getvalue(), media_type="image/jpeg")

    return router
