# Real-Network Verification for Public GitHub Source · Slice 41

Date: 2026-11-04. This document defines **reproducible live evidence**, not merely synthetic tests, for the read-only public GitHub Issues adapter introduced in Slice 40.

## Three deliberately distinct proof levels

| Evidence level | What was actually proven | What it does NOT prove |
| --- | --- | --- |
| `LIVE_TRANSPORT_VERIFIED` | The shipped `_public_issues_http` completed a real unauthenticated HTTPS GET to official `api.github.com`, parsed a bounded current provider response, and returned a genuine response from one public repository | That any source issue contains all app-specific labels, or that source events were actually committed |
| `VERIFIED_WITH_LIVE_LABELS` | The real response had at least one open **issue** with exactly one category label each for service, budget and timeline; the live data passed the shipped sanitizer and preview route | That events have been written to SQLite, or that any external provider was modified |
| `VERIFIED_IN_MEMORY_ONLY` | With explicit CLI opt-in, a genuinely matched public issue was mapped via the exact existing preview/commit machinery and recorded in a temporary `:memory:` SQLite ledger | Production persistence, authenticated operator approval, private GitHub access, or external write authorization |

If an HTTP GET succeeds but the first 15 returned issue/PR items do not include one eligible label triple, the correct report is `LIVE_TRANSPORT_VERIFIED`, `label_mapping=NO_ELIGIBLE_ISSUES_ON_FIRST_PAGE`. This is neither a mapping failure nor evidence of successful live issue ingestion. `--require-eligible` yields a separate nonzero status `LIVE_MAPPING_NOT_VERIFIED` in this scenario.

## Live read-only smoke in GitHub Actions

The independent job `.github/workflows/public-github-live-smoke.yml` runs on PR changes relevant to this capability or manual GitHub Actions dispatch. It invokes:

```bash
python scripts/probe_public_github_live.py --repo octocat/Hello-World
```

This path **does not patch or inject** fake HTTP data. The probe calls `PublicGitHubReadOnlyIntake.preview`, which uses the **same production HTTP adapter**. The request is a single HTTPS GET to `api.github.com:443`, with GitHub REST API version `2026-03-10`, strict TLS, 5-second socket timeout, no request token and no redirect following. It has the same one-page/15-item/512-KiB limits as normal use. GitHub may reply with 403/429 rate limiting or an outage; in that event, the job fails and no verification label is awarded.

The job **never calls GitHub mutation endpoints** and never uses `GITHUB_TOKEN` in application code. CI checkout has `persist-credentials: false`. Its Python setup is a standard-library-only, Python 3.12 runner with read-only repository permissions.

### Optional locally reproducible strict mapping test

For a genuinely **public**, consented repo with the required labels, operators can manually run:

```bash
python scripts/probe_public_github_live.py --repo OWNER/REPO --require-eligible
python scripts/probe_public_github_live.py --repo OWNER/REPO --check-ephemeral-import
```

The second command performs the source read followed by an explicit CLI opt-in **temporary local SQLite `:memory:` import**. It never writes to a persistent database and never changes GitHub. It reports only provenance class, checked repo, scanned/ignored/eligible counts, a sanitized status, and number of temporary event records. It never logs titles, bodies, author identities, access tokens or raw issue JSON.

Do not run the commands against private repositories or URLs, and do not treat an arbitrary public repo lacking `aas:service:*`, `aas:budget:*`, and `aas:timeline:*` as an integration acceptance fixture.

## Validation boundary

`tests/test_github_public_live_probe.py` exercises **11 offline unit tests** using injected fixture fetchers to verify output redaction, absence of false claims on zero eligible results, one-time no-write preview and explicit ephemeral import, the difference between provider failure and verified transport, and PR exclusion. These tests do not perform network calls.

`python -m unittest discover -s tests -v` remains network-isolated and deterministic. The separate live job is the only path that should be counted as **actual external provider evidence**. A green browser matrix and unit-test matrix alone cannot replace the live job, and a successful live transport probe does not upgrade a live label-mapping claim.

The GitHub official documentation confirms unauthenticated reads for public `List repository issues`, and current API support for `2026-03-10`: [GitHub Issues REST API](https://docs.github.com/en/rest/issues/issues) and [API Versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions).

## Security and user-impact limits

The local workbench is still unauthenticated; no public server, private user data, source issue content storage or external writes are permitted. The source adapter reads the first page only. Network and rate-limit failures are meaningful operational blockers and must not be quietly ignored as pass. A repository with at least one genuinely labeled open public issue is needed to establish true label-to-local-ledger E2E under real source conditions; this stage does not create public test issues or alter third-party repositories.

**Evidence must be recorded precisely in `docs/PROGRESS.md` with the exact PR HEAD and workflow run conclusion**. That record must distinguish transport, label mapping, and temporary local import outcomes.
