# HHS NOFO Metrics

HHS NOFO Metrics is a deterministic Python package for calculating versioned,
reproducible plain-language measurements from an HHS Notice of Funding
Opportunity. It supports source-native semantic HTML, tagged PDFs, and an
explicitly lower-confidence fallback for untagged PDFs.

The package is maintained by Agile Six and includes provisional, versioned
profiles derived from HHS NOFO measurement guidance. It is not an official HHS
policy interpretation, certification, or endorsement.

It produces measurements and provenance. It does not make clearance,
compliance, accessibility, legal, policy, or document-quality determinations,
and it does not perform generative-AI review.

## Supported product surface

- One public `analyze()` API accepting PDF inputs or an explicit HTML
  `SourceBundle`, with an explicitly required profile.
- A compact CLI for analysis and contract inspection.
- Six deterministic metrics: Word Count, average words per sentence,
  characters per word, Flesch Reading Ease, Flesch-Kincaid Grade Level, and
  passive-sentence percentage.
- Sentence-scope results also publish the count of sentence-bearing semantic
  blocks and average sentences per paragraph for consumer presentation.
- Versioned profiles and source-free result contracts.
- Reliability-aware PDF estimates for tagged-PDF workflows.
- Three versioned, built-in extraction adapters with a shared internal contract.

The current profiles are:

- `hhs-nofo-fy27-html@0.4.0`: provisional source-native behavior for semantic
  export HTML.
- `hhs-nofo-fy27-pdf-estimate@0.5.0`: recommended PDF-only profile for PDFs
  with a usable structure tree; returns numeric estimates with metric-specific
  reliability and include/exclude sensitivity when tagged content is unknown.
- `hhs-nofo-fy27-generic-pdf-estimate@0.4.0`: fallback for untagged or
  Word-saved PDFs; reconstructs conservative paragraph blocks from visual text
  lines and always labels configured metrics as low-reliability estimates.

For PDF-only workflows that need useful numeric estimates without a separate
manual review artifact, use the PDF-estimate profile. It preserves
source-declared block order, does not fall back to visual-line guessing, and
labels each value `high`, `moderate`, or `low` reliability based on extraction
coverage and the result's sensitivity to including or excluding unknown tagged
content.

If a PDF has no usable structure tree, callers may deliberately select the
generic-PDF estimate profile. It never presents reconstructed flat-PDF
paragraphs as authoritative source structure. Builder integrations should use
semantic HTML whenever it is available.

## Installation

Until a package registry release exists, install from a commit-pinned Git URL:

```bash
pip install "hhs-nofo-metrics @ git+https://github.com/mikec-ai/hhs-nofo-metrics.git@<commit-sha>"
```

For local development, use the editable installation shown below.

## Python API

```python
from hhs_nofo_metrics import NofoMetricsError, analyze

try:
    result = analyze(
        "notice.pdf",
        profile="hhs-nofo-fy27-pdf-estimate@0.5.0",
    )
except NofoMetricsError as error:
    print(error.to_dict())
else:
    payload = result.to_dict()
```

Consumers must inspect each metric's `status` before reading its value. A
calculated or estimated value means the identified method completed; it does
not mean the document passed a review.

`profile` is required. The package has no implicit universal measurement
policy; callers must select the source and reliability contract they intend.

For a tagged PDF estimate:

```python
result = analyze(
    "notice.pdf",
    profile="hhs-nofo-fy27-pdf-estimate@0.5.0",
)

reliability = result.metrics["word_count"].reliability
assert reliability is not None
print(reliability.level, reliability.sensitivity.to_dict())
```

For a system that already owns ordered semantic content, use its export HTML
instead of reverse-engineering reading order from a PDF:

```python
from hhs_nofo_metrics import SourceBundle, analyze

result = analyze(
    SourceBundle.from_html(export_html.encode("utf-8")),
    profile="hhs-nofo-fy27-html@0.4.0",
    adapter_config={"root_id": "download_target"},
    production_path="nofo_builder_export_html",
)
```

## CLI

```bash
hhs-nofo-metrics analyze notice.pdf \
  --profile hhs-nofo-fy27-pdf-estimate@0.5.0 \
  --output result.json

hhs-nofo-metrics analyze export.html \
  --source-kind html \
  --profile hhs-nofo-fy27-html@0.4.0 \
  --adapter-config html-adapter.json \
  --output result.json

hhs-nofo-metrics analyze tagged-notice.pdf \
  --profile hhs-nofo-fy27-pdf-estimate@0.5.0 \
  --output result.json

hhs-nofo-metrics analyze word-saved-notice.pdf \
  --profile hhs-nofo-fy27-generic-pdf-estimate@0.4.0 \
  --output result.json

hhs-nofo-metrics profiles list
hhs-nofo-metrics adapters list
hhs-nofo-metrics --version
```

Running `hhs-nofo-metrics` without arguments returns a compact content-first
view of the installed profiles, adapters, and useful next commands. Standard
output uses TOON 4.1 by default for concise agent-readable receipts, lists,
details, and errors. Add `--json` anywhere in a command to receive JSON instead:

```bash
hhs-nofo-metrics profiles list --json
```

The analysis result file itself is always written as versioned JSON. Terminal
output is only a receipt and does not replace that stable result artifact.

## Architecture and contracts

- [Changelog](CHANGELOG.md)
- [Current architecture](docs/ARCHITECTURE.md)
- [Measurement methodology](docs/METHODOLOGY.md)
- [Consumer-neutral integration contract](docs/INTEGRATION_CONTRACT.md)
- [Release process](docs/RELEASE_PROCESS.md)
- [NOFO Builder consumer guide](docs/consumers/NOFO_BUILDER.md)
- [NOFO Checker integration](docs/NOFO_CHECKER_INTEGRATION.md)
- [Public/private repository boundary](docs/PUBLIC_REPOSITORY_BOUNDARY.md)

## Development

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff format --check .
.venv/bin/ruff check .
python tools/check_public_repo.py
python -m build
python tools/check_wheel_contents.py dist/*.whl
```

Private PDFs, Word captures, screenshots, Gartner data, review packets, and
calibration receipts belong outside this repository. Run the public-boundary
check before every release candidate and the history-aware form before any
repository is made public.

## License

This project is available under the [MIT License](LICENSE).
