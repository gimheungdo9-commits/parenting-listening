import streamlit as st
import pandas as pd
from datetime import datetime
from channel_registry import get_all_channels
from processor import filter_and_process, create_zip_package, load_history, save_to_master_and_history, load_master_urls, get_archive_file

try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False

def translate_to_english(kw_list):
    if not TRANSLATOR_AVAILABLE:
        return {}
    result = {}
    try:
        translator = GoogleTranslator(source='ko', target='en')
        for kw in kw_list:
            if all(ord(c) < 128 or c.isspace() for c in kw):
                result[kw] = kw
            else:
                translated = translator.translate(kw)
                if translated:
                    result[kw] = translated
    except Exception:
        pass
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
col_user, col_info = st.columns([1, 2])
with col_user:
    worker_name = st.text_input("👤 작업자 이름 (팀원 닉네임)", value="팀원1")
with col_info:
    master_count = len(load_master_urls())
    st.info(f"📊 현재 우리 팀 누적 마스터 보관함에 **{master_count}건**의 고유 글이 보관되어 있습니다.")

if "keyword_text" not in st.session_state:
    st.session_state.keyword_text = """이유식 거부
신생아 통잠 안자요
독박육아 힘들어
아기 열 응급실
어린이집 적응 울음"""

# 📂 키워드 번들 파일 업로드
with st.expander("📂 키워드 번들 파일 올리기 (.txt 또는 .csv)"):
    st.caption("미리 만들어 둔 키워드 목록 파일을 올리면, 아래 검색창에 자동으로 채워집니다.")
    uploaded_file = st.file_uploader("파일 선택", type=["txt", "csv"], label_visibility="collapsed")
    if uploaded_file is not None:
        try:
            raw = uploaded_file.read().decode("utf-8-sig")
            if uploaded_file.name.endswith(".csv"):
                lines = []
                for line in raw.splitlines():
                    if line.strip():
                        first_col = line.split(",")[0].strip().strip('"')
                        lines.append(first_col)
            else:
                lines = [l.strip() for l in raw.splitlines() if l.strip()]
            skip_headers = ["keyword", "keywords", "키워드", "검색어", "검색식"]
            if lines and lines[0].lower() in skip_headers:
                lines = lines[1:]
            if lines:
                st.success(f"파일에서 **{len(lines)}개**의 키워드를 발견했습니다!")
                col_file1, col_file2 = st.columns(2)
                with col_file1:
                    if st.button("🔄 검색창에 덮어쓰기 (기존 키워드 대체)"):
                        st.session_state.keyword_text = "\n".join(lines)
                        st.rerun()
                with col_file2:
                    if st.button("➕ 검색창에 추가하기 (기존 키워드 유지)"):
                        existing = st.session_state.keyword_text.strip()
                        if existing:
                            st.session_state.keyword_text = existing + "\n" + "\n".join(lines)
                        else:
                            st.session_state.keyword_text = "\n".join(lines)
                        st.rerun()
                st.caption("미리보기: " + ", ".join(lines[:10]) + ("..." if len(lines) > 10 else ""))
        except Exception as e:
            st.error(f"파일을 읽는 중 오류가 발생했습니다: {e}")

# 🧩 키워드 번들 조합기
with st.expander("🧩 키워드 번들 조합기 (AND / OR 자동 생성기)"):
    st.caption("주제어(AND)와 다양한 상황/고민어(OR)를 넣으면 고효율 검색식을 자동으로 조합해 줍니다.")
    b_col1, b_col2 = st.columns(2)
    with b_col1:
        group_a = st.text_input("📌 필수 주제어 (AND)", value="이유식")
    with b_col2:
        group_b = st.text_input("🔀 상황/고민 키워드들 (OR - 쉼표로 구분)", value="거부, 안먹어요, 뱉음, 울음, 입꾹닫")

    if st.button("👉 번들 검색식 생성해서 아래 입력창에 넣기"):
        a_words = [w.strip() for w in group_a.split(",") if w.strip()]
        b_words = [w.strip() for w in group_b.split(",") if w.strip()]
        new_bundles = [a + " " + b for a in a_words for b in b_words]
        if new_bundles:
            st.session_state.keyword_text = "\n".join(new_bundles)
            st.success(f"총 {len(new_bundles)}개의 번들 검색식이 생성되었습니다!")
            st.rerun()

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. 검색식 입력 (한 줄에 하나씩)")
    st.caption("💡 팁: 띄어쓰기는 AND / '단어A | 단어B'는 OR로 자동 분리 수집됩니다.")
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
    max_count = st.slider("검색식당 최대 수집 건수", min_value=10, max_value=1000, value=100, step=10)
    sort_option = st.radio("정렬 기준", ["정확도순", "최신순"], horizontal=True)
    exclude_master_dup = st.checkbox("👥 이전에 팀원이 이미 수집한 글은 중복 제외하기", value=True)

    # 🌐 자동 번역 옵션
    auto_translate = st.checkbox(
        "🌐 한글 키워드를 영어로 자동 번역하여 해외 채널도 동시 검색",
        value=True,
        help="Reddit, 유튜브 등 해외 채널 선택 시 한글 키워드를 영어로 자동 번역하여 함께 검색합니다."
    )

with col2:
    st.subheader("3. 수집 채널 선택")
    all_channels = get_all_channels()
    selected_channels = []

    for ch in all_channels:
        is_avail, status_msg = ch.check_availability(st.secrets)
        if is_avail:
            if st.checkbox(f"✅ {ch.name}", value=True):
                selected_channels.append(ch)
        elif status_msg == "키 필요":
            st.checkbox(f"🔑 {ch.name} (키 필요)", disabled=True)
        else:
            st.checkbox(f"⏳ {ch.name} (준비 중)", disabled=True)

# 🌐 번역 미리보기
if auto_translate and keywords:
    has_intl = any("Reddit" in c.name or "유튜브" in c.name for c in selected_channels)
    if has_intl:
        with st.expander("🌐 자동 번역 미리보기 (해외 채널용 영어 키워드)"):
            with st.spinner("번역 중..."):
                translations = translate_to_english(keywords)
            if translations:
                for ko, en in translations.items():
                    if ko != en:
                        st.write(f"🇰🇷 **{ko}** → 🇺🇸 **{en}**")
                    else:
                        st.write(f"🇺🇸 **{en}** (이미 영어)")
            else:
                st.warning("번역 라이브러리를 사용할 수 없습니다. 한글 키워드 그대로 검색합니다.")

# 4. 필터 설정
with st.expander("⚙️ 필터 세부 설정 (광고/정보글 제외, 필수어)"):
    f_col1, f_col2 = st.columns(2)
    with f_col1:
        ad_words_text = st.text_area("🚫 광고 금지어",
            value="협찬, 제공받아, 제공받은, 광고, 체험단, 서포터즈, 쿠폰, 최저가, 구매링크, 공동구매, 공구, 할인코드, 쿠팡파트너스, 쿠팡 파트너스, 수수료를 제공, 원고료", height=90)
        info_words_text = st.text_area("📑 정보글 제외어 (제목 기준)",
            value="총정리, 완벽 가이드, 완벽정리, 가이드, 정리해 드립니다, 알아보기, 알아봅시다, 시기별, 단계별, 방법 총, 꿀팁 모음, 나무위키, 위키", height=90)
    with f_col2:
        req_words_text = st.text_area("🍼 육아 필수어",
            value="아기, 아이, 애기, 육아, 엄마, 아빠, 신생아, 개월, 돌, 이유식, 수유, 분유, 모유, 어린이집, 유치원, 통잠, 기저귀, 남편, 와이프", height=90)
        min_length = st.number_input("📏 최소 글자 수", min_value=10, max_value=200, value=30)

filter_config = {
    "ad_words": [w.strip() for w in ad_words_text.split(",")],
    "info_words": [w.strip() for w in info_words_text.split(",")],
    "req_words": [w.strip() for w in req_words_text.split(",")],
    "min_length": min_length
}

# 5. 수집 실행 버튼
st.divider()
if st.button("🚀 수집 시작", type="primary", use_container_width=True):
    if not keywords:
        st.warning("검색식을 최소 하나 이상 입력해 주세요.")
        st.stop()
    if not selected_channels:
        st.warning("수집할 채널을 최소 하나 이상 선택해 주세요.")
        st.stop()

    # 해외 채널용 번역 준비
    translations = {}
    has_intl_channels = any("Reddit" in c.name or "유튜브" in c.name for c in selected_channels)
    if auto_translate and has_intl_channels:
        translations = translate_to_english(keywords)

    all_raw_items = []
    progress_bar = st.progress(0.0)
    status_text = st.empty()
    total_tasks = len(selected_channels) * len(keywords)
    current_task = 0

    for ch in selected_channels:
        is_intl = "Reddit" in ch.name or "유튜브" in ch.name
        for kw in keywords:
            current_task += 1
            search_kw = kw
            display_msg = f"🔄 **[{ch.name}]** · '{kw}' 수집 중..."

            if is_intl and auto_translate and kw in translations and kw != translations[kw]:
                search_kw = translations[kw]
                display_msg = f"🔄 **[{ch.name}]** · '{kw}' → 🌐 '{search_kw}' 수집 중..."

            status_text.write(display_msg)
            items = ch.search(search_kw, max_count, sort_option, st.secrets, status_callback=lambda msg: status_text.info(msg))

            for item in items:
                item["검색식"] = kw

            all_raw_items.extend(items)
            progress_bar.progress(current_task / total_tasks)

    status_text.write("🧹 글 정제 및 팀원 누적 중복 검사 중...")
    valid_items, excluded_items, stats = filter_and_process(all_raw_items, filter_config, exclude_master_dup)

    time_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_bytes, zip_filename = create_zip_package(valid_items, excluded_items, time_tag, worker=worker_name)

    history_record = {
        "수집일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "작업자": worker_name,
        "채널": ", ".join([c.name for c in selected_channels]),
        "검색식": " / ".join(keywords[:2]) + (f" 외 {len(keywords)-2}개" if len(keywords) > 2 else ""),
        "요청건수": max_count,
        "총수집": stats["total"],
        "광고제외": stats["ad"],
        "정보글제외": stats["info"],
        "무관제외": stats["irrelevant"],
        "중복제외": stats["dup"] + stats["master_dup"],
        "최종저장": stats["saved"],
        "파일명": zip_filename
    }
    save_to_master_and_history(valid_items, history_record)

    progress_bar.progress(1.0)
    msg = (f"✅ 수집 완료! 총 {stats['total']}건 중 광고 {stats['ad']} / 정보글 {stats['info']} / "
           f"무관 {stats['irrelevant']} / 팀누적중복 {stats['master_dup']} / 이번중복 {stats['dup']} 제외 "
           f"➔ **최종 {stats['saved']}건** 저장")
    status_text.success(msg)

    st.session_state.valid_items = valid_items
    st.session_state.excluded_items = excluded_items
    st.session_state.last_zip_bytes = zip_bytes
    st.session_state.last_zip_name = zip_filename

# 6. 결과 미리보기 및 즉시 다운로드
if "valid_items" in st.session_state and st.session_state.valid_items:
    st.subheader(f"📋 방금 수집된 결과 미리보기 ({len(st.session_state.valid_items)}건)")
    df_preview = pd.DataFrame(st.session_state.valid_items)[["채널", "검색식", "제목", "작성자", "작성일", "URL"]]
    st.dataframe(df_preview, use_container_width=True)

    st.download_button(
        label=f"💾 방금 수집한 결과 즉시 다운로드 ({st.session_state.last_zip_name})",
        data=st.session_state.last_zip_bytes,
        file_name=st.session_state.last_zip_name,
        mime="application/zip",
        type="primary"
    )

# 7. 회차별 누적 이력 및 개별 다시 다운로드 보관함
st.divider()
st.subheader("📦 우리 팀 누적 수집 이력 & 회차별 다시 다운로드 보관함")
st.caption("새로고침을 하거나 나중에 다시 들어와도, 과거에 수집했던 회차별 zip 파일을 언제든지 각각 다시 다운로드할 수 있습니다.")

hist_df = load_history()
if not hist_df.empty:
    for idx, row in hist_df.iterrows():
        with st.container():
            col_t, col_w, col_k, col_cnt, col_btn = st.columns([2, 1, 3, 1, 2])
            col_t.write(f"🕒 **{row.get('수집일시', '')}**")
            col_w.write(f"👤 {row.get('작업자', '')}")
            col_k.write(f"🔍 {row.get('검색식', '')}")
            col_cnt.write(f"📊 **{row.get('최종저장', 0)}건**")

            fname = row.get("파일명", "")
            file_data = get_archive_file(fname) if fname else None
            if file_data:
                col_btn.download_button(
                    label=f"💾 {row.get('최종저장', 0)}건 받기",
                    data=file_data,
                    file_name=fname,
                    mime="application/zip",
                    key=f"archive_dl_{idx}_{fname}"
                )
            else:
                col_btn.caption("파일 없음")
            st.divider()
else:
    st.info("아직 기록된 수집 이력이 없습니다. 수집을 시작하면 회차별로 다운로드 버튼이 생겨납니다.")

# 8. 데이터 초기화 버튼
with st.expander("🗑️ 전체 기록 초기화"):
    st.warning("아래 버튼을 누르면 수집 이력, 마스터 보관함, 보관 zip 파일이 모두 삭제됩니다.")
    if st.button("🗑️ 모든 기록 삭제하고 처음부터 다시 시작", type="secondary"):
        import shutil
        shutil.rmtree("data", ignore_errors=True)
        for key in ["valid_items", "excluded_items", "last_zip_bytes", "last_zip_name"]:
            if key in st.session_state:
                del st.session_state[key]
        st.success("모든 기록이 초기화되었습니다! 새로고침하면 깨끗한 상태로 시작합니다.")
        st.rerun()
