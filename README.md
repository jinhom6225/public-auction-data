# public-auction-data

Daily copy of Onbid (온비드) real-estate public-auction listings for the
unofficial Android app 공매 알리미 (`com.joe.publicauctionalert`). This app
is made by an individual developer. It is not affiliated with, endorsed
by or acting for 한국자산관리공사(캠코), 온비드 or 국토교통부.

- `main`: the collector (`collector/`, Python 3.11 standard library only,
  no `pip install`) and the workflow `.github/workflows/collect.yml`.
- `data` (force-pushed once a day, one commit): what the app reads —
  `v1/index.json`, `v1/items/<sido>.json` — plus the collector's own state
  `cache/seen.json` (day-to-day history) and `cache/budget.json` (daily
  call counts), the real-transaction cache `cache/rtms/<api>/<lawd>/<yyyymm>.json`
  (summary rows of the last 12 months per sgg and MOLIT API) and
  `cache/backfill_state.json` (progress per API). The app never reads
  outside `v1/`.

Source: 공공데이터포털 (한국자산관리공사·국토교통부 제공), datasets 15157207
(Onbid) and 15126468·15126467·15126464·15126466·15126465·15126463·15126470
(MOLIT real-transaction prices).

## Output (`v1/`)

- `index.json`: `schemaVersion` 1, `generatedAt` (UTC `…Z`), `source`, and
  `regions` — all 16 current provinces in code order, each
  `{sido, name, file: "items/<sido>.json", count, sha256}` (an empty
  province has count 0).
- `items/<sido>.json`: `{schemaVersion, generatedAt, sido, items}`; items
  sorted by `key`. Field list and rules: `collector/validate.py`
  (`FIELDS`, D13 rules).
- `compare` (research §3.4, `collector/compare.py`, numbers in
  `collector/data/compare_params.json`), exact shapes checked by
  `validate.py`:
  - `{"kind": "none"}`
  - `{"kind": "sameBuilding", "months": 12, "n", "medianPerM2", "estimate",
    "ratio", "deals"}` — **apartment and officetel items only** (plan A9,
    진호님 2026-11-01; rowhouse items, including 도시형생활주택/기타주거용, get
    sameDong), same building key (PNU without the 11th digit), area ±10%
    (inclusive), at least 3 deals; the median is over all `n` matches,
    `deals` shows the 10 newest (`n` ≥ 3, `deals` = min(n, 10)); `ratio` is
    null for share items, a null `lowestBid` or a ratio above 100,000; an
    estimate above 1e13 falls back to sameDong.
  - `{"kind": "sameDong", "months": 12, "n", "medianPerM2", "deals"}` — same
    읍면동, area ±30%, land: same 지목, commercial/factory: same building use
    (tables in `compare_params.json`; an unmapped usage code gets none); the
    10 newest deals, `n` = number shown. The unit is the **읍면동**, not the
    리: an item in a 리 compares with deals anywhere in its 읍·면 (MOLIT
    rows of 읍면 often name the 리, but the item's `umd` is the 읍면 code).
    Without `umd` the PNU's 읍면동 is used.
  - In both kinds the rounded `medianPerM2` must be 1..1e13 won/㎡,
    otherwise the comparison falls back (sameBuilding → sameDong → none).
  - `deals[]` = `{ym: "2026-09", m2, floor (int or null, basement < 0), amount (원)}`.
  Raw MOLIT rows never reach `v1/`.
- Compact JSON, keys sorted, final newline; same input gives the same
  bytes. Every file ≤ 5MB (index ≤ 256KB), same caps as the app.

## Run

```sh
python3 -m unittest discover collector/tests          # all tests, no network
python3 -m collector.collect --out out --first-run \
  --offline-fixtures collector/tests/fixtures/onbid_pages \
  --now 2026-10-31T20:10:00Z                           # dry run, no key (Onbid + MOLIT fixtures)
python3 -m collector.validate_output out
```

`collect.py` exit codes: 0 success · 1 call failure or missing key ·
2 validation failure · 3 collector bug (unexpected exception; the
traceback is logged with the key redacted — fix the code, do not just
rerun) · 64 usage error. On any failure nothing under `--out` changes.

Failure rules (plan D29): a list page that fails twice fails the run; the
national item count falling more than 40% versus yesterday fails the run
(**starting value — fix it after 7 days of `totalCount` observation once
the workflow runs, step 4c**); a province that had at least 50 items
yesterday and falls by more than 60% fails the run; a province file over
5MB fails the run; the same list row (`cltrMngNo`, `pbctCdtnNo`) on two
pages fails the run; a failed page is retried once after 3 seconds;
more than 20% or 500 dropped records fails the run (an item whose name
carries a phone number or a link is dropped as `contact in name` — the app
has the same rule; 0 of 8,763 names in the Phase 1 dump match); the daily cap
(900 calls per API, KST day) is checked before calling.

Real-transaction rules (plan D17, D29 ②, Phase 3 + review): MOLIT is
called only with current, selectable LAWD_CD codes (old codes answer zero
rows) for the (API, sgg) pairs that have items. Every day the current and
previous month of every pair are fetched again; missing months of the
12-month window are backfilled (pairs with more items first, recent months
first) until the API's daily cap (900). Time limits from the collector
start: no new backfill month after 40 minutes, no MOLIT call at all after
50 minutes (those months keep yesterday's file, not counted as failed), so
the 60-minute job timeout holds. Months 2–11 back are never fetched again
(late cancellations are accepted). The seven APIs run side by side (one
thread and client each, 4 tps each, 28 tps in total under the 30 tps
limit); a page is retried once after 2 seconds.

Failures: a failed month keeps yesterday's file. The run fails (exit 1)
when more than 10% of the attempted months failed, or more than half of
one API's. Once failures pass 10% of the planned months, or a thread hits
a collector bug, every API thread stops calling. Gateway code 22 or HTTP
429 (provider's daily limit — 22 is the expected data.go.kr code, not yet
observed) stops that API for the day without counting as a failure. Auth
codes 30–32 (key not registered/expired/not approved) stop that API
without a retry and count as failed, so the run fails. A pair (API, sgg)
whose items are gone keeps its cached months for 14 days
(`backfill_state.json` `pairsLastSeen`), then drops them.

Bodies: at most 2MB, no NUL byte (UTF-16/32 never reach a parser), strict
UTF-8 starting with `<` (no BOM, no other `encoding=`, no XML-illegal
control characters), no DOCTYPE/ENTITY in the decoded text, at most 1,000
rows a page and 10,000 a month (checked on the first page), at most 60
fields per deal. Reading yesterday's cache is bounded too: 1MB per month
file, 10,000 rows, 40,000 files, nesting depth, a real `fetched` date
(within the month to today), no symlinks; each run logs
`rtms manifest: files=… rows=… sha256=…`.

Land API ceiling (runbook): the daily refresh needs 2 calls per land pair,
so above **450 land pairs** the 900 cap is spent on refresh alone (today
237) and the lowest-priority pairs are not even refreshed. Before that,
raise the land cap toward its real limit of 1,000/day or ask for the
production account (운영계정).

## Secrets (plan D28)

The key comes only from the environment variable `DATA_GO_KR_SERVICE_KEY`
(GitHub secret of the same name, set only on the Collect step). On Actions
the raw and URL-encoded forms are masked first. Errors show operation name,
HTTP status and resultCode only; URLs and query strings are never printed;
redirects are refused. `collector/tests/test_secrets.py` runs every failure
path with a fake key and checks stdout, stderr and files.

## Manual fallback (research Q11)

If Actions is blocked, run the same collector locally and push `data` by
hand (진호님). Keep same-day manual reruns to **10 or fewer** for Onbid: a
failed run does not record its calls, so the daily cap cannot see them.
MOLIT is tighter: one failed run can already have spent most of the land
API's real 1,000-call day, so after a failed run that got past the Onbid
list, rerun **once** at most the same day. If the provider's limit is hit
anyway (code 22 or HTTP 429), the collector stops that API for the day and continues
tomorrow.

**The `data` ruleset refuses this push.** Once the `data` branch has the
"restrict updates" rule with GitHub Actions as the only bypass actor
(step 4c), a manual `git push --force … data:data` is rejected. Before
pushing by hand, either add yourself (repository admin) to that rule's
bypass list or set the ruleset to *Disabled*; push; then remove yourself
from the bypass list / set it back to *Active* right away.

```sh
cd ~/Documents/public-auction-data
read -rs DATA_GO_KR_SERVICE_KEY && export DATA_GO_KR_SERVICE_KEY   # never paste it into chat or files
python3 -m unittest discover collector/tests
git fetch --depth 1 origin data && git worktree add --detach prev FETCH_HEAD
python3 -m collector.collect --out out --prev prev      # --first-run if there is no data branch
python3 -m collector.validate_output out
(cd out && git init -q -b data && git add v1 cache \
  && git commit -q -m "data: manual $(date -u +%Y-%m-%dT%H:%MZ)" \
  && git push --force git@github-jinhom:jinhom6225/public-auction-data.git data:data)
git worktree remove prev && rm -rf out
unset DATA_GO_KR_SERVICE_KEY
```

`git worktree remove prev` needs no `--force`: the collector only reads
`prev/`, so the worktree stays clean (checked: git refuses only when
`prev/` holds modified or untracked files — then something wrote into it;
look before using `--force`). If a run stopped half-way and `prev/` is
still there, remove it the same way before the next `git worktree add`;
if the folder was deleted by hand, run `git worktree prune`.

## Step 4c (진호님): repository settings

After creating the public repository `jinhom6225/public-auction-data` and
the secret `DATA_GO_KR_SERVICE_KEY`, add two rulesets (Settings → Rules →
Rulesets):

- `main`: block force pushes and deletion. No pull-request requirement
  (the workflow pushes the monthly keepalive commit directly).
- `data`: block deletion, and restrict pushes to GitHub Actions (add the
  GitHub Actions app as the only bypass actor of a "restrict updates"
  rule) so nobody else can plant a crafted cache; the workflow
  force-pushes it daily. This also refuses your own manual push (Manual
  fallback): add yourself to the bypass list or disable the ruleset only
  for that push, then restore it.

The workflow job runs only on `refs/heads/main` and pins its actions by
commit SHA (`actions/checkout` v4.4.0, `actions/setup-python` v5.6.0,
verified with `git ls-remote`); Dependabot (`.github/dependabot.yml`)
proposes updates monthly.

## Data files (`collector/data/`)

- `regions.json` — current legal-dong codes (시도 16, 시군구 269 incl. 13
  parent cities that cannot be chosen, 읍면동 5,067, 리 15,209). Built from
  the same CSV as the app bundle `assets/data/regions.json`
  (public_auction_alert `tool/data/legal_dong_20260630.csv`, 공공데이터포털
  15063424, sha256 recorded in the file):

  ```sh
  python3 -m collector.build_regions \
    ~/Documents/public_auction_alert/tool/data/legal_dong_20260630.csv \
    collector/data/regions.json 2026-06-30
  ```

  Update yearly together with the app bundle and `region_map.json` (next
  file expected 2027-07).
- `region_map.json` — old → current codes for Onbid PNUs (29/46 → 12,
  42 → 51, 45 → 52 one-to-one; 인천 28110/28140/28260 and 화성 41590 split,
  resolved by dong name). Address fields are used first (plan A3).
- `usage_groups.json` — Onbid usage small codes → seven groups (plan A8).

## Test fixtures

- `collector/tests/fixtures/raw/` — Phase 1 raw Onbid responses (no key).
- `collector/tests/fixtures/onbid_pages/onbid_p1.json` — 90 items / 514
  rows of 서울·전남광주·인천 cut from the Phase 1 full dump (not committed
  anywhere) with `collector/dev/cut_fixture.py`.
- `collector/tests/fixtures/boundary/day{1,2}/` — hand-made edge cases
  over two days (`collector/dev/make_boundary_fixture.py`): names of
  601/602/603 characters, failCount 108, 2999 schedule on both or one
  side, private price, share, five rounds, price drop and rise, item gone
  and item new. `golden_boundary/day1` = first run at
  `2026-10-31T20:10:00Z`, `golden_boundary/day2` = `--prev day1` at
  `2026-11-01T20:10:00Z` (pinned by a test).
- `collector/tests/fixtures/onbid_pages/rtms_<api>_<lawd>_<yyyymm>.xml` —
  MOLIT pages for the sggs of those items, cut from the A9 measurement
  responses (2025-11 … 2026-10, uncommitted `.local/a9/raw/`) down to the
  items' 읍면동 with `collector/dev/cut_rtms_fixture.py` (same comparison
  result as the full page). Months without a file are served empty.
- `collector/tests/fixtures/raw/rtms_*.xml` — Phase 1 raw MOLIT responses
  (parser tests).
- `collector/tests/fixtures/golden/` — the dry-run output of the real
  pages at `--now 2026-10-31T20:10:00Z` (pinned by a test). `v1/` and
  `cache/*.json` are kept byte for byte; the `cache/rtms/` month files are
  pinned by `rtms_manifest.json` (file count, row count, digest, non-empty
  files) instead of being copied.
- The app's `test/fixtures/items_sample/real_day1/` is a copy of
  `golden/v1/`, and `items_sample/boundary_day2/` of
  `golden_boundary/day2/v1/`. Regenerate only on purpose:

  ```sh
  python3 -I collector/dev/make_boundary_fixture.py collector/tests/fixtures/boundary   # if changed
  python3 -I collector/dev/regen_golden.py
  ```
- `collector/dev/measure_a9.py` — the plan A9 re-measurement (real MOLIT
  calls with a key file, raw pages kept in the gitignored `.local/`).

## Known limits

- Plan A1: whether `cltrMngNo` stays the same when every round failed and
  the item is re-issued under a new notice could not be checked offline.
  `firstSeen` follows D16 (kept for 90 days); watch the first days after
  step 4c.
- A failed run does not record its calls in `cache/budget.json` (the data
  branch is untouched). Onbid uses about 49 of 900 calls a day, so keep
  same-day manual reruns to 10 or fewer (MOLIT: once, see above).
- Right after step 4c many items show `compare: none` until the backfill
  has filled the 12-month window (see public_auction_alert `data_spike.md`
  §13 for the call and day estimates).
- Monthly keepalive: on KST days 1–3, if `main` has no commit yet that
  month, the workflow commits `status/last_run.md`; a rejected push only
  warns (the data branch is already published) and is retried next day.
- Real-transaction comparison: `collector/compare_hook.py` (stage),
  `rtms_client.py` (calls, strict XML, summary rows), `rtms_cache.py`,
  `backfill.py`, `pnu.py`, `compare.py`.
