FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt
# bake the default model into the image so it runs offline
RUN python -c "from transformers import AutoImageProcessor, AutoModelForDepthEstimation as M; n='depth-anything/Depth-Anything-V2-Small-hf'; AutoImageProcessor.from_pretrained(n); M.from_pretrained(n)"
COPY . .
EXPOSE 8000
CMD ["python", "run.py", "--host", "0.0.0.0", "--no-browser"]
