# Mission Brief — Re-index After a Model Upgrade

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: [TODO.md](../../TODO.md), "Model upgrades hide existing faces" (Sourcery review of #5, 2026-11-11).

## Mission

> "Returns the pages containing images of the same character with high confidence."

Keep every indexed face searchable after the face model's runtime changes. Today a DeepFace or TensorFlow version change produces a new `model_profile` key. The search contract ([index-read.md](../../specs/001-image-indexer/contracts/index-read.md) §1) filters faces by the current profile, so every face stored under the old profile silently drops out of search, and the pipeline never re-embeds it: those images are `indexed`, not `pending`.

## Goals

1. **Detect the gap**: a run (or a check command) reports how many images have faces only under an older profile.
2. **Re-embed from stored bytes**: `indexed` images keep their normalized JPEG in `image.jpeg`, so re-indexing them needs no network access and no re-crawl.
3. **Swap atomically per image**: an image's old faces are replaced by its new ones in one transaction, so search never sees one image with faces from both profiles. This is a per-image guarantee only: see "Coverage during a re-index" below.
4. **Reuse the detect path**: re-indexing goes through the existing `plan_batches` → `detect` tasks (the `face_cpu` pool, batch caps and 3-attempt limit), not a second code path.

## Constraints

- **Same model rules**: new faces are stored under the new profile only; the read contract's profile filter stays as it is.
- **Schema**: `face` is `UNIQUE (image_id, face_index)`, so old faces must be deleted in the same transaction as the insert, or the key must include `model_profile_id`. Choosing between these is part of this mission.
- **`no_faces` images have no bytes**: the `jpeg_iff_kept` constraint drops the JPEG of an image with no faces. A new model might find faces in them, but only a re-download could tell. Out of scope unless the open question below decides otherwise.
- **Status invariants**: the `dims_iff_analyzed` and `indexed_at_iff_done` constraints must keep holding during and after the change.
- **Cost**: the first full index took 54.8 minutes of detection for about 4,700 images. A re-index costs the same, so it must be explicit (a DAG param or a separate DAG), never a surprise inside a normal run.
- **Coverage during a re-index** (Sourcery review of #6, 2026-11-14): search embeds the query with the installed, new runtime, and vectors from different runtimes must not be compared, so old-profile faces cannot be searched once the runtime changes. Until the re-index finishes, search covers only the images already re-embedded. The re-index must report its progress (images left), and the README must say that search is partial until it finishes. Keeping full coverage would need both runtimes installed side by side, which is out of scope.
- **What counts as a runtime change** (Sourcery review of #6, 2026-11-14): the profile key holds DeepFace's full 0.0.x version but only TensorFlow's major.minor (001 research R9). A TensorFlow patch upgrade keeps the key by design, so it triggers no re-index. If a patch release is ever shown to change embeddings, the key must include it; that is out of scope here.
- **Python, Airflow, Docker Compose**, as for the pipeline.

## Non-Goals

- Choosing a different face model. `model_profile.py` stays the single place for that.
- Keeping old-profile faces searchable alongside new ones.
- Re-downloading or re-normalizing images.

## Success Criteria

1. After bumping DeepFace or TensorFlow and running the re-index, a search finds the same pages it found before the upgrade, for a known face.
2. No image ends up with faces from two profiles. A query for this returns 0 rows.
3. The `summarize` table reports the images re-indexed and the faces replaced.
4. An interrupted re-index can be re-run safely: it resumes and finishes without duplicates.
5. The README documents when and how to run it.

## Open Questions

- **Trigger**: a DAG param (`reindex: true`), a separate `reindex` DAG, or automatic whenever the stored profile differs from the installed one?
- **Old profile rows**: keep `model_profile` rows for history, or delete them once no face references them?
- **`no_faces` images**: leave them as analyzed by the old model, or forget their URLs so the next crawl downloads and re-checks them?
- **Uniqueness**: delete old faces in the swap transaction, or widen the unique key to include the profile?
