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
  call counts). The app never reads outside `v1/`.

Source: 공공데이터포털 (한국자산관리공사·국토교통부 제공), dataset 15157207.

## Output (`v1/`)

- `index.json`: `schemaVersion` 1, `generatedAt` (UTC `…Z`), `source`, and
  `regions` — all 16 current provinces in code order, each
  `{sido, name, file: "items/<sido>.json", count, sha256}` (an empty
  province has count 0).
- `items/<sido>.json`: `{schemaVersion, generatedAt, sido, items}`; items
  sorted by `key`. Field list and rules: `collector/validate.py`
  (`FIELDS`, D13 rules). `compare` is `{"kind": "none"}` until Phase 3.
- Compact JSON, keys sorted, final newline; same input gives the same
  bytes. Every file ≤ 5MB (index ≤ 256KB), same caps as the app.

## Run

```sh
python3 -m unittest discover collector/tests          # all tests, no network
python3 -m collector.collect --out out --first-run \
  --offline-fixtures collector/tests/fixtures/onbid_pages \
  --now 2026-10-31T20:10:00Z                           # dry run, no key
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
more than 20% or 500 dropped records fails the run; the daily cap
(900 calls per API, KST day) is checked before calling.

## Secrets (plan D28)

The key comes only from the environment variable `DATA_GO_KR_SERVICE_KEY`
(GitHub secret of the same name, set only on the Collect step). On Actions
the raw and URL-encoded forms are masked first. Errors show operation name,
HTTP status and resultCode only; URLs and query strings are never printed;
redirects are refused. `collector/tests/test_secrets.py` runs every failure
path with a fake key and checks stdout, stderr and files.

## Manual fallback (research Q11)

If Actions is blocked, run the same collector locally and push `data` by
hand (진호님). Keep same-day manual reruns to **10 or fewer**: a failed run
does not record its calls, so the daily cap cannot see them.

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

## Step 4c (진호님): repository settings

After creating the public repository `jinhom6225/public-auction-data` and
the secret `DATA_GO_KR_SERVICE_KEY`, add two rulesets (Settings → Rules →
Rulesets):

- `main`: block force pushes and deletion. No pull-request requirement
  (the workflow pushes the monthly keepalive commit directly).
- `data`: block deletion only (the workflow force-pushes it daily).

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
- `collector/tests/fixtures/golden/` — the dry-run output of the real
  pages at `--now 2026-10-31T20:10:00Z` (pinned by a test).
- The app's `test/fixtures/items_sample/real_day1/` is a copy of
  `golden/v1/`, and `items_sample/boundary_day2/` of
  `golden_boundary/day2/v1/`. Regenerate only on purpose:

  ```sh
  rm -rf collector/tests/fixtures/golden
  python3 -m collector.collect --out collector/tests/fixtures/golden --first-run \
    --offline-fixtures collector/tests/fixtures/onbid_pages --now 2026-10-31T20:10:00Z
  F=collector/tests/fixtures
  python3 -I collector/dev/make_boundary_fixture.py $F/boundary
  rm -rf $F/golden_boundary
  python3 -m collector.collect --out $F/golden_boundary/day1 --first-run \
    --offline-fixtures $F/boundary/day1 --now 2026-10-31T20:10:00Z
  python3 -m collector.collect --out $F/golden_boundary/day2 --prev $F/golden_boundary/day1 \
    --offline-fixtures $F/boundary/day2 --now 2026-11-01T20:10:00Z
  ```

## Known limits

- Plan A1: whether `cltrMngNo` stays the same when every round failed and
  the item is re-issued under a new notice could not be checked offline.
  `firstSeen` follows D16 (kept for 90 days); watch the first days after
  step 4c.
- A failed run does not record its calls in `cache/budget.json` (the data
  branch is untouched). Onbid uses about 49 of 900 calls a day, so keep
  same-day manual reruns to 10 or fewer.
- Monthly keepalive: on KST days 1–3, if `main` has no commit yet that
  month, the workflow commits `status/last_run.md`; a rejected push only
  warns (the data branch is already published) and is retried next day.
- Phase 3 (real-transaction comparison) plugs in at
  `collector/compare_hook.py`.
