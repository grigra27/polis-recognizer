# 0.3.6 release candidate — Pillow 13 validation

Validation date: 2026-11-10. Base: `1766773db90ff21e3b58ef9070904309418ee812`
(current main, version 0.3.5). Working branch:
`codex/p1-pillow13-20261110`.

Status: locally built and tested release candidate. The owner separately
authorized committing and pushing the P1 code and reports on 2026-11-10.
The delivery branch is
[`codex/p1-pillow13-20261110`](https://github.com/grigra27/polis-recognizer/tree/codex/p1-pillow13-20261110).
No PR, merge to main, tag, package upload, or production change is included
in that step. PyPI still serves 0.3.5, whose `Pillow<13` requirement prevents
the Polis Helper migration from being installed from the public index alone.

## Changes

- Version 0.3.6 in both `pyproject.toml` and `polis_recognizer/__init__.py`.
- `Pillow>=13.0.0,<14`; `pypdf[crypto]>=6.21.4,<7` supplies AES support.
- Image upload detection is restricted to PNG/JPEG before decoding using
  `Image.open(..., formats=("PNG", "JPEG"))`.
- EXIF orientation is normalized before image OCR. The decoded, oriented
  image is materialized before its source stream is closed.
- OpenCV's two-dimensional uint8 output uses Pillow's inferred grayscale
  mode in `Image.fromarray`.
- Deterministic regressions cover renamed XBM rejection before the XBM
  decoder or Tesseract runs, real PNG/JPEG EXIF, and non-fallback OpenCV
  preprocessing.
- Real runtime checks cover image OCR, text/scanned/multipage PDFs, pypdf,
  pdfplumber/pdfminer, Poppler, Tesseract `rus+eng`, AES-128/AES-256,
  page limits, malformed input, and byte-size limits. All inputs are synthetic;
  no corpus files were committed or uploaded.

## Results

| Check | Result |
|---|---|
| Python 3.12.13/macOS, full `pytest -q --cov` | 292 passed; 83.85% coverage |
| Python 3.11.15/macOS, full `pytest -q --cov` | 292 passed; 83.85% coverage |
| Python 3.12.13/Linux arm64, tests importing installed wheel | 292 passed; 83.85% coverage |
| Python 3.12.16/Linux amd64 under QEMU, image/PDF/OCR regressions and smoke checks | 15 passed |
| Python 3.12.16/native Linux amd64, full suite against installed wheel | 292 passed; 83.85% coverage |
| `ruff check .` | passed |
| `python -m build` (wheel built from sdist) | passed |
| `python -m twine check dist/polis_recognizer-0.3.6*` | passed |
| Python 3.11 standalone dependency resolution and `pip check` | passed |
| Polis Helper's complete Python 3.12 lock installed with candidate wheel | passed; `pip check` passed |

The existing coverage floor remains 73%. Tests do not relax byte/pixel/page
limits, the parser sandbox, or antivirus handling in the application.

Python 3.11 also resolved and exercised pdfplumber 0.11.10, pdfminer.six
20260107, and OpenCV 4.14.0.94. The application retains its separately
verified lock: pdfplumber 0.11.9, pdfminer.six 20251230, OpenCV 4.13.0.92.

The selected Linux cp312 Pillow wheel loaded libjpeg-turbo 3.2.0,
FreeType 2.14.3, OpenJPEG 2.5.4, WebP 1.6.0, libtiff 4.7.2,
and zlib 1.3.1/zlib-ng 2.3.3. AES and OpenCV checks execute their actual
native libraries. Linux tests copied the tests to a temporary directory and
imported `polis_recognizer` from `site-packages`, rather than the source tree.

## Distribution identity

- `dist/polis_recognizer-0.3.6-py3-none-any.whl`
  - SHA-256: `da7b3345f712edad511c3935048c2f9ed37e71e21f4f3144fdd2dc0ae73c384c`
- `dist/polis_recognizer-0.3.6.tar.gz`
  - SHA-256: `f55f208d829d29e1c3821e107e853987b93b52ba68dc2847bfa630a308ec6a62`

## Reproduction and remaining application gates

Build with Python 3.12 using `python -m build`, validate both artifacts with
`python -m twine check`, install the wheel with its `[test]` extra into a clean
external virtual environment, then run `pytest -q --cov` and `pip check`.
Install dependencies normally; dependency constraints were not bypassed.
Tesseract Russian/English packs and Poppler are required for the real smoke
checks, matching the existing package CI.

The full application evidence lives in the sibling Polis Helper checkout,
under `docs/operations/security_dependency_remediation_2026_11_10/`, with the
human report and audit index beside that directory. Its native Linux/PostgreSQL
workflow passes all 458 targets; frontend CI and real V8 coverage pass 963 tests.

Two additional application sandbox tests timed out in the historical amd64/QEMU
run. Native amd64 validation has now resolved that gap: [GitHub Actions run
42870650986](https://github.com/grigra27/polishelper/actions/runs/42870650986)
passed all 458 existing application workflow targets, including both sandbox
tests with unchanged limits. Additional application/security checks passed
192 tests with one existing skip. The full installed-wheel recognizer suite
passed all 292 tests at 83.85% coverage on native x86_64/Python 3.12.16, with
real Tesseract rus+eng and Poppler. The application used an isolated PostgreSQL
15 service; production was not accessed.

The run checked out recognizer commit
`5b63b0ce277977cc34932b82c18ad76b645da7ed` and application commit
`ebe0a9ae4d7c4792b834f7e0dde5e613b796235d`. Rebuilt wheel and sdist SHA-256
matched the distribution identities above on both macOS and native Linux.
The candidate application lock regenerated byte-identically twice; `pip check`
passed. Raw run logs and metadata are retained in the application evidence
`closure-attempt/` directory. This validates the source-built candidate,
while installation from the public package index still awaits a release.

Publishing 0.3.6 remains a separate external step, requiring separate
authorization. Once it is available, Polis Helper must repeat registry-only
lock regeneration, installation in a clean Python 3.12 environment, `pip check`,
and both default `pip-audit --strict` commands. The current strict PyPI audits
stop because version 0.3.6 has no public release metadata. Strict OSV audits of
the complete application input and lock report zero known vulnerabilities.
