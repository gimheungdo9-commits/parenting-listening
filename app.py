import streamlit as st
import pandas as pd
from datetime import datetime
from channel_registry import get_all_channels
from processor import filter_and_process, create_zip_package, load_history, save_to_master_and_history, load_master_urls

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

# 검색식 세션 상태 초기화
if "keyword_text" not in st.session_state:
    st.session_state.keyword_text = """이유식 거부
신생아 통잠 안자요
독박육아 힘들어
아기 열 응급실
어린이집 적응 울음"""

# 💡 키워드 번들 조합기 (AND / OR 마법사)
with st.expander("🧩 [추천] 키워드 번들 조합기 (AND / OR 자동 생성기)"):
    st.caption("주제어(AND)와 다양한 상황/고민어(OR)를 넣으면 고효율 검색식을 자동으로 조합해 줍니다.")
    b_col1, b_col2 = st.columns(2)
    with b_col1:
        group_a = st.text_input("📌 필수 주제어 (AND)", value="이유식", help="반드시 들어가야 할 단어 (예: 이유식, 분유, 어린이집)")
    with b_col2:
        group_b = st.text_input("🔀 상황/고민 키워드들 (OR - 쉼표로 구분)", value="거부, 안먹어요, 뱉음, 울음, 입꾹닫", help="이 중 하나라도 걸리면 되는 유의어들")
    
    if st.button("👉 번들 검색식 생성해서 아래 입력창에 넣기"):
        a_words = [w.strip() for w in group_a.split(",") if w.strip()]
        b_words = [w.strip() for w in group_b.split(",") if w.strip()]
        new_bundles = []
        for a in a_words:
            for b in b_words:
                new_bundles.append(f"{a} {b}")
        if new_bundles:
            st.session_state.keyword_text = "\n".join(new_bundles)
            st.success(f"총 {len(new_bundles)}개의 고효율 번들 검색식이 생성되었습니다!")
            st.rerun()

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. 검색식 입력 (한 줄에 하나씩)")
    st.caption("💡 팁: 띄어쓰기는 AND / 한 줄에 '단어A | 단어B'를 넣으면 OR로 자동 분리 수집됩니다.")
    keyword_text = st.text_area("검색어 목록", value=st.session_state.keyword_text, height=140)
    st.session_state.keyword_text = keyword_text

    # 검색식 파싱 (OR 연산자 | 처리 포함)
    raw_lines = [k.strip() for k in keyword_text.splitlines() if k.strip()]
    keywords = []
    for line in raw_lines:
        if "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            keywords.extend(parts)
        else:
            keywords.append(line)

    st.subheader("2. 수집 옵션")
    max_count = st.slider("검색식당 최대 수집 건수", min_value=10, max_value=1000, value=100, step=10)
    sort_option = st.radio("정렬 기준", ["정확도순", "최신순"], horizontal=True)
    exclude_master_dup = st.checkbox("👥 이전에 팀원이 이미 수집한 글은 중복 제외하기", value=True)

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

    all_raw_items = []
    progress_bar = st.progress(0.0)
    status_text = st.empty()
    total_tasks = len(selected_channels) * len(keywords)
    current_task = 0

    for ch in selected_channels:
        for kw in keywords:
            current_task += 1
            status_text.write(f"🔄 **[{ch.name}]** · '{kw}' 수집 중...")
            items = ch.search(kw, max_count, sort_option, st.secrets, status_callback=lambda msg: status_text.info(msg))
            all_raw_items.extend(items)
            progress_bar.progress(current_task / total_tasks)

    status_text.write("🧹 글 정제 및 팀원 누적 중복 검사 중...")
    valid_items, excluded_items, stats = filter_and_process(all_raw_items, filter_config, exclude_master_dup)

    history_record = {
        "수집일시": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "작업자": worker_name,
        "채널": ", ".join([c.name for c in selected_channels]),
        "검색식": " / ".join(keywords[:2]) + (f" 외 {len(keywords)-2}개" if len(keywords) > 2 else ""),
        "요청건수": max_count,
        "총수집": stats["total"],
        "광고제외": stats["ad"],
        "정보글제외": stats["info"],
        "무관제외": stats["irrelevant"],
        "중복제외": stats["dup"] + stats["master_dup"],
        "최종저장": stats["saved"]
    }
    save_to_master_and_history(valid_items, history_record)

    progress_bar.progress(1.0)
    msg = (f"✅ 수집 완료! 총 {stats['total']}건 중 광고 {stats['ad']} / 정보글 {stats['info']} / "
           f"무관 {stats['irrelevant']} / 팀누적중복 {stats['master_dup']} / 이번중복 {stats['dup']} 제외 ➔ **최종 {stats['saved']}건** 저장")
    status_text.success(msg)

    st.session_state.valid_items = valid_items
    st.session_state.excluded_items = excluded_items

# 6. 결과 표 및 다운로드
if "valid_items" in st.session_state and st.session_state.valid_items:
    st.subheader(f"📋 수집 결과 미리보기 ({len(st.session_state.valid_items)}건)")
    df_preview = pd.DataFrame(st.session_state.valid_items)[["채널", "검색식", "제목", "작성자", "작성일", "URL"]]
    st.dataframe(df_preview, use_container_width=True)

    time_tag = datetime.now().strftime("%Y%m%d_%H%M")
    zip_bytes = create_zip_package(st.session_state.valid_items, st.session_state.get("excluded_items", []), time_tag)

    st.download_button(
        label=f"💾 결과 전체 다운로드 (육아수집_{time_tag}.zip)",
        data=zip_bytes,
        file_name=f"육아수집_{time_tag}.zip",
        mime="application/zip",
        type="primary"
    )

# 7. 팀 누적 수집 이력 장부
st.divider()
st.subheader("📜 우리 팀 누적 수집 이력 장부")
history_df = load_history()
if not history_df.empty:
    st.dataframe(history_df, use_container_width=True)
else:
    st.caption("아직 기록된 수집 이력이 없습니다. 수집을 시작하면 자동으로 기록됩니다.")