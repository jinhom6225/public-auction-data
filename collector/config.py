"""Fixed numbers of the collector (plan D10, D13, D17, D27-D29, A6).

The app has the same limits (`kMaxItemsFileBytes`, `kMaxIndexBytes`, D13
record rules); Phase 4 tests that both sides agree.
"""

from __future__ import annotations

import datetime
import os

KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
KEY_PARAM = "serviceKey"

SCHEMA_VERSION = 1
KST = datetime.timezone(datetime.timedelta(hours=9), "KST")

# D10: per-file caps, same values as the app.
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_INDEX_BYTES = 256 * 1024

# A6: Onbid list request.
ONBID_OPERATION = "getRlstCltrList2"
ONBID_URL = "https://apis.data.go.kr/B010003/OnbidRlstListSrvc2/getRlstCltrList2"
ONBID_PAGE_SIZE = 1000
ONBID_MAX_PAGES = 200
ONBID_MAX_BYTES = 4 * 1024 * 1024
ONBID_TPS = 8
ONBID_PROPERTY_DIVISIONS = ("0007", "0010", "0005", "0002", "0003", "0006", "0008", "0011", "0013")
ONBID_PARAMS = {
    "resultType": "json",
    "pvctTrgtYn": "N",
    "dspsMthodCd": "0001",
    "prptDivCd": ",".join(ONBID_PROPERTY_DIVISIONS),
}
HTTP_TIMEOUT_SECONDS = 30

# D17: daily call cap per API (KST day). Phase 3 adds the RTMS APIs.
DAILY_CAPS = {"onbid": 900}

# D29 (3): fail when the national item count drops more than this versus
# yesterday. Starting value; fixed after 7 days of totalCount observation
# following step 4c.
MAX_DAILY_DECREASE = 0.40

# D13: records the collector itself drops. More than this share, or more
# than DROP_LIMIT_COUNT, means the source format changed: fail the run.
DROP_LIMIT_SHARE = 0.20
DROP_LIMIT_COUNT = 500

# D16: forget an item this many days after it was last seen.
SEEN_RETENTION_DAYS = 90

# A5 / D32: deep link to the item page and the only allowed host.
ONBID_HOST = "www.onbid.co.kr"
ONBID_ITEM_URL = "https://www.onbid.co.kr/op/cltrpbancinf/cltrdtl/CltrDtlController/mvmnCltrDtl.do"
MAX_URL_LENGTH = 2048

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SOURCE_LABEL = "공공데이터포털 (한국자산관리공사·국토교통부 제공)"
SOURCE_URL = "https://www.data.go.kr/data/15157207/openapi.do"
