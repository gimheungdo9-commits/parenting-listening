import os
import io
import re
import zipfile
import pandas as pd
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from typing import List, Dict, Any, Tuple
from fulltext import STATUS_MEMBERS, to_json_str
from storage import get_store

# 결과 파일 칸 구성 (한 곳에서 관리)
RESULT_COLS = ["채널", "검색식", "제목", "본문요약", "본문전체", "본문상태", "작성자", "작성일", "URL", "수집일시", "홍보의심"]
MD_COLS = ["채널", "검색식", "제목", "본문요약", "본문상태", "작성자", "작성일", "URL", "홍보의심"]
READ_LIST_COLS = ["카페명", "제목", "요약", "URL", "검색식", "읽음여부", "메모"]

# 주소 뒤에 붙는 추적용 값 (같은 글인데 주소만 달라지게 만드는 것들)
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "from", "trackingcode", "referrerallarticles",
    "fromrss", "proxyreferer", "isinf", "answerno",
}


def normalize_url(url):
    """
    같은 글을 같은 주소로 맞춥니다.
    주의: 지식iN(docId)·유튜브 댓글(v, lc)·카페(articleid)는 '?' 뒤에 글 번호가 있어서
    '?' 뒤를 통째로 지우면 서로 다른 글이 전부 같은 글로 취급됩니다. 추적값만 지웁니다.
    """
    if not url or not isinstance(url, str):
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    for mobile, pc in (("m.blog.naver.com", "blog.naver.com"),
                       ("m.cafe.naver.com", "cafe.naver.com"),
                       ("m.kin.naver.com", "kin.naver.com"),
                       ("m.youtube.com", "www.youtube.com")):
        if host == mobile:
            host = pc
    q = parse_qs(parts.query)

    # 블로그: PostView.naver?blogId=..&logNo=.. → /아이디/글번호
    if host == "blog.naver.com" and "blogId" in q and "logNo" in q:
        return "https://blog.naver.com/" + q["blogId"][0] + "/" + q["logNo"][0]
    segs = [s for s in parts.path.split("/") if s]
    if host in ("blog.naver.com", "cafe.naver.com") and len(segs) == 2 and segs[1].isdigit():
        return "https://" + host + "/" + segs[0] + "/" + segs[1]   # 블로그·카페 글 주소는 '?' 뒤가 필요 없음
    # 지식iN: 질문 번호(docId)가 같으면 같은 글
    if host == "kin.naver.com" and "docId" in q:
        return "https://kin.naver.com/qna/detail.naver?docId=" + q["docId"][0]

    keep = sorted((k, v) for k, vs in q.items() if k.lower() not in TRACKING_PARAMS for v in vs)
    return urlunsplit(("https", host, parts.path.rstrip("/"), urlencode(keep), ""))

def load_master_urls():
    df = get_store().load_master()
    if df.empty or "URL" not in df.columns:
        return set()
    return set(u for u in df["URL"].map(normalize_url) if u)


def load_master_df():
    return get_store().load_master()


def load_history():
    return get_store().load_history()


def save_to_master_and_history(valid_items, history_record, excluded_items=None, run_id=""):
    """
    이번 수집 결과를 누적 장부에 더합니다.
    - 장부에 이미 있는 글(같은 주소)은 다시 넣지 않습니다.
    - 회원전용 카페 글은 '직접읽을글' 목록에도 넣습니다 (이미 있으면 건너뜀).
    """
    store = get_store()
    run_id = run_id or history_record.get("파일명", "")

    if valid_items:
        new_df = pd.DataFrame(valid_items)
        new_df["회차ID"] = run_id
        new_df["_norm"] = new_df["URL"].map(normalize_url)
        existing = load_master_urls()
        new_df = new_df[~new_df["_norm"].isin(existing)].drop_duplicates("_norm").drop(columns="_norm")
        store.append_master(new_df)

        read_df = build_read_list(_prepare_df(valid_items, RESULT_COLS))
        if not read_df.empty:
            read_df["회차ID"] = run_id
            old_read = store.load_read_list()
            old_urls = set(old_read["URL"].map(normalize_url)) if not old_read.empty else set()
            read_df = read_df[~read_df["URL"].map(normalize_url).isin(old_urls)]
            store.append_read_list(read_df)

    if excluded_items:
        ex_df = pd.DataFrame(excluded_items)
        ex_df["회차ID"] = run_id
        store.append_excluded(ex_df)

    store.append_history(pd.DataFrame([history_record]))
    store.clear_cache()


def get_archive_file(filename):
    """회차별 결과 zip. 이 컴퓨터에 저장된 파일이 있으면 그걸, 없으면 장부에서 다시 만듭니다."""
    if not filename:
        return None
    store = get_store()
    data = store.get_archive(filename)
    if data:
        return data
    master = store.load_master()
    if master.empty or "회차ID" not in master.columns:
        return None
    run_rows = master[master["회차ID"] == filename]
    if run_rows.empty:
        return None
    excluded = store.load_excluded()
    if not excluded.empty and "회차ID" in excluded.columns:
        excluded = excluded[excluded["회차ID"] == filename]
    tag = filename.replace("육아수집_", "").replace(".zip", "")
    data, _ = _build_zip(run_rows, excluded, tag)
    return data


def simple_df_to_markdown(df):
    headers = list(df.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |"
    ]
    for _, row in df.iterrows():
        cells = [str(v).replace("\n", " ").replace("|", "-")[:100] for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)

def filter_and_process(items, filters, exclude_master_dup=True):
    ad_words = [w.strip() for w in filters.get("ad_words", []) if w.strip()]
    info_words = [w.strip() for w in filters.get("info_words", []) if w.strip()]
    req_words = [w.strip() for w in filters.get("req_words", []) if w.strip()]
    pet_words = [w.strip() for w in filters.get("pet_words", []) if w.strip()]
    min_length = filters.get("min_length", 30)

    master_urls = load_master_urls() if exclude_master_dup else set()
    valid_dict = {}
    seen_content = set()
    excluded_items = []
    stats = {
        "total": len(items), "ad": 0, "info": 0, "irrelevant": 0,
        "short": 0, "dup": 0, "master_dup": 0, "pet": 0, "corporate": 0, "saved": 0
    }

    for item in items:
        title = item.get("제목", "")
        snippet = item.get("본문요약", "")
        author = item.get("작성자", "")
        full_text = title + " " + snippet
        url = item.get("URL", "")
        norm_url = normalize_url(url)

        # 0. 이전 팀원 수집 누적 중복
        if exclude_master_dup and norm_url in master_urls:
            stats["master_dup"] += 1
            excluded_items.append({**item, "제외사유": "이전 팀원 수집 누적 중복"})
            continue

        # 1. 반려동물 글 제외 (제목+요약+작성자 검사)
        pet_check = title + " " + snippet + " " + author
        found_pet = [w for w in pet_words if w in pet_check]
        if found_pet:
            stats["pet"] += 1
            excluded_items.append({**item, "제외사유": "반려동물 글 (" + ", ".join(found_pet) + ")"})
            continue

        # 2. 기업 블로그 제외 (작성자에 "공식" 포함)
        if "공식" in author:
            stats["corporate"] += 1
            excluded_items.append({**item, "제외사유": "기업 블로그 (작성자: " + author + ")"})
            continue

        # 3. 광고 금지어
        found_ad = [w for w in ad_words if w in full_text]
        if found_ad:
            stats["ad"] += 1
            excluded_items.append({**item, "제외사유": "광고 의심 (" + ", ".join(found_ad) + ")"})
            continue

        # 3-2. 체험단 N기 패턴 (예: 이너프렌즈3기, 맘서포터즈2기)
        campaign_match = re.search(r'[가-힣]+\d+기', full_text)
        if campaign_match:
            stats["ad"] += 1
            excluded_items.append({**item, "제외사유": "광고 의심 - 체험단 활동 (" + campaign_match.group() + ")"})
            continue

        # 4. 정보글 제외어 (제목 기준)
        found_info = [w for w in info_words if w in title]
        if found_info:
            stats["info"] += 1
            excluded_items.append({**item, "제외사유": "정보성 글 (" + ", ".join(found_info) + ")"})
            continue

        # 5. 육아 필수어 (Reddit은 영문 포함)
        is_reddit = "Reddit" in item.get("채널", "")
        check_words = list(req_words)
        if is_reddit:
            check_words = check_words + ["baby", "toddler", "infant", "kid", "child", "mom", "dad", "sleep", "food", "feed", "cry"]

        if check_words:
            found_req = [w for w in check_words if w.lower() in full_text.lower()]
            if not found_req:
                stats["irrelevant"] += 1
                excluded_items.append({**item, "제외사유": "육아 필수어 미포함"})
                continue

        # 6. 최소 글자 수
        if len(snippet.strip()) < min_length:
            stats["short"] += 1
            excluded_items.append({**item, "제외사유": "최소 글자 수 미달 (" + str(len(snippet.strip())) + "자)"})
            continue

        # 7. 해시태그 과다 표시 (제외하지 않고 표시만)
        hashtag_count = snippet.count("#")
        promo_flag = ""
        if hashtag_count >= 5:
            promo_flag = "해시태그 과다"
        item["홍보의심"] = promo_flag

        # 8. 이번 수집 내 중복 (같은 주소, 또는 같은 사람이 똑같은 글을 두 번 올린 경우)
        content_key = (author.strip(), snippet.strip())
        if norm_url not in valid_dict and snippet.strip() and content_key in seen_content:
            stats["dup"] += 1
            excluded_items.append({**item, "제외사유": "같은 작성자의 같은 내용 중복"})
            continue
        if snippet.strip():
            seen_content.add(content_key)
        if norm_url in valid_dict:
            stats["dup"] += 1
            existing = valid_dict[norm_url]
            if item["검색식"] not in existing["검색식"]:
                existing["검색식"] = existing["검색식"] + " | " + item["검색식"]
            excluded_items.append({**item, "제외사유": "이번 수집 내 중복 (같은 글: " + str(existing.get("제목", ""))[:20] + ")"})
        else:
            valid_dict[norm_url] = item

    valid_items = list(valid_dict.values())
    stats["saved"] = len(valid_items)
    return valid_items, excluded_items, stats

def _prepare_df(items_or_df, cols):
    df = items_or_df.copy() if isinstance(items_or_df, pd.DataFrame) else pd.DataFrame(items_or_df)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df.fillna("")


def build_read_list(df):
    """파트 C: 회원전용이라 본문을 못 가져온 카페 글 → 직접 읽을 글 목록 (카페명 순)."""
    if df.empty or "본문상태" not in df.columns:
        return pd.DataFrame(columns=READ_LIST_COLS)
    members = df[df["본문상태"].astype(str).str.startswith(STATUS_MEMBERS)]
    if members.empty:
        return pd.DataFrame(columns=READ_LIST_COLS)
    out = pd.DataFrame({
        "카페명": members["작성자"],
        "제목": members["제목"],
        "요약": members["본문요약"],
        "URL": members["URL"],
        "검색식": members["검색식"],
        "읽음여부": "",
        "메모": "",
    })
    return out.sort_values(["카페명", "제목"], kind="stable").reset_index(drop=True)


def read_list_summary(items):
    """화면 표시용: (직접 읽을 글 수, 카페 수)."""
    df = build_read_list(_prepare_df(items, RESULT_COLS))
    return len(df), (df["카페명"].nunique() if not df.empty else 0)


def _note_content(row):
    body = str(row.get("본문전체", "")).strip()
    status = str(row.get("본문상태", "")).strip()
    header = (
        "---\n"
        "title: " + to_json_str(row.get("제목", "")) + "\n"
        "channel: " + to_json_str(row.get("채널", "")) + "\n"
        "author: " + to_json_str(row.get("작성자", "")) + "\n"
        "date: " + to_json_str(row.get("작성일", "")) + "\n"
        "url: " + to_json_str(row.get("URL", "")) + "\n"
        "keyword: " + to_json_str(row.get("검색식", "")) + "\n"
        "body_status: " + to_json_str(status) + "\n"
        "collected_at: " + to_json_str(row.get("수집일시", "")) + "\n"
        "---\n\n"
        "# " + str(row.get("제목", "")) + "\n\n"
    )
    if body:
        return header + "### 본문\n" + body + "\n"
    reason = status or "본문 없음"
    return header + "### 본문 요약 (전체 본문 없음: " + reason + ")\n" + str(row.get("본문요약", "")) + "\n"


def _write_notes(zf, df):
    for i, row in df.iterrows():
        safe_title = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', str(row.get("제목", "")))[:30].strip()
        fname = "notes/" + str(row.get("채널", "")) + "_" + str(i + 1) + "_" + safe_title + ".md"
        zf.writestr(fname, _note_content(row))


def _build_zip(valid_items_or_df, excluded_items_or_df, time_tag):
    zip_buffer = io.BytesIO()
    valid_df = _prepare_df(valid_items_or_df, RESULT_COLS)[RESULT_COLS].reset_index(drop=True)

    ex_cols = ["채널", "검색식", "제목", "작성자", "URL", "제외사유"]
    excluded_df = _prepare_df(excluded_items_or_df, ex_cols)[ex_cols]

    read_df = build_read_list(valid_df)

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("수집결과.csv", valid_df.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("제외된글.csv", excluded_df.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("직접읽을글.csv", read_df.to_csv(index=False, encoding="utf-8-sig"))

        md_table = simple_df_to_markdown(valid_df[MD_COLS])
        zf.writestr("수집결과.md", "# 육아 소셜 리스닝 수집 결과 (" + time_tag + ")\n\n" + md_table)

        _write_notes(zf, valid_df)

    return zip_buffer.getvalue(), read_df


def make_zip_filename(time_tag, worker=""):
    safe_worker = re.sub(r'[\\/:*?"<>|]', '', worker)[:10].strip() or "팀원"
    return "육아수집_" + time_tag + "_" + safe_worker + ".zip"


def create_zip_package(valid_items, excluded_items, time_tag, worker=""):
    zip_bytes, _ = _build_zip(valid_items, excluded_items, time_tag)
    zip_filename = make_zip_filename(time_tag, worker)
    get_store().save_archive(zip_filename, zip_bytes)
    return zip_bytes, zip_filename


def create_cumulative_zip():
    store = get_store()
    master_df = store.load_master()
    if master_df.empty:
        return None, None

    time_tag = datetime.now().strftime("%Y%m%d_%H%M")
    zip_buffer = io.BytesIO()

    master_df = _prepare_df(master_df, RESULT_COLS).reset_index(drop=True)
    # 직접 읽을 글은 팀원들이 적은 읽음여부·메모가 있는 장부 쪽을 우선 사용
    read_df = store.load_read_list()
    if read_df.empty:
        read_df = build_read_list(master_df)

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("전체누적_수집결과.csv", master_df.to_csv(index=False, encoding="utf-8-sig"))
        zf.writestr("전체누적_직접읽을글.csv", read_df.to_csv(index=False, encoding="utf-8-sig"))

        md_table = simple_df_to_markdown(master_df[MD_COLS])
        zf.writestr("전체누적_수집결과.md", "# 전체 누적 수집 결과 (" + time_tag + ")\n\n" + md_table)

        _write_notes(zf, master_df)

    zip_bytes = zip_buffer.getvalue()
    zip_filename = "전체누적결과_" + time_tag + ".zip"
    return zip_bytes, zip_filename
