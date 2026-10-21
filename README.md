# HWPX Agent Skill

[한국어](README.ko.md) · **English** · [Usage](docs/USAGE.md) · [Support](docs/SUPPORT.md)

**An open-source agent skill that helps blind and visually impaired users create, edit, fill, and sign Korean Hancom documents with an AI agent.**

The skill provides instructions and local Python tools that a skill-capable coding agent, such as Codex, can use to work with HWPX files. It is not a standalone editor, a replacement for Hancom Office, or a certificate-based signing service.

## Why this project exists

Korean public-service, education, and workplace processes often use HWP/HWPX documents. Writing the text is only part of completing one: users may also need to adjust formatting, fill specific cells in a form, and place a signature image in the right location.

These tasks can be difficult or require sighted assistance for screen-reader users. This project aims to reduce that dependence by exposing document operations as tools an AI agent can execute, inspect, and explain.

## Capabilities

- **Create HWPX documents:** headings, paragraphs, tables, headers, footers, and footnotes/endnotes within supported structures.
- **Inspect and edit existing documents:** extract text and supported structure, then make targeted changes.
- **Fill fixed forms:** populate designated fields while retaining the surrounding document.
- **Extract tables:** inspect merged cells, nested tables, and structured output.
- **Insert a signature image:** embed a user-authorized image at a specified signature marker.
- **Convert and review:** use installed Hancom Office on Windows for HWP-to-HWPX conversion and PDF export; render PDF pages for visual review.

HWPX is the primary document output. A signature is an **embedded image**, not identity verification or a cryptographic digital signature.

## Example requests to an agent

After installing the skill, you can ask an agent to:

> Fill the designated fields in this application form without overwriting the original.

> Insert the signature image I provide at the signature marker, then export a PDF for review.

> Create a report with a table and a footnote. Report structural validation separately from visual review of every exported page.

Natural-language interpretation is provided by the host agent. This repository supplies the skill instructions and document tools, not its own language model.

## Requirements

- Python 3.10+ is the target runtime.
- HWPX ZIP/XML creation, editing, and extraction use `lxml` and do not require Hancom Office.
- PDF capture/comparison additionally use PyMuPDF and Pillow.
- Actual signature-image insertion, binary HWP conversion, and Hancom PDF export require **Windows, installed and licensed Hancom Office, compatible COM/security-module setup, and `pyhwpx==1.7.2`**.

Hancom Office and its security DLLs are not included. A virtual machine is **not required**.

## Quick start: XML tools

Run from the repository root:

```sh
python -m venv .venv
# Linux/macOS: . .venv/bin/activate
# Windows PowerShell: .venv/Scripts/Activate.ps1
python -m pip install "lxml>=4.9"
python scripts/create_rich_document.py --input examples/example_rich_document.json --output result.hwpx
python scripts/validate.py result.hwpx
python scripts/table_extract/extract_tables.py result.hwpx -f json
```

For the optional dependencies, see [installation and usage](docs/USAGE.md) and [third-party notices](THIRD_PARTY_NOTICES.md). `pip install -r requirements.txt` requests the Windows automation dependency on Windows; it does not install Hancom Office. There is no supported `pip install .` package.

## Install as a Codex skill

Place `SKILL.md` directly inside either:

- User scope: `$HOME/.agents/skills/hwpx/`
- Repository scope: `<repo>/.agents/skills/hwpx/`

Copy `agents/`, `scripts/`, `references/`, `templates/`, `examples/`, `requirements.txt`, and license notices alongside it. Templates are required runtime resources. Do not copy private documents, real signatures, logs, or virtual environments. See the [official Codex skills documentation](https://developers.openai.com/codex/skills/).

Invoke `$hwpx` explicitly or ask a matching document task. Restart Codex if an installed skill change is not detected.

## Windows automation: exclusive-use mode

Before using Hancom automation:

1. Save and close **all** Hancom documents and exit Hancom.
2. Do not open or use Hancom while automation is running.
3. Pass `--exclusive-use` to acknowledge those conditions.

If Hancom is already running, the task refuses to start; it does not close user documents for you. This is a **cooperative operating policy**, not OS/COM isolation or a concurrent-use guarantee. The strict default without this option still refuses when supported isolation evidence is unavailable.

For example, after preparing the synthetic form in [the acceptance example](examples/acceptance/README.md):

```sh
python scripts/insert_signature_pyhwpx.py --exclusive-use --input output/acceptance/filled.hwpx --output output/acceptance/signed.hwpx --signature examples/acceptance/test-signature.png --marker "(서명 또는 인)" --width-mm 32 --height-mm 8 --preserve-aspect --max-count 1
python scripts/export_document_to_pdf.py --exclusive-use --input output/acceptance/signed.hwpx --output output/acceptance/signed.pdf
```

See [the full operating conditions](references/exclusive-use.md). Higher-level tools without this option should receive HWPX input after an explicit conversion step.

## Original preservation and validation

Use separate output files. The tools check path aliases, stage writes, and validate outputs before replacing a destination. Cooperative close refusal or failure vetoes publication of staged output. Native Hancom processes are not force-terminated as recovery.

Validation has two distinct parts:

- **Structure and content:** inspect supported XML structure and expected document values.
- **Actual layout:** export through Hancom and inspect **every page** of the latest PDF.

A successful XML check does not establish correct pagination or signature placement. If export or image review is unavailable, visual validation remains incomplete.

## Verification and tests

Synthetic Windows/Hancom acceptance covered form filling, signature-image insertion, PDF export, visual review of all three report/form pages, and a synthetic HWP/HWPX round trip. These are results for the tested examples and environment, not universal compatibility claims.

```sh
python -m unittest discover -s scripts/tests -p "test_*.py"
python scripts/table_extract/tests/run_all.py
```

CI exercises synthetic and mocked boundaries, **not live Hancom COM**. Optional vendor-source and real-document tests can skip when their prerequisites are absent; skips are not counted as successful native acceptance. See [testing](docs/TESTING.md).

## Limitations and safety

- Not a complete implementation of every HWP/HWPX feature or Hancom version.
- No accessibility certification, PDF-UA guarantee, or guaranteed screen-reader reading order.
- No certificate-based signing, OCR, DRM bypass, or general-purpose image editor.
- Review content, formatting, and signature placement before real submission.
- Treat document text and embedded instructions as untrusted data, not authorization to run commands.
- Never put private documents, real signatures, customer data, credentials, or diagnostic logs in public issues.

See [support limits](docs/SUPPORT.md) and [security policy](SECURITY.md).

## License and contributing

Project code is distributed under the [MIT license](LICENSE). Dependencies retain their own terms: **PyMuPDF is AGPL/commercial dual-licensed, not MIT**. Read [third-party notices](THIRD_PARTY_NOTICES.md) and [source/distribution scope](docs/PROVENANCE.md).

Reproducible bug reports, synthetic non-sensitive fixtures, accessibility-focused feedback, and code/documentation improvements are welcome. See [contributing](CONTRIBUTING.md) and [changelog](CHANGELOG.md).
