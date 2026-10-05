# Pi source snapshot

Upstream: https://github.com/earendil-works/pi

Release: v1.0.2, commit `cd32f7725fdbddbaecdff5b1e68491563394e0ca` (MIT).

`vendor/pi` is an unmodified snapshot from GitHub's archive for this fixed commit,
including the upstream license and development instructions. It has no embedded
Git repository. Source introduction does not require committing unrelated work.

The project workspace consumes only telemetry → ai → agent. `scripts/build-pi.mjs`
compiles these checked-in sources offline without fetching model catalog updates.
Generated `dist` and `node_modules` are ignored. Domain hooks and API adaptation
live in `services/pi-agent`, so this version can be replaced without carrying a
runtime fork. Future upstream edits should follow `vendor/pi/AGENTS.md` and be
documented here with a patch list and a new verified commit/archive digest.

Generated model metadata is not included in GitHub source archives. The separate
`vendor/pi-model-data` contains the JSON assets from the published
`@earendil-works/pi-ai@1.0.2` npm archive, copied to the ignored upstream data path
before compilation. This preserves offline builds and does not modify catalog
generator sources. Npm tarball SHA256:
`8ac8e5fabd65e0f0ecb8538b6211824859f5997594005d7a03e1f70b38438a17`.
