"""
본문 전체 가져오기 (파트 B) + 직접 읽을 글 판정 (파트 C)

원칙
- 로그인 없이 누구나 볼 수 있는 공개 페이지만 읽습니다.
- 로그인, 쿠키·세션 재사용, 회원전용 글 우회는 하지 않습니다.
  카페 글이 회원전용이면 본문을 가져오지 않고 "회원전용-요약만"으로 표시만 합니다.
- 글 하나를 읽을 때마다 1~2초 쉽니다.
"""

import json
import random
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 본문상태 값 (결과 파일의 "본문상태" 칸에 들어가는 글자)
# ---------------------------------------------------------------------------
STATUS_FULL = "전체"
STATUS_MEMBERS = "회원전용-요약만"
STATUS_FAILED = "가져오기 실패"
STATUS_OFF = "요약만 (본문 가져오기 꺼짐)"

# 본문 안에서 찾을 "광고 표시" 기본 단어
# (본문은 길어서 '광고', '쿠폰' 같은 짧은 단어를 넣으면 진짜 부모 글까지 걸러집니다.
#  그래서 협찬·제공 사실을 밝히는 표현 위주로만 넣었습니다.)
DEFAULT_BODY_AD_WORDS = [
    "협찬", "제공받아", "제공받은", "제공 받아", "제공 받은", "원고료", "소정의",
    "무상으로 제공", "무상 제공", "무상으로 지원", "지원받아", "지원 받아",
    "체험단", "서포터즈", "쿠팡파트너스", "쿠팡 파트너스", "파트너스 활동",
    "수수료를 제공", "업체로부터",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
TIMEOUT = 12
MAX_ANSWERS = 5          # 지식iN 답변은 최대 몇 개까지 가져올지
MAX_BODY_CHARS = 20000   # 글 하나당 최대 글자 수 (엑셀 칸 한도 대비)

# 본문에서 빼낼 요소 (이미지, 동영상, 스티커, 지도, 링크 미리보기 등)
REMOVE_SELECTORS = [
    "script", "style", "noscript", "iframe", "img", "video", "svg", "button",
    ".se-image", ".se-imageStrip", ".se-imageGroup", ".se-video", ".se-sticker",
    ".se-map", ".se-placesMap", ".se-oglink", ".se-module-oglink", ".se-file",
    ".se-material", ".se-audio", ".se-oembed", ".se-wrappingParagraph-image",
    ".se_mediaArea", ".se_og_box", ".se_sticker", ".se_map",
    ".__se_link_preview", ".u_likeit_list_module", ".post_tag", ".wrap_tag",
]

ZERO_WIDTH = re.compile(r"[​‌‍﻿ ]")
DATE_RE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    text = ZERO_WIDTH.sub(" ", text or "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()[:MAX_BODY_CHARS]


BLOCK_TAGS = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "tr", "pre", "section", "article", "ul", "ol", "table"]


def _text_of(node) -> str:
    """
    HTML 덩어리에서 이미지·버튼 등을 빼고 글자만 뽑습니다.
    굵은 글씨·색깔 글씨(span, b 등)는 문장 중간에 있으므로 줄을 바꾸지 않고,
    문단(p, div 등)이 끝날 때와 <br>에서만 줄을 바꿉니다.
    """
    if node is None:
        return ""
    for sel in REMOVE_SELECTORS:
        for bad in node.select(sel):
            bad.decompose()
    # HTML 원본의 줄맞춤용 공백(태그 사이 빈 줄)은 글 내용이 아니므로 지웁니다.
    for s in node.find_all(string=True):
        if "\n" in s and not s.strip():
            s.extract()
    for br in node.find_all("br"):
        br.replace_with("\n")
    for block in node.find_all(BLOCK_TAGS):
        block.append("\n")
    return _clean_text(node.get_text(""))


def _first(soup, selectors: List[str]):
    for sel in selectors:
        found = soup.select_one(sel)
        if found is not None and found.get_text(strip=True):
            return found
    return None


def _find_date(text: str) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    y, mo, d = m.groups()
    try:
        return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _get(session: requests.Session, url: str, **kw) -> requests.Response:
    return session.get(url, headers={**HEADERS, **kw.pop("headers", {})}, timeout=TIMEOUT, **kw)


# ---------------------------------------------------------------------------
# 네이버 블로그
# ---------------------------------------------------------------------------
def _blog_ids(url: str) -> Optional[Tuple[str, str]]:
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    if "blogId" in q and "logNo" in q:
        return q["blogId"][0], q["logNo"][0]
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) >= 2 and segs[-1].isdigit():
        return segs[-2], segs[-1]
    return None


def parse_blog_html(html: str) -> Tuple[str, str]:
    """블로그 페이지 HTML → (본문, 작성일)."""
    soup = BeautifulSoup(html, "html.parser")
    date_node = _first(soup, [".blog_date", "p.blog_date", "span.se_publishDate",
                              ".se_publishDate", ".date", "p.date"])
    date = _find_date(date_node.get_text(" ")) if date_node else ""
    body_node = _first(soup, [
        "div.se-main-container",      # 스마트에디터 ONE (요즘 글)
        "div#viewTypeSelector",       # 옛날 모바일 글
        "div.post_ct",
        "div#postViewArea",           # 옛날 PC 글
        "div.se_component_wrap",
    ])
    return _text_of(body_node), date


def fetch_blog(session: requests.Session, url: str) -> Dict[str, str]:
    ids = _blog_ids(url)
    if not ids:
        return {"status": STATUS_FAILED, "body": "", "date": "", "note": "블로그 주소 인식 불가"}
    blog_id, log_no = ids
    # 블로그 PC 화면은 안쪽에 또 다른 화면(iframe)이 있어서, 모바일 화면으로 바로 읽습니다.
    mobile_url = f"https://m.blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"
    resp = _get(session, mobile_url)
    if resp.status_code != 200:
        return {"status": STATUS_FAILED, "body": "", "date": "", "note": f"HTTP {resp.status_code}"}
    body, date = parse_blog_html(resp.text)
    if not body:
        # 비공개·삭제된 글이거나 화면 구조가 다른 경우
        return {"status": STATUS_FAILED, "body": "", "date": date, "note": "본문을 찾지 못함 (비공개·삭제 글일 수 있음)"}
    return {"status": STATUS_FULL, "body": body, "date": date, "note": ""}


# ---------------------------------------------------------------------------
# 네이버 지식iN
# ---------------------------------------------------------------------------
def parse_kin_html(html: str) -> Tuple[str, str]:
    """지식iN 페이지 HTML → ("[질문] ... [답변1] ...", 질문 작성일)."""
    soup = BeautifulSoup(html, "html.parser")

    q_title = _first(soup, [".endTitleSection", ".c-heading__title", "div.title"])
    q_body = _first(soup, [
        ".questionDetail", ".c-heading__content", "._questionContentsArea",
        ".question-content",
    ])
    q_info = _first(soup, [".c-heading .c-userinfo", ".c-userinfo__info", ".c-heading__info"])
    date = _find_date(q_info.get_text(" ")) if q_info else ""

    answers = []
    answer_items = soup.select(".answer-content__item, ._answer, .answer-content__list > div")
    for item in answer_items:
        node = _first(item, [".se-main-container", "._endContentsText", ".answerDetail", ".c-heading-answer__content"])
        txt = _text_of(node)
        if txt and txt not in answers:
            answers.append(txt)
        if len(answers) >= MAX_ANSWERS:
            break
    if not answers:  # 구조가 다를 때 대비
        for node in soup.select("._endContentsText, .answerDetail"):
            txt = _text_of(node)
            if txt and txt not in answers:
                answers.append(txt)
            if len(answers) >= MAX_ANSWERS:
                break

    title_txt = _text_of(q_title)
    body_txt = _text_of(q_body)
    if not (title_txt or body_txt):
        return "", date

    parts = ["[질문]", (title_txt + "\n" + body_txt).strip()]
    for i, ans in enumerate(answers, 1):
        parts += ["", f"[답변{i}]", ans]
    return _clean_text("\n".join(parts)), date


def fetch_kin(session: requests.Session, url: str) -> Dict[str, str]:
    resp = _get(session, url)
    if resp.status_code != 200:
        return {"status": STATUS_FAILED, "body": "", "date": "", "note": f"HTTP {resp.status_code}"}
    body, date = parse_kin_html(resp.text)
    if not body:
        return {"status": STATUS_FAILED, "body": "", "date": date, "note": "질문 본문을 찾지 못함"}
    return {"status": STATUS_FULL, "body": body, "date": date, "note": ""}


# ---------------------------------------------------------------------------
# 네이버 카페 (로그인 없이 열리는 '전체공개' 글만)
# ---------------------------------------------------------------------------
_CLUB_ID_CACHE: Dict[str, Optional[str]] = {}
CLUB_ID_PATTERNS = [
    r'g_sClubId\s*=\s*["\']?(\d+)',
    r'clubid=(\d+)',
    r'"cafeId"\s*:\s*(\d+)',
    r'clubId["\']?\s*[:=]\s*["\']?(\d+)',
]


def _cafe_ids(url: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """카페 글 주소 → (카페주소이름, 글번호, 카페숫자번호 또는 None)."""
    parts = urlsplit(url)
    q = {k.lower(): v for k, v in parse_qs(parts.query).items()}
    if "articleid" in q:
        club = q.get("clubid", [None])[0]
        return "", q["articleid"][0], club
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) >= 2 and segs[-1].isdigit():
        return segs[-2], segs[-1], None
    return None


def _club_id(session: requests.Session, cafe_name: str) -> Optional[str]:
    if cafe_name in _CLUB_ID_CACHE:
        return _CLUB_ID_CACHE[cafe_name]
    club = None
    try:
        resp = _get(session, f"https://cafe.naver.com/{cafe_name}")
        if resp.status_code == 200:
            for pat in CLUB_ID_PATTERNS:
                m = re.search(pat, resp.text)
                if m:
                    club = m.group(1)
                    break
    except requests.RequestException:
        club = None
    _CLUB_ID_CACHE[cafe_name] = club
    return club


def parse_cafe_json(data: Dict[str, Any]) -> Tuple[str, str]:
    """카페 글 정보(JSON) → (본문, 작성일). 본문이 없으면 빈 글자."""
    article = (data.get("result") or {}).get("article") or {}
    content_html = article.get("contentHtml") or ""
    body = _text_of(BeautifulSoup(content_html, "html.parser")) if content_html else ""
    date = ""
    wd = article.get("writeDate")
    if isinstance(wd, (int, float)) and wd > 0:
        date = datetime.fromtimestamp(wd / 1000).strftime("%Y-%m-%d")
    return body, date


def fetch_cafe(session: requests.Session, url: str) -> Dict[str, str]:
    ids = _cafe_ids(url)
    if not ids:
        return {"status": STATUS_MEMBERS, "body": "", "date": "", "note": "카페 주소 인식 불가 - 직접 확인 필요"}
    cafe_name, article_id, club = ids
    if not club and cafe_name:
        club = _club_id(session, cafe_name)
    if not club:
        return {"status": STATUS_MEMBERS, "body": "", "date": "", "note": "카페 번호 확인 불가 - 직접 확인 필요"}

    # 로그인하지 않은 방문자가 공개 글을 열 때 화면이 불러오는 주소와 같습니다.
    # 쿠키·로그인 정보는 보내지 않습니다. 회원전용 글이면 여기서 막히고, 그대로 '회원전용'으로 표시합니다.
    api = (f"https://apis.naver.com/cafe-web/cafe-articleapi/v2.1/cafes/{club}"
           f"/articles/{article_id}?useCafeId=true&requestFrom=A")
    resp = _get(session, api, headers={"Referer": f"https://cafe.naver.com/{cafe_name or ''}"})
    if resp.status_code in (401, 403):
        return {"status": STATUS_MEMBERS, "body": "", "date": "", "note": "회원전용 글"}
    if resp.status_code != 200:
        return {"status": STATUS_MEMBERS, "body": "", "date": "", "note": f"열람 불가 (HTTP {resp.status_code})"}
    try:
        data = resp.json()
    except ValueError:
        return {"status": STATUS_MEMBERS, "body": "", "date": "", "note": "열람 불가 (응답 형식 다름)"}
    body, date = parse_cafe_json(data)
    if not body:
        return {"status": STATUS_MEMBERS, "body": "", "date": date, "note": "회원전용 또는 본문 없음"}
    return {"status": STATUS_FULL, "body": body, "date": date, "note": ""}


# ---------------------------------------------------------------------------
# 채널 → 가져오는 방법 연결
# ---------------------------------------------------------------------------
def fetch_one(session: requests.Session, item: Dict[str, Any]) -> Dict[str, str]:
    channel = item.get("채널", "")
    url = item.get("URL", "")
    try:
        if "블로그" in channel:
            return fetch_blog(session, url)
        if "지식iN" in channel:
            return fetch_kin(session, url)
        if "카페" in channel:
            return fetch_cafe(session, url)
    except requests.RequestException as e:
        return {"status": STATUS_FAILED, "body": "", "date": "", "note": f"접속 오류: {type(e).__name__}"}
    except Exception as e:  # 예상 못 한 화면 구조 등 — 프로그램은 멈추지 않습니다
        return {"status": STATUS_FAILED, "body": "", "date": "", "note": f"처리 오류: {type(e).__name__}"}
    return {"status": "", "body": "", "date": "", "note": ""}


def needs_fetch(item: Dict[str, Any]) -> bool:
    ch = item.get("채널", "")
    return any(k in ch for k in ("블로그", "지식iN", "카페"))


def find_body_ads(body: str, body_ad_words: List[str]) -> List[str]:
    return [w for w in body_ad_words if w and w in body]


def enrich_full_text(
    items: List[Dict[str, Any]],
    body_ad_words: List[str],
    progress: Optional[Callable[[int, int, str], None]] = None,
    delay_range: Tuple[float, float] = (1.0, 2.0),
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """
    필터를 통과한 글들의 본문을 채웁니다.
    반환: (남길 글, 본문 광고로 새로 제외된 글, 통계)
    """
    body_ad_words = [w.strip() for w in body_ad_words if w and w.strip()]
    session = requests.Session()
    kept, newly_excluded = [], []
    st = {"full": 0, "members": 0, "failed": 0, "body_ad": 0}
    targets = [it for it in items if needs_fetch(it)]
    total, done = len(targets), 0

    for item in items:
        ch = item.get("채널", "")

        # 유튜브 댓글·Reddit 등은 이미 전체 글이 들어 있습니다.
        if not needs_fetch(item):
            if not item.get("본문전체"):
                item["본문전체"] = item.get("본문요약", "")
            item["본문상태"] = STATUS_FULL
            st["full"] += 1
            kept.append(item)
            continue

        done += 1
        if progress:
            progress(done, total, f"{ch} · {item.get('제목', '')[:25]}")

        res = fetch_one(session, item)
        item["본문상태"] = res["status"] or STATUS_FAILED
        item["본문메모"] = res.get("note", "")
        if res["date"] and not item.get("작성일"):
            item["작성일"] = res["date"]

        if res["status"] == STATUS_FULL:
            ads = find_body_ads(res["body"], body_ad_words)
            if ads:
                st["body_ad"] += 1
                item["본문전체"] = res["body"]
                newly_excluded.append({**item, "제외사유": "본문에서 광고 표시 발견 (" + ", ".join(ads[:3]) + ")"})
            else:
                st["full"] += 1
                item["본문전체"] = res["body"]
                kept.append(item)
        elif res["status"] == STATUS_MEMBERS:
            st["members"] += 1
            kept.append(item)
        else:
            st["failed"] += 1
            kept.append(item)

        if done < total:
            time.sleep(random.uniform(*delay_range))

    return kept, newly_excluded, st


def mark_fetch_off(items: List[Dict[str, Any]]) -> None:
    """본문 가져오기를 끈 경우: 유튜브·Reddit은 '전체', 나머지는 '요약만'으로 표시."""
    for item in items:
        if needs_fetch(item):
            item["본문상태"] = STATUS_OFF
        else:
            if not item.get("본문전체"):
                item["본문전체"] = item.get("본문요약", "")
            item["본문상태"] = STATUS_FULL


def to_json_str(value: Any) -> str:
    """옵시디언 머리말(YAML)에 안전하게 넣기 위해 따옴표로 감쌉니다."""
    if value is None or (isinstance(value, float) and value != value):
        value = ""
    return json.dumps(str(value), ensure_ascii=False)
