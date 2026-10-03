"""Download the pinned LoveDA candidate and store tensor-only local weights."""
import argparse
import json
from pathlib import Path

from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation

MODEL = "wu-pr-gw/segformer-b2-finetuned-with-LoveDA"
REVISION = "5c74556c08bebb5f45f50b6f78f61a62c5d220c7"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    processor = SegformerImageProcessor.from_pretrained(MODEL, revision=REVISION)
    model = SegformerForSemanticSegmentation.from_pretrained(
        MODEL, revision=REVISION, trust_remote_code=False, weights_only=True)
    args.out.mkdir(parents=True, exist_ok=True)
    processor.save_pretrained(args.out)
    model.save_pretrained(args.out, safe_serialization=True)
    (args.out / "provenance.json").write_text(json.dumps({
        "model_id": MODEL, "revision": REVISION,
        "training_overlap": "not documented by publisher",
        "license": "not specified by model publisher; research prototype only",
    }, indent=2))
    print(args.out.resolve())


if __name__ == "__main__":
    main()
