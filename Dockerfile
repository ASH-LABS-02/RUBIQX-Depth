FROM python:3.12-slim
# GDAL-backed rasterio wheels need these runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# CPU build by default; docker-compose.gpu.yml switches to the CUDA wheels
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir torch torchvision --index-url ${TORCH_INDEX} \
 && pip install --no-cache-dir -r requirements.txt
# bake the default model into the image so it runs offline
RUN python -c "from transformers import AutoImageProcessor, AutoModelForDepthEstimation as M; n='depth-anything/Depth-Anything-V2-Small-hf'; AutoImageProcessor.from_pretrained(n); M.from_pretrained(n)"
COPY . .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/scenes', timeout=4)" || exit 1
CMD ["python", "run.py", "--host", "0.0.0.0", "--no-browser"]
