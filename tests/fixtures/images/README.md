# Test images

Most test images are generated in code (`tests/images.py`), so no binary fixtures are committed.

The real-model smoke test (`tests/smoke/test_deepface_smoke.py`, marker `model`) optionally uses
two photos placed here, which are not committed:

- `face_one.jpg`: exactly one clearly visible face
- `face_two.jpg`: exactly two clearly visible faces

Use images you are licensed to use (for example CC0 photos). Without them, those two cases are
skipped; the no-face case and the stack smoke test (`scripts/smoke.sh`) still run the real model.
