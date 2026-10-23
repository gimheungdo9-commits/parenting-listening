import streamlit as st
import pandas as pd
import requests as req_lib
from datetime import datetime
from channel_registry import get_all_channels
from processor import (filter_and_process, create_zip_package, load_history,
                        save_to_master_and_history, load_master_urls,
                        get_archive_file, create_cumulative_zip, read_list_summary)
from fulltext import enrich_full_text, mark_fetch_off, DEFAULT_BODY_AD_WORDS

def translate_to_english(kw_list):
    result = {}
    for kw in kw_list:
        if all(ord(c) < 128 or c.isspace() for c in kw):
            result[kw] = kw
            continue
        try:
            resp = req_lib.get(
                "https://api.mymemory.translated.net/get",
                params={"q": kw, "langpair": "ko|en"},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                translated = data.get("responseData", {}).get("translatedText", "")
                if translated and translated.upper() != kw.upper():
                    result[kw] = translated
        except Exception:
            continue
    return result

st.set_page_config(page_title="육아 소셜 리스닝 수집기", page_icon="👶", layout="wide")

# 1. 비밀번호 인증
team_pwd = st.secrets.get("team_password", "")
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 육아 소셜 리스닝 수집기")
    st.write("팀 전용 웹 프로그램입니다. 비밀번호를 입력해 주세요.")
    input_pwd = st.text_input("비밀번호", type="password")
    if st.button("접속하기"):
        if input_pwd == team_pwd:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# 2. 메인 헤더 & 작업자 정보
st.title("👶 육아 소셜 리스닝 수집기")
col_user, col_info, col_cum = st.columns([1, 2, 1])
with col_user:
    worker_name = st.text_input("👤 작업자 이름", value="팀원1")
with col_info:
    master_count = len(load_master_urls())
    st.info("📊 누적 마스터 보관함: **" + str(master_count) + "건**")
with col_cum:
    if master_count > 0:
        cum_zip, cum_name = create_cumulative_zip()
        if cum_zip:
            st.download_button(
                label="📦 전체 누적 다운로드",
                data=cum_zip,
                file_name=cum_name,
                mime="application/zip"
            )

if "keyword_text" not in st.session_state:
    st.session_state.keyword_text = """이유식 거부
신생아 통잠 안자요
독박육아 힘들어
아기 열 응급실
어린이집 적응 울음"""

# 📂 키워드 번들 파일 업로드
with st.expander("📂 키워드 번들 파일 올리기 (.txt 또는 .csv)"):
    st.caption("미리 만들어 둔 키워드 목록 파일을 올리면 검색창에 자동으로 채워집니다.")
    uploaded_file = st.file_uploader("파일 선택", type=["txt", "csv"], label_visibility="collapsed")
    if uploaded_file is not None:
        try:
            raw = uploaded_file.read().decode("utf-8-sig")
            if uploaded_file.name.endswith(".csv"):
                lines = []
                for line in raw.splitlines():
                    if line.strip():
                        lines.append(line.split(",")[0].strip().strip('"'))
            else:
                lines = [l.strip() for l in raw.splitlines() if l.strip()]
            skip_headers = ["keyword", "keywords", "키워드", "검색어", "검색식"]
            if lines and lines[0].lower() in skip_headers:
                lines = lines[1:]
            if lines:
                st.success("파일에서 **" + str(len(lines)) + "개**의 키워드를 발견했습니다!")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("🔄 검색창에 덮어쓰기"):
                        st.session_state.keyword_text = "\n".join(lines)
                        st.rerun()
                with c2:
                    if st.button("➕ 검색창에 추가하기"):
                        existing = st.session_state.keyword_text.strip()
                        st.session_state.keyword_text = (existing + "\n" + "\n".join(lines)) if existing else "\n".join(lines)
                        st.rerun()
                st.caption("미리보기: " + ", ".join(lines[:10]) + ("..." if len(lines) > 10 else ""))
        except Exception as e:
            st.error("파일 읽기 오류: " + str(e))

# 🧩 키워드 번들 조합기
with st.expander("🧩 키워드 번들 조합기 (AND / OR 자동 생성기)"):
    st.caption("주제어(AND)와 상황/고민어(OR)를 넣으면 고효율 검색식을 자동 조합합니다.")
    b1, b2 = st.columns(2)
    with b1:
        group_a = st.text_input("📌 필수 주제어 (AND)", value="이유식")
    with b2:
        group_b = st.text_input("🔀 상황/고민 키워드 (OR, 쉼표 구분)", value="거부, 안먹어요, 뱉음, 울음, 입꾹닫")
    if st.button("👉 번들 검색식 생성"):
        a_words = [w.strip() for w in group_a.split(",") if w.strip()]
        b_words = [w.strip() for w in group_b.split(",") if w.strip()]
        bundles = [a + " " + b for a in a_words for b in b_words]
        if bundles:
            st.session_state.keyword_text = "\n".join(bundles)
            st.rerun()

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. 검색식 입력")
    st.caption("💡 띄어쓰기 = AND / '단어A | 단어B' = OR 자동 분리")
    keyword_text = st.text_area("검색어 목록", value=st.session_state.keyword_text, height=140)
    st.session_state.keyword_text = keyword_text

    raw_lines = [k.strip() for k in keyword_text.splitlines() if k.strip()]
    keywords = []
    for line in raw_lines:
        if "|" in line:
            keywords.extend([p.strip() for p in line.split("|") if p.strip()])
        else:
            keywords.append(line)

    st.subheader("2. 수집 옵션")
    max_count = st.slider("검색식당 최대 수집 건수", 10, 1000, 100, 10)
    sort_option = st.radio("정렬 기준", ["정확도순", "최신순"], horizontal=True)
    exclude_master_dup = st.checkbox("👥 이전 팀원 수집 중복 제외", value=True)
    auto_translate = st.checkbox("🌐 한글→영어 자동 번역 (Reddit 검색용)", value=True)
    fetch_full = st.checkbox("📄 본문 전체 가져오기 (블로그·지식iN·공개 카페글)", value=True,
                             help="필터를 통과한 글만 본문을 가져옵니다. 글마다 1~2초씩 쉬어서 시간이 걸려요. "
                                  "회원전용 카페 글은 가져오지 않고 '직접 읽을 글' 목록으로 보냅니다.")
    if fetch_full:
        st.caption("⏱️ 예상: 글 100건이면 본문 가져오기에 약 2~3분 추가")

with col2:
    st.subheader("3. 수집 채널 선택")
    all_channels = get_all_channels()
    selected_channels = []
    for ch in all_channels:
        is_avail, status_msg = ch.check_availability(st.secrets)
        if is_avail:
            if st.checkbox("✅ " + ch.name, value=True):
                selected_channels.append(ch)
        elif status_msg == "키 필요":
            st.checkbox("🔑 " + ch.name + " (키 필요)", disabled=True)
        else:
            st.checkbox("⏳ " + ch.name + " (준비 중)", disabled=True)

# 🌐 번역 미리보기 & 캐시
if auto_translate and keywords:
    has_intl = any("Reddit" in c.name for c in selected_channels)
    if has_intl:
        kw_key = "|".join(sorted(keywords))
        if st.session_state.get("cached_kw_key") != kw_key:
            translations = translate_to_english(keywords)
            st.session_state["cached_translations"] = translations
            st.session_state["cached_kw_key"] = kw_key
        else:
            translations = st.session_state.get("cached_translations", {})

        with st.expander("🌐 자동 번역 미리보기 (해외 채널용)"):
            if translations:
                for ko, en in translations.items():
                    if ko != en:
                        st.write("🇰🇷 **" + ko + "** → 🇺🇸 **" + en + "**")
                    else:
                        st.write("🇺🇸 **" + en + "** (이미 영어)")
            else:
                st.warning("번역 결과가 없습니다. 한글 키워드 그대로 검색합니다.")

# 4. 필터 설정
with st.expander("⚙️ 필터 세부 설정"):
    f1, f2 = st.columns(2)
    with f1:
        ad_words_text = st.text_area("🚫 광고 금지어",
            value="협찬, 제공받아, 제공받은, 광고, 체험단, 체험팩, 체험판, 서포터즈, 쿠폰, 최저가, 구매링크, 공동구매, 공구, 할인코드, 쿠팡파트너스, 쿠팡 파트너스, 수수료를 제공, 원고료, 원고, 소정의, 무상으로, 지원받아", height=90)
        info_words_text = st.text_area("📑 정보글 제외어 (제목)",
            value="총정리, 완벽 가이드, 완벽정리, 가이드, 정리해 드립니다, 알아보기, 알아봅시다, 시기별, 단계별, 방법 총, 꿀팁 모음, 나무위키, 위키", height=90)
        pet_words_text = st.text_area("🐱 반려동물 제외어 (제목+요약+작성자)",
            value="고양이, 강아지, 앵무, 코뉴어, 새끼고양이, 집사, 펠렛, 사료, 이유조, 반려, 펫, 냥이, 댕댕", height=90)
        st.caption("⚠️ '냥' 한 글자는 '그냥'까지 걸러서 '냥이'로 바꿔 두었어요.")
    with f2:
        req_words_text = st.text_area("🍼 육아 필수어",
            value="아기, 아이, 애기, 육아, 엄마, 아빠, 신생아, 개월, 돌, 이유식, 수유, 분유, 모유, 어린이집, 유치원, 통잠, 기저귀, 남편, 와이프, 접종, 소아과, 어린이, 영유아, 자녀, 첫째, 둘째, 딸, 아들, 애들, 우리애", height=90)
        body_ad_words_text = st.text_area("📄 본문 광고 표시어 (본문 전체에서 검사)",
            value=", ".join(DEFAULT_BODY_AD_WORDS), height=90,
            help="본문 맨 아래 '제품을 제공받아…' 같은 협찬 표시를 잡습니다. "
                 "'광고', '쿠폰'처럼 짧은 단어는 진짜 부모 글까지 걸러서 일부러 뺐어요.")
        min_length = st.number_input("📏 최소 글자 수", 10, 200, 30)
        st.caption("🏢 작성자에 '공식'이 포함된 기업 블로그는 자동 제외됩니다.")
        st.caption("📢 '이너프렌즈3기' 같은 체험단 N기 패턴도 자동 감지됩니다.")

filter_config = {
    "ad_words": [w.strip() for w in ad_words_text.split(",")],
    "info_words": [w.strip() for w in info_words_text.split(",")],
    "req_words": [w.strip() for w in req_words_text.split(",")],
    "pet_words": [w.strip() for w in pet_words_text.split(",")],
    "min_length": min_length
}

# 5. 수집 실행
st.divider()
if st.button("🚀 수집 시작", type="primary", use_container_width=True):
    if not keywords:
        st.warning("검색식을 최소 하나 이상 입력해 주세요.")
        st.stop()
    if not selected_channels:
        st.warning("수집할 채널을 최소 하나 이상 선택해 주세요.")
        st.stop()

    # 번역 준비
    translations = {}
    has_intl = any("Reddit" in c.name for c in selected_channels)
    if auto_translate and has_intl:
        translations = st.session_state.get("cached_translations", {})
        if not translations:
            translations = translate_to_english(keywords)

    all_raw_items = []
    progress_bar = st.progress(0.0)
    status_text = st.empty()
    total_tasks = len(selected_channels) * len(keywords)
    current_task = 0

    for ch in selected_channels:
        is_intl = "Reddit" in ch.name  # 유튜브는 한국 부모 댓글을 위해 한국어 그대로 검색
        for kw in keywords:
            current_task += 1
            search_kw = kw
            display_kw = kw

            if is_intl and auto_translate and kw in translations and kw != translations[kw]:
                search_kw = translations[kw]
                display_kw = kw + " → " + search_kw

            status_text.write("🔄 **[" + ch.name + "]** · '" + display_kw + "' 수집 중...")

            items = ch.search(
                search_kw, max_count, sort_option, st.secrets,
                status_callback=lambda msg: status_text.info(msg)
            )

            for item in items:
                item["검색식"] = kw

            all_raw_items.extend(items)
            progress_bar.progress(current_task / total_tasks)

    status_text.write("🧹 글 정제 및 중복 검사 중...")
    valid_items, excluded_items, stats = filter_and_process(all_raw_items, filter_config, exclude_master_dup)

    # 📄 파트 B: 본문 전체 가져오기 (필터 통과한 글만)
    stats["body_ad"] = 0
    ft_stats = None
    if fetch_full and valid_items:
        progress_bar.progress(0.0)

        def _ft_progress(done, total, label):
            progress_bar.progress(done / total if total else 1.0)
            status_text.write("📄 본문 가져오는 중 **" + str(done) + " / " + str(total) + "** · " + label)

        body_ad_words = [w.strip() for w in body_ad_words_text.split(",")]
        valid_items, body_excluded, ft_stats = enrich_full_text(valid_items, body_ad_words, _ft_progress)
        excluded_items.extend(body_excluded)
        stats["body_ad"] = ft_stats["body_ad"]
        stats["saved"] = len(valid_items)
    else:
        mark_fetch_off(valid_items)

    read_count, read_cafes = read_list_summary(valid_items)

    time_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes, zip_filename = create_zip_package(valid_items, excluded_items, time_tag, worker=worker_name)

    dup_total = stats["dup"] + stats["master_dup"]
    history_record = {
        "수집일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "작업자": worker_name,
        "채널": ", ".join([c.name for c in selected_channels]),
        "검색식": " / ".join(keywords[:2]) + (" 외 " + str(len(keywords)-2) + "개" if len(keywords) > 2 else ""),
        "요청건수": max_count,
        "총수집": stats["total"],
        "광고제외": stats["ad"],
        "정보글제외": stats["info"],
        "반려동물제외": stats["pet"],
        "기업블로그제외": stats["corporate"],
        "무관제외": stats["irrelevant"],
        "중복제외": dup_total,
        "본문광고제외": stats["body_ad"],
        "최종저장": stats["saved"],
        "직접읽을글": read_count,
        "파일명": zip_filename
    }
    save_to_master_and_history(valid_items, history_record)
    progress_bar.progress(1.0)

    # 제외 사유별 상세 요약
    status_text.success("✅ 수집 완료! 최종 **" + str(stats["saved"]) + "건** 저장")
    summary_parts = []
    if stats["master_dup"]: summary_parts.append("팀 누적 중복 " + str(stats["master_dup"]))
    if stats["dup"]: summary_parts.append("이번 중복 " + str(stats["dup"]))
    if stats["ad"]: summary_parts.append("광고 " + str(stats["ad"]))
    if stats["info"]: summary_parts.append("정보글 " + str(stats["info"]))
    if stats["pet"]: summary_parts.append("반려동물 " + str(stats["pet"]))
    if stats["corporate"]: summary_parts.append("기업블로그 " + str(stats["corporate"]))
    if stats["irrelevant"]: summary_parts.append("무관 " + str(stats["irrelevant"]))
    if stats["short"]: summary_parts.append("글자수미달 " + str(stats["short"]))
    if stats["body_ad"]: summary_parts.append("본문 광고표시 " + str(stats["body_ad"]))
    if summary_parts:
        st.info("📊 제외 상세: " + " / ".join(summary_parts) + "  (총 수집 " + str(stats["total"]) + "건)")
    if ft_stats:
        st.info("📄 본문: 전체 " + str(ft_stats["full"]) + "건 / 회원전용(요약만) " + str(ft_stats["members"])
                + "건 / 가져오기 실패 " + str(ft_stats["failed"]) + "건")
    if read_count:
        st.warning("📌 직접 읽을 글 **" + str(read_count) + "건** (카페 " + str(read_cafes) + "곳) — "
                   "다운로드 파일 안 `직접읽을글.csv`에 있어요. 카페에 정식 가입한 뒤 읽고 메모해 주세요.")

    st.session_state.valid_items = valid_items
    st.session_state.excluded_items = excluded_items
    st.session_state.last_zip_bytes = zip_bytes
    st.session_state.last_zip_name = zip_filename

# 6. 결과 미리보기 및 다운로드
if "valid_items" in st.session_state and st.session_state.valid_items:
    st.subheader("📋 수집 결과 미리보기 (" + str(len(st.session_state.valid_items)) + "건)")
    preview_cols = ["채널", "검색식", "제목", "본문상태", "작성자", "작성일", "URL", "홍보의심"]
    df_preview = pd.DataFrame(st.session_state.valid_items)
    for c in preview_cols:
        if c not in df_preview.columns:
            df_preview[c] = ""
    st.dataframe(df_preview[preview_cols], use_container_width=True)

    st.download_button(
        label="💾 방금 수집 결과 다운로드 (" + st.session_state.last_zip_name + ")",
        data=st.session_state.last_zip_bytes,
        file_name=st.session_state.last_zip_name,
        mime="application/zip",
        type="primary"
    )

# 7. 회차별 이력 및 보관함
st.divider()
st.subheader("📦 팀 누적 수집 이력 & 회차별 다운로드")
hist_df = load_history()
if not hist_df.empty:
    for idx, row in hist_df.iterrows():
        with st.container():
            ct, cw, ck, cc, cb = st.columns([2, 1, 3, 1, 2])
            ct.write("🕒 **" + str(row.get("수집일시", "")) + "**")
            cw.write("👤 " + str(row.get("작업자", "")))
            ck.write("🔍 " + str(row.get("검색식", "")))
            cc.write("📊 **" + str(row.get("최종저장", 0)) + "건**")
            fname = row.get("파일명", "")
            file_data = get_archive_file(fname) if fname else None
            if file_data:
                cb.download_button(
                    label="💾 " + str(row.get("최종저장", 0)) + "건 받기",
                    data=file_data, file_name=fname,
                    mime="application/zip",
                    key="archive_" + str(idx) + "_" + str(fname)
                )
            else:
                cb.caption("파일 없음")
            st.divider()
else:
    st.info("아직 수집 이력이 없습니다. 수집을 시작하면 회차별로 기록됩니다.")
