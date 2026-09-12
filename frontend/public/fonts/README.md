# Bundled web fonts

Self-hosted so the application makes no outbound request for typography. It
runs air-gapped: a remote stylesheet `@import` is render-blocking, so offline
it delayed first paint by the full connection timeout on every launch and then
fell back to system fonts anyway.

Regenerate with the vendoring script (build-time, needs network access). Only
the `latin` and `latin-ext` subsets are taken; the interface is English and the
other subsets roughly triple the payload.

| Family | Weights | Used for |
|---|---|---|
| Inter | 300–800 | Interface text |
| JetBrains Mono | 400, 500, 600 | Monospace, transcripts, code |
| Caveat | 400, 600, 700 | `font-display` |
| Kalam | 300, 400, 700 | `font-hand` |
| Architects Daughter | 400 | `font-blueprint` |
| Patrick Hand | 400 | `font-marker` |

All six are licensed under the SIL Open Font License 1.1, which permits
bundling and redistribution. The license text is in `OFL.txt`.

The administrator console served by the backend uses a system font stack
instead of a bundled family, so that it needs no static font mount.
