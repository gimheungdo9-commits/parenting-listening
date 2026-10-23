"""
누적 장부 저장소

- 비밀 설정(secrets)에 구글 시트 정보가 있으면 → 팀 공용 구글 시트에 저장
  (인터넷 서버에 올려도 지워지지 않고, 팀원 모두 같은 장부를 씁니다)
- 없으면 → 지금처럼 이 컴퓨터의 data 폴더에 저장

구글 시트 안의 탭(시트) 4개
  누적장부    : 필터를 통과한 모든 글 (회차ID로 어느 수집에서 왔는지 구분)
  수집이력    : 누가 언제 무엇을 몇 건 모았는지
  제외된글    : 걸러진 글과 이유
  직접읽을글  : 회원전용 카페 글 — 팀원이 시트에서 바로 읽음여부·메모를 채웁니다
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd

DATA_DIR = "data"
ARCHIVE_DIR = os.path.join(DATA_DIR, "archives")
HISTORY_FILE = os.path.join(DATA_DIR, "collection_history.csv")
MASTER_FILE = os.path.join(DATA_DIR, "master_database.csv")
EXCLUDED_FILE = os.path.join(DATA_DIR, "excluded.csv")
READ_LIST_FILE = os.path.join(DATA_DIR, "read_list.csv")

MASTER_COLS = ["회차ID", "채널", "검색식", "제목", "본문요약", "본문전체", "본문상태", "작성자",
               "작성일", "URL", "수집일시", "홍보의심"]
HISTORY_COLS = ["수집일시", "작업자", "채널", "검색식", "요청건수", "총수집", "광고제외", "정보글제외",
                "반려동물제외", "기업블로그제외", "무관제외", "중복제외", "본문광고제외", "최종저장",
                "직접읽을글", "파일명"]
EXCLUDED_COLS = ["회차ID", "채널", "검색식", "제목", "작성자", "URL", "제외사유"]
READ_LIST_COLS = ["카페명", "제목", "요약", "URL", "검색식", "회차ID", "읽음여부", "메모"]

TAB_MASTER = "누적장부"
TAB_HISTORY = "수집이력"
TAB_EXCLUDED = "제외된글"
TAB_READ = "직접읽을글"

SHEET_CELL_LIMIT = 45000  # 구글 시트 한 칸 최대 글자 수(5만)보다 여유 있게


def _rows(df: pd.DataFrame, cols: List[str]) -> List[List[str]]:
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols].fillna("").astype(str)
    return [[v[:SHEET_CELL_LIMIT] for v in row] for row in df.values.tolist()]


def _empty(cols: List[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=cols)


# ---------------------------------------------------------------------------
# 1) 이 컴퓨터의 data 폴더에 저장 (지금까지 방식)
# ---------------------------------------------------------------------------
class LocalStore:
    kind = "local"
    label = "이 컴퓨터 (data 폴더)"

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(ARCHIVE_DIR, exist_ok=True)

    def _read(self, path: str, cols: List[str]) -> pd.DataFrame:
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
                for c in cols:
                    if c not in df.columns:
                        df[c] = ""
                return df
            except Exception:
                return _empty(cols)
        return _empty(cols)

    def _append(self, path: str, df: pd.DataFrame, cols: List[str], newest_first=False):
        if df.empty:
            return
        old = self._read(path, cols)
        new = df.copy()
        for c in cols:
            if c not in new.columns:
                new[c] = ""
        combined = pd.concat([new, old] if newest_first else [old, new], ignore_index=True)
        combined.to_csv(path, index=False, encoding="utf-8-sig")

    def load_master(self) -> pd.DataFrame:
        return self._read(MASTER_FILE, MASTER_COLS)

    def load_history(self) -> pd.DataFrame:
        return self._read(HISTORY_FILE, HISTORY_COLS)

    def load_excluded(self) -> pd.DataFrame:
        return self._read(EXCLUDED_FILE, EXCLUDED_COLS)

    def load_read_list(self) -> pd.DataFrame:
        return self._read(READ_LIST_FILE, READ_LIST_COLS)

    def append_master(self, df):
        self._append(MASTER_FILE, df, MASTER_COLS)

    def append_history(self, df):
        self._append(HISTORY_FILE, df, HISTORY_COLS, newest_first=True)

    def append_excluded(self, df):
        self._append(EXCLUDED_FILE, df, EXCLUDED_COLS)

    def append_read_list(self, df):
        self._append(READ_LIST_FILE, df, READ_LIST_COLS)

    # 회차별 zip은 이 컴퓨터에 파일로도 보관
    def save_archive(self, filename: str, data: bytes):
        with open(os.path.join(ARCHIVE_DIR, filename), "wb") as f:
            f.write(data)

    def get_archive(self, filename: str) -> Optional[bytes]:
        path = os.path.join(ARCHIVE_DIR, filename)
        if filename and os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        return None

    def clear_cache(self):
        pass


# ---------------------------------------------------------------------------
# 2) 팀 공용 구글 시트에 저장
# ---------------------------------------------------------------------------
class SheetStore:
    kind = "sheet"
    label = "팀 공용 구글 시트"

    def __init__(self, spreadsheet):
        self.sh = spreadsheet
        self._ws: Dict[str, Any] = {}
        self._cache: Dict[str, pd.DataFrame] = {}
        for tab, cols in ((TAB_MASTER, MASTER_COLS), (TAB_HISTORY, HISTORY_COLS),
                          (TAB_EXCLUDED, EXCLUDED_COLS), (TAB_READ, READ_LIST_COLS)):
            self._ensure_tab(tab, cols)

    def _ensure_tab(self, tab: str, cols: List[str]):
        try:
            ws = self.sh.worksheet(tab)
        except Exception:
            ws = self.sh.add_worksheet(title=tab, rows=1000, cols=len(cols))
        header = ws.row_values(1)
        if not header:
            ws.update(range_name="A1", values=[cols])
        elif header != cols:
            # 예전 칸 구성이면 빠진 칸만 오른쪽 끝에 추가 (기존 데이터는 그대로)
            missing = [c for c in cols if c not in header]
            if missing:
                new_header = header + missing
                if ws.col_count < len(new_header):
                    ws.add_cols(len(new_header) - ws.col_count)
                ws.update(range_name="A1", values=[new_header])
        self._ws[tab] = ws

    CACHE_SECONDS = 120  # 화면을 누를 때마다 시트를 읽지 않도록 2분간 기억

    def _load(self, tab: str, cols: List[str]) -> pd.DataFrame:
        hit = self._cache.get(tab)
        if hit is not None and time.time() - hit[0] < self.CACHE_SECONDS:
            return hit[1].copy()
        values = self._ws[tab].get_all_values()
        if not values:
            df = _empty(cols)
        else:
            header, body = values[0], values[1:]
            width = len(header)
            body = [(r + [""] * width)[:width] for r in body]
            df = pd.DataFrame(body, columns=header)
            for c in cols:
                if c not in df.columns:
                    df[c] = ""
        self._cache[tab] = (time.time(), df)
        return df.copy()

    def _append(self, tab: str, df: pd.DataFrame, cols: List[str]):
        if df is None or df.empty:
            return
        header = self._ws[tab].row_values(1) or cols
        self._ws[tab].append_rows(_rows(df, header), value_input_option="RAW")
        self._cache.pop(tab, None)

    def load_master(self):
        return self._load(TAB_MASTER, MASTER_COLS)

    def load_history(self):
        df = self._load(TAB_HISTORY, HISTORY_COLS)
        return df.iloc[::-1].reset_index(drop=True)  # 최신이 위로

    def load_excluded(self):
        return self._load(TAB_EXCLUDED, EXCLUDED_COLS)

    def load_read_list(self):
        return self._load(TAB_READ, READ_LIST_COLS)

    def append_master(self, df):
        self._append(TAB_MASTER, df, MASTER_COLS)

    def append_history(self, df):
        self._append(TAB_HISTORY, df, HISTORY_COLS)

    def append_excluded(self, df):
        self._append(TAB_EXCLUDED, df, EXCLUDED_COLS)

    def append_read_list(self, df):
        self._append(TAB_READ, df, READ_LIST_COLS)

    # 인터넷 서버에는 파일을 오래 둘 수 없어서, 회차별 zip은 시트 내용으로 다시 만듭니다.
    def save_archive(self, filename, data):
        pass

    def get_archive(self, filename):
        return None

    def clear_cache(self):
        self._cache.clear()


# ---------------------------------------------------------------------------
# 어느 저장소를 쓸지 고르기
# ---------------------------------------------------------------------------
def _service_account_info(secrets) -> Optional[Dict[str, Any]]:
    """
    비밀 설정에서 구글 서비스 계정 정보를 꺼냅니다. 두 가지 방식 모두 지원:
      1) GCP_SERVICE_ACCOUNT_JSON = '''{ ...받은 json 파일 내용 통째로... }'''
      2) [gcp_service_account] 표 형식
    """
    raw = secrets.get("GCP_SERVICE_ACCOUNT_JSON", "") if hasattr(secrets, "get") else ""
    if raw:
        return json.loads(raw)
    try:
        table = secrets.get("gcp_service_account")
    except Exception:
        table = None
    if table:
        return dict(table)
    return None


def make_store(secrets) -> "tuple":
    """
    반환: (저장소, 경고문구 또는 None)
    구글 시트 설정이 있는데 연결에 실패하면, 이 컴퓨터 저장으로 넘어가고 이유를 알려 줍니다.
    """
    sheet_url = ""
    try:
        sheet_url = secrets.get("SHEET_URL", "") or ""
    except Exception:
        sheet_url = ""
    if not sheet_url:
        return LocalStore(), None
    try:
        info = _service_account_info(secrets)
        if not info:
            return LocalStore(), "SHEET_URL은 있는데 서비스 계정 정보(GCP_SERVICE_ACCOUNT_JSON)가 없어요."
        import gspread  # 구글 시트 연결 도구
        client = gspread.service_account_from_dict(info)
        return SheetStore(client.open_by_url(sheet_url)), None
    except json.JSONDecodeError:
        return LocalStore(), "GCP_SERVICE_ACCOUNT_JSON 내용이 깨졌어요. json 파일 내용을 다시 통째로 붙여넣어 주세요."
    except ImportError:
        return LocalStore(), "구글 시트 도구가 설치되지 않았어요. 검은 창에서 pip install gspread 를 실행해 주세요."
    except Exception as e:
        name = type(e).__name__
        hint = ""
        if "PermissionError" in name or "403" in str(e):
            hint = " 시트를 서비스 계정 이메일에 '편집자'로 공유했는지 확인해 주세요."
        elif "NotFound" in name or "404" in str(e):
            hint = " SHEET_URL 주소가 맞는지 확인해 주세요."
        elif "APIError" in name:
            hint = " 구글 클라우드에서 Google Sheets API를 '사용'으로 켰는지 확인해 주세요."
        return LocalStore(), "구글 시트 연결 실패 (" + name + ")." + hint


# 앱 전체에서 하나의 저장소를 같이 씁니다.
_STORE = None
_STORE_WARNING = None


def set_store(store, warning=None):
    global _STORE, _STORE_WARNING
    _STORE, _STORE_WARNING = store, warning


def get_store():
    global _STORE
    if _STORE is None:
        _STORE = LocalStore()
    return _STORE


def get_store_warning():
    return _STORE_WARNING
