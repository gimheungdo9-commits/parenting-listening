import os
import io
import re
import zipfile
import pandas as pd
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from typing import List, Dict, Any, Tuple

DATA_DIR = "data"
ARCHIVE_DIR = os.path.join(DATA_DIR, "archives")
HISTORY_FILE = os.path.join(DATA_DIR, "collection_history.csv")
MASTER_FILE = os.path.join(DATA_DIR, "master_database.csv")

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.replace("m.blog.naver.com", "blog.naver.com")
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

def load_master_urls() -> set:
    ensure_data_dir()
    if os.path.exists(MASTER_FILE):
        try:
            df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig")
            return set(df["URL"].dropna().apply(normalize_url))
        except Exception:
            return set()
    return set()

def load_history() -> pd.DataFrame:
    ensure_data_dir()
    cols = ["수집일시", "작업자", "채널", "검색식", "요청건수", "총수집", "광고제외", "정보글제외", "무관제외", "중복제외", "최종저장", "파일명"]
    if os.path.exists(HISTORY_FILE):
        try:
            df = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig")
            for c in cols:
                if c not in df.columns:
                    df[c] = ""
            return df
        except Exception:
            return pd.DataFrame(columns=cols)
    return pd.DataFrame(columns=cols)

def get_archive_file(filename: str):
    """보관소에서 특정 회차의 zip 파일 데이터를 읽어옵니다."""
    ensure_data_dir()
    path = os.path.join(ARCHIVE_DIR, filename)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    return None

def save_to_master_and_history(valid_items: List[Dict[str, Any]], history_record: Dict[str, Any]):
    ensure_data_dir()
    if valid_items:
        new_df = pd.DataFrame(valid_items)
        if os.path.exists(MASTER_FILE):
            try:
                old_df = pd.read_csv(MASTER_FILE, encoding="utf-8-sig")
                combined = pd.concat([old_df, new_df], ignore_index=True)
                combined.drop_duplicates(subset=["URL"], keep="first", inplace=True)
                combined.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
            except Exception:
                new_df.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
        else:
            new_df.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")

    hist_df = load_history()
    new_hist_row = pd.DataFrame([history_record])
    updated_hist = pd.concat([new_hist_row, hist_df], ignore_index=True)
    updated_hist.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")

def simple_df_to_markdown(df: pd.DataFrame) -> str:
    headers = list(df.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |"
    ]
    for _, row in df.iterrows():
        clean_row = [str(val).replace("\n", " ").replace("|", "-") for val in row]
        lines.append("| " + " | ".join(clean_row) + " |")
    return "\n".join(lines)

def filter_and_process(items: List[Dict[str, Any]], filters: Dict[str, Any], exclude_master_dup: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    ad_words = [w.strip() for w in filters.get("ad_words", []) if w.strip()]
    info_words = [w.strip() for w in filters.get("info_words", []) if w.strip()]
    req_words = [w.strip() for w in filters.get("req_words", []) if w.strip()]
    min_length = filters.get("min_length", 30)

    master_urls = load_master_urls() if exclude_master_dup else set()
    valid_dict = {}
    excluded_items = []
    stats = {"total": len(items), "ad": 0, "info": 0, "irrelevant": 0, "short": 0, "dup": 0, "master_dup": 0, "saved": 0}

    for item in items:
        title = item.get("제목", "")
        snippet = item.get("본문요약", "")
        full_text = f"{title} {snippet}"
        url = item.get("URL", "")
        norm_url = normalize_url(url)

        if exclude_master_dup and norm_url in master_urls:
            stats["master_dup"] += 1
            excluded_items.append({**item, "제외사유": "이전 팀원 수집 누적 중복 (마스터 DB에 이미 존재)"})
            continue

        found_ad = [w for w in ad_words if w in full_text]
        if found_ad:
            stats["ad"] += 1
            excluded_items.append({**item, "제외사유": f"광고 의심 ({', '.join(found_ad)})"})
            continue

        found_info = [w for w in info_words if w in title]
        if found_info:
            stats["info"] += 1
            excluded_items.append({**item, "제외사유": f"정보성 글 의심 ({', '.join(found_info)})"})
            continue

        is_reddit = "Reddit" in item.get("채널", "")
        check_words = req_words
        if is_reddit:
            check_words = req_words + ["baby", "toddler", "infant", "kid", "child", "mom", "dad", "sleep", "food", "feed", "cry"]

        if check_words:
            found_req = [w for w in check_words if w.lower() in full_text.lower()]
            if not found_req:
                stats["irrelevant"] += 1
                excluded_items.append({**item, "제외사유": "육아 필수어 미포함"})
                continue

        if len(snippet.strip()) < min_length:
            stats["short"] += 1
            excluded_items.append({**item, "제외사유": f"최소 글자 수 미달 ({len(snippet.strip())}자)"})
            continue

        if norm_url in valid_dict:
            stats["dup"] += 1
            existing = valid_dict[norm_url]
            if item["검색식"] not in existing["검색식"]:
                existing["검색식"] = f"{existing['검색식']} | {item['검색식']}"
        else:
            valid_dict[norm_url] = item

    valid_items = list(valid_dict.values())
    stats["saved"] = len(valid_items)
    return valid_items, excluded_items, stats

def create_zip_package(valid_items: List[Dict[str, Any]], excluded_items: List[Dict[str, Any]], time_tag: str, worker: str = "") -> Tuple[bytes, str]:
    """결과 zip 파일을 만들고, 보관소 폴더에도 영구 저장합니다."""
    ensure_data_dir()
    zip_buffer = io.BytesIO()

    cols = ["채널", "검색식", "제목", "본문요약", "본문전체", "작성자", "작성일", "URL", "수집일시"]
    valid_df = pd.DataFrame(valid_items)
    for c in cols:
        if c not in valid_df.columns: valid_df[c] = ""
    valid_df = valid_df[cols]

    ex_cols = ["채널", "검색식", "제목", "URL", "제외사유"]
    excluded_df = pd.DataFrame(excluded_items)
    for c in ex_cols:
        if c not in excluded_df.columns: excluded_df[c] = ""
    excluded_df = excluded_df[ex_cols]

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("수집결과.csv", valid_df.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("제외된글.csv", excluded_df.to_csv(index=False, encoding="utf-8-sig"))
        md_table = simple_df_to_markdown(valid_df[["채널", "검색식", "제목", "작성자", "작성일", "URL"]])
        zf.writestr("수집결과.md", "# 육아 소셜 리스닝 수집 결과 (" + time_tag + ")\n\n" + md_table)

        for i, row in valid_df.iterrows():
            safe_title = re.sub(r'[\/:*?"<>|]', '_', str(row["제목"]))[:30].strip()
            fname = f"notes/{row['채널']}_{i+1}_{safe_title}.md"
            note_content = (
                "---\n"
                f"title: {row['제목']}\n"
                f"channel: {row['채널']}\n"
                f"author: {row['작성자']}\n"
                f"date: {row['작성일']}\n"
                f"url: {row['URL']}\n"
                f"keyword: {row['검색식']}\n"
                f"collected_at: {row['수집일시']}\n"
                "---\n\n"
                f"# {row['제목']}\n\n"
                "### 본문 요약\n"
                f"{row['본문요약']}\n\n"
                "### 본문 전체\n"
                f"{row['본문전체']}\n"
            )
            zf.writestr(fname, note_content)

    zip_bytes = zip_buffer.getvalue()
    safe_worker = re.sub(r'[\/:*?"<>|]', '', worker)[:10].strip() or "팀원"
    zip_filename = f"육아수집_{time_tag}_{safe_worker}.zip"

    # 보관소 폴더에 저장
    archive_path = os.path.join(ARCHIVE_DIR, zip_filename)
    with open(archive_path, "wb") as f:
        f.write(zip_bytes)

    return zip_bytes, zip_filename