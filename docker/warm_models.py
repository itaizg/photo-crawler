"""Build-time step: download and cache the RetinaFace and Facenet512 weights.

Runs inside `docker build`, so pipeline runs never download models (research R9).
Weights land under `$DEEPFACE_HOME/.deepface/weights`.
"""

import numpy as np
from deepface import DeepFace

from face_index import model_profile


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
    print("weights cached for", model_profile.current_profile().key)


if __name__ == "__main__":
    main()
