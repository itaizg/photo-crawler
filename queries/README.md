# Query photos

Put the photos you want to search for here. This folder is shared with the `face-matcher`
container (mounted at `/work/queries`, with `/work` as the working directory), so a path such as
`queries/alice.jpg` means the same file on your machine and inside the container.

```bash
docker compose run --rm face-matcher queries/alice.jpg
```

`--list-faces` writes an annotated copy next to the photo, as `<name>.faces.jpg`. It stays here
after the container exits, and running it again overwrites it.

Everything in this folder except this README and `eval/` is git-ignored. Use photos you are
licensed to use. See the README section "How to use the face matching script".

## `eval/`: the committed evaluation set

`queries/eval/` is committed: the label sets that measure the search, one openly licensed photo
per neutral label (`photos/person-a.jpg`, `photos/absent-a.jpg`, never a real name), and
`attribution.csv` with each photo's source, photographer and licence. CI checks that every photo
is attributed with an allowed licence and a source link that cannot name the person. See the
README section "Search accuracy evaluation" and
[specs/003-evaluation-set/contracts/eval-set-files.md](../specs/003-evaluation-set/contracts/eval-set-files.md).
