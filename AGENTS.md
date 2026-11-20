# AI maintainer context

This is the public, read-only Streamlit site and its registration-to-publication
automation. Do not reintroduce a local admin interface or commit registration
exports, private manifests, OAuth credentials, service-account keys, or tokens.

## Architecture

- `app.py` renders the public tournament page, brackets, photos, and PDFs.
- `tournament_public.json` and `fighter_photos/` are intentionally public output.
- `.github/workflows/public-tournament-sync.yml` runs on a manual dispatch, a
  Google Sheets form-submit dispatch, and a daily reconciliation schedule. It
  tests, syncs, validates, commits only public output, pushes to `main`, and
  records success in the private report spreadsheet.
- `automation/apps_script/on_form_submit.gs` installs the spreadsheet form
  trigger and dispatches the workflow to GitHub. Its
  `GITHUB_DISPATCH_TOKEN` belongs only in Apps Script Script Properties; never
  put it in this repository or logs.
- `automation/sync_public.py` exports the first worksheet and extracts its
  embedded Drive photo hyperlinks, validates registrations, downloads submitted
  photos, preserves valid unchanged brackets, and writes the private manifest
  and status to the `PhotoManifest` and `SyncStatus` tabs. Submitted photos are
  normalized to square JPEGs; local Haar face/eye detection aligns the crop
  when a face is found, with centered Pillow cropping as a safe fallback.
- `SyncConfig!A1` is `BRACKETS_LOCKED`; `B1` is `FALSE` while draws may change
  and `TRUE` when they must be frozen. A roster change while locked must stop
  publication.

## Product and privacy invariants

- The existing form is not to be changed. The organizer confirms its payment
  process already covers consent and its optional-photo notice says the photo
  may be shared publicly. Under that policy, submitting a photo is the
  publication-consent signal; omitting a photo is allowed.
- A submitted but missing, invalid, inaccessible, duplicated, oversized, or
  undecodable photo must fail the sync before publication rather than silently
  publish a partial update.
- Names and submitted photos are public site content. Detailed photo
  traceability and sync status stay in the private report spreadsheet.
- Never print participant names, photo URLs, Drive IDs, credentials, or private
  spreadsheet contents in GitHub logs or summaries. Public summaries may contain
  counts and non-identifying status only.
- Google authentication uses GitHub OIDC / Workload Identity Federation via
  `google-github-actions/auth@v3`; do not add service-account key files. The
  provider is restricted to this repository, `main`, and the sync workflow.
- Do not broaden Google IAM access or GitHub token permissions without a clear
  need. Do not force-push. Workflow publication must remain limited to
  `tournament_public.json` and `fighter_photos/`.

## Configuration and verification

GitHub Actions repository variables (values are deliberately not recorded here):
`GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `SPREADSHEET_ID`, and
`SYNC_MANIFEST_SPREADSHEET_ID`. The service account needs only access to the
source sheet, its submitted-photo Drive folder(s), and edit access to the private
report sheet; it must not receive a broad project role.

Install automation dependencies with
`python -m pip install -r automation/requirements.txt`. Run focused tests with
`python -m unittest discover -s automation -p "test_*.py"`. The public app has
separate dependencies in the root `requirements.txt`.

After changes, run the focused tests and `git diff --check`. Inspect the workflow
run in GitHub Actions before claiming a publication succeeded. A manual workflow
run has been verified; the first real form-submit trigger still needs an
end-to-end confirmation.

The human-facing `LISEZ-MOI.txt` and `automation/SETUP.txt` were intentionally
removed at the organizer's request. Keep this file as the sole maintainer
context; dependency manifests and runtime configuration are not documentation
and must remain.

The repository is public and its Git history is public. Removing a file from the
current tree does not erase its past versions; do not rewrite history as part of
routine cleanup.
