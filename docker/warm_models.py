"""Build-time step: download and cache the RetinaFace and Facenet512 weights.

Runs inside `docker build`, so pipeline runs never download models (research R9).
Weights land under `$DEEPFACE_HOME/.deepface/weights`.
"""

import logging

import numpy as np
from deepface import DeepFace

from face_index import model_profile

log = logging.getLogger(__name__)


def main() -> None:
    DeepFace.build_model(model_profile.MODEL)
    blank = np.zeros((160, 160, 3), dtype=np.uint8)
    DeepFace.represent(
        img_path=blank,
        model_name=model_profile.MODEL,
        detector_backend=model_profile.DETECTOR,
        enforce_detection=False,
        align=model_profile.ALIGN,
        normalization=model_profile.NORMALIZATION,
    )
    log.info("weights cached for %s", model_profile.current_profile().key)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
