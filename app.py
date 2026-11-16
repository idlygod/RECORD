"""RE:CORD — 학교 활동 아카이브.

실행: py -m streamlit run app.py
UI와 동작은 Python + Streamlit으로만 작성했습니다.
개별 HTML/CSS/JavaScript/SQL 코드는 사용하지 않습니다.
"""
from datetime import datetime
from pathlib import Path

import streamlit as st

from core import (
    CATEGORIES,
    TEMPLATES,
    add_activity,
    add_task,
    authenticate,
    completion_percent,
    edit_activity,
    get_activity,
    inheritance_draft,
    initialize_storage,
    list_activities,
    read_backup,
    register_user,
    save_backup,
    update_checklist,
    update_visibility,
)

st.set_page_config(
    page_title="RE:CORD | 학교 활동 아카이브",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "RE:CORD — Python과 Streamlit으로 만든 학교 활동 인수인계 아카이브"},
)
initialize_storage()

PAGES = ("홈", "공유 아카이브", "내 기록", "기록 작성", "이용 안내")
for name, default in {
    "user": None,
    "page": "홈",
    "selected_id": None,
    "origin_page": "공유 아카이브",
    "notice": "",
    "browse_keyword": "",
    "browse_category": "전체",
    "record_draft": None,
    "draft_revision": 0,
}.items():
    if name not in st.session_state:
        st.session_state[name] = default


def current_user_id():
    """로그인 상태를 확인하고 사용자 번호를 반환한다."""
    account = st.session_state.user
    return account["id"] if account else None


def navigate(page, record_id=None, origin=None):
    """현재 화면과 열람할 활동을 변경한다."""
    st.session_state.page = page
    st.session_state.selected_id = record_id
    if origin in PAGES:
        st.session_state.origin_page = origin


def go_to(page, record_id=None, origin=None, *, draft=None):
    if page == "기록 작성":
        # 기록 작성은 누를 때마다 새로운 임시 작성 화면을 만든다.
        # 다른 활동을 이어받거나 신규 작성할 때 예전 입력값이 섞이지 않도록 revision을 변경한다.
        st.session_state.record_draft = draft
        st.session_state.draft_revision += 1
        st.session_state.draft_return = (
            st.session_state.page,
            st.session_state.selected_id,
            st.session_state.origin_page,
        )
    navigate(page, record_id, origin)
    st.rerun()


def abandon_draft():
    """저장 없이 원래 화면으로 돌아간다. 원본 또는 JSON 파일은 수정하지 않는다."""
    destination, record_id, origin = st.session_state.get(
        "draft_return", ("공유 아카이브", None, "공유 아카이브")
    )
    st.session_state.record_draft = None
    if destination not in PAGES and destination != "상세 보기":
        destination = "공유 아카이브"
    go_to(destination, record_id, origin)


def preview(text, size=115):
    """긴 설명을 기록 카드에서 읽기 쉽게 줄인다."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[:size].rstrip() + "…"


def notify(message):
    st.session_state.notice = message


def sidebar_auth():
    user = st.session_state.user
    st.sidebar.divider()
    if user:
        st.sidebar.caption("로그인 계정")
        st.sidebar.markdown(f"**{user['username']}**")
        if st.sidebar.button("로그아웃", key="logout", use_container_width=True):
            st.session_state.user = None
            st.session_state.record_draft = None
            notify("로그아웃했습니다.")
            go_to("홈")
        return

    st.sidebar.caption("활동을 저장하거나 이어받으려면 로그인하세요.")
    with st.sidebar.expander("로그인 / 회원가입", expanded=True):
        login_tab, join_tab = st.tabs(["로그인", "회원가입"])
        with login_tab:
            with st.form("login_form"):
                username = st.text_input("아이디", key="login_id")
                password = st.text_input("비밀번호", type="password", key="login_pw")
                submitted = st.form_submit_button("로그인", use_container_width=True)
            if submitted:
                matched = authenticate(username, password)
                if matched:
                    st.session_state.user = matched
                    notify("로그인했습니다.")
                    st.rerun()
                else:
                    st.error("아이디 또는 비밀번호를 확인하세요.")
        with join_tab:
            with st.form("join_form"):
                new_id = st.text_input("새 아이디", key="join_id")
                new_pw = st.text_input("비밀번호 (8자 이상)", type="password", key="join_pw")
                registered = st.form_submit_button("회원가입", use_container_width=True)
            if registered:
                try:
                    user_id = register_user(new_id, new_pw)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state.user = {"id": user_id, "username": new_id.strip()}
                    notify("계정을 만들었습니다. 이제 활동을 기록할 수 있습니다.")
                    st.rerun()


def sidebar():
    st.sidebar.title("RE:CORD")
    st.sidebar.caption("기록을 남기고, 경험을 이어가다")
    st.sidebar.divider()
    st.sidebar.caption("WORKSPACE")
    for page in PAGES:
        selected = st.session_state.page == page
        if st.sidebar.button(
            page,
            key=f"nav_{page}",
            use_container_width=True,
            type="primary" if selected else "secondary",
        ):
            go_to(page)
    sidebar_auth()
    st.sidebar.divider()
    st.sidebar.caption("Python · Streamlit · JSON")
    st.sidebar.caption("수행평가 시제품 · 학교 공식 시스템 아님")


def page_heading(eyebrow, heading, description, allow_create=True):
    left, right = st.columns([5, 1.5], vertical_alignment="center")
    with left:
        st.caption(eyebrow)
        st.title(heading)
        if description:
            st.write(description)
    with right:
        if allow_create and st.button("＋  새 기록 작성", type="primary", use_container_width=True,
                                      key=f"create_from_{eyebrow}"):
            go_to("기록 작성")
    st.divider()


def copy_into_my_records(record_id, viewer_id):
    """기존 활동을 작성 양식에 미리 채운다. 저장 버튼을 누르기 전에는 복제하지 않는다."""
    if viewer_id is None:
        st.warning("활동을 이어받으려면 먼저 왼쪽 메뉴에서 로그인하거나 회원가입하세요.")
        return
    try:
        draft = inheritance_draft(record_id, viewer_id)
    except (ValueError, OSError) as error:
        st.error(f"활동을 이어받지 못했습니다: {error}")
    else:
        go_to("기록 작성", draft=draft)


def activity_card(item, location):
    with st.container(border=True):
        st.caption(f"{item['category']}   ·   {item['year']}년   ·   {item['visibility']}")
        st.subheader(item["title"])
        st.caption(f"{item['group_name']}  /  작성자 {item['author']}")
        st.write(preview(item["purpose"]))
        percent = completion_percent(item["task_done"], item["task_total"])
        if item["task_total"]:
            st.progress(percent / 100)
            st.caption(f"내 체크리스트 {item['task_done']}/{item['task_total']}개 완료" if current_user_id() is not None
                       else f"체크리스트 {item['task_total']}개 · 로그인 후 진행률 저장")
        else:
            st.caption("체크리스트 없음")
        detail_column, copy_column = st.columns(2, gap="small")
        with detail_column:
            if st.button("자세히 보기", key=f"card_{location}_{item['id']}", use_container_width=True):
                go_to("상세 보기", item["id"], location)
        with copy_column:
            if st.button("활동 이어받기", key=f"copy_{location}_{item['id']}",
                         use_container_width=True):
                copy_into_my_records(item["id"], current_user_id())


def activity_grid(items, location):
    columns = st.columns(2, gap="medium")
    for index, item in enumerate(items):
        with columns[index % 2]:
            activity_card(item, location)


def shared_records(viewer_id):
    """공개 기록만 표시하면서 현재 로그인 사용자의 진행률을 계산한다."""
    return [
        item for item in list_activities(viewer_id)
        if item["visibility"] == "공유"
    ]


def homepage():
    page_heading(
        "THE SCHOOL MEMORY ARCHIVE",
        "한 번의 경험이, 다음 활동의 시작이 되도록.",
        "선배가 남긴 준비 과정과 시행착오를 찾아보고, 내 활동으로 이어받는 학교 기록 공간입니다.",
    )
    user_id = current_user_id()
    public = shared_records(user_id)
    mine = list_activities(user_id, only_mine=True) if user_id is not None else []
    inherited = sum(1 for item in mine if item.get("source_id") is not None)

    overview = st.columns(3, gap="medium")
    for column, title, value, subtitle in zip(
        overview,
        ("공유된 활동", "내가 남긴 기록", "이어받은 활동"),
        (len(public), len(mine), inherited),
        ("누구나 열람할 수 있는 기록", "내가 작성한 활동 문서", "복제해 새로 시작한 활동"),
    ):
        with column:
            with st.container(border=True):
                st.metric(title, value)
                st.caption(subtitle)

    st.write("")
    st.subheader("분야별로 둘러보기")
    st.caption("찾고 싶은 학교 활동의 종류를 선택하세요.")
    categories = st.columns(3, gap="medium")
    descriptions = {
        "동아리": "프로젝트·탐구·동아리 운영 경험",
        "교내 행사": "축전·캠프·행사 준비 및 진행",
        "학생회": "건의·기획·학교 자치 활동",
    }
    for column, category in zip(categories, CATEGORIES):
        with column:
            with st.container(border=True):
                st.subheader(category)
                count = sum(item["category"] == category for item in public)
                st.caption(f"{descriptions[category]}  ·  공유 기록 {count}개")
                if st.button("이 분야 보기", key=f"home_cat_{category}", use_container_width=True):
                    st.session_state.browse_category = category
                    go_to("공유 아카이브")

    st.write("")
    header, action = st.columns([5, 1.4], vertical_alignment="center")
    with header:
        st.subheader("최근 공유 기록")
        st.caption("가장 최근에 남겨진 활동부터 보여줍니다.")
    with action:
        if st.button("전체 기록 보기", use_container_width=True, key="home_all"):
            st.session_state.browse_category = "전체"
            go_to("공유 아카이브")
    if public:
        activity_grid(public[:4], "홈")
    else:
        st.info("아직 공유된 활동이 없습니다. 계정을 만든 후 첫 활동을 공유해 보세요.")


def filtered_records(base_records):
    """선택한 분야·연도·검색어로 자료를 걸러 반환한다."""
    category = st.session_state.browse_category
    keyword = st.session_state.browse_keyword.strip().casefold()
    matches = []
    for item in base_records:
        if category in CATEGORIES and item["category"] != category:
            continue
        haystack = " ".join(str(item[key]) for key in
                            ("title", "group_name", "purpose", "lessons")).casefold()
        if keyword and keyword not in haystack:
            continue
        matches.append(item)
    return matches


def backup_section(user_id):
    with st.expander("내 기록 JSON 백업 · 파일 입출력", expanded=False):
        st.write("내 활동을 JSON 파일로 저장한 뒤 다시 읽어 활동 수와 체크리스트 수를 확인합니다.")
        if st.button("백업 저장 및 파일 다시 읽기", key="make_backup"):
            try:
                path = save_backup(user_id)
                summary = read_backup(user_id)
                st.session_state.backup_result = (str(path), summary)
            except (OSError, ValueError) as error:
                st.error(f"백업 중 오류가 발생했습니다: {error}")
        if "backup_result" in st.session_state:
            filename, summary = st.session_state.backup_result
            expected = f"my_record_{user_id}.json"
            if Path(filename).name == expected and Path(filename).exists():
                st.success(f"저장·읽기 완료: 활동 {summary['count']}개, 체크리스트 {summary['task_count']}개")
                st.caption("포함된 기록: " + (", ".join(summary["titles"]) or "없음"))
                st.download_button(
                    "백업 파일 다운로드",
                    data=Path(filename).read_bytes(),
                    file_name="my_record_backup.json", mime="application/json",
                    key="backup_download",
                )


def archive(only_mine=False):
    user_id = current_user_id()
    if only_mine and user_id is None:
        page_heading("MY LIBRARY", "내 기록", "로그인 후 개인 기록과 이어받은 기록을 관리할 수 있습니다.", False)
        st.warning("왼쪽 메뉴에서 로그인하거나 회원가입해 주세요.")
        return

    location = "내 기록" if only_mine else "공유 아카이브"
    page_heading(
        "MY LIBRARY" if only_mine else "EXPLORE THE ARCHIVE",
        "내 활동 기록" if only_mine else "활동 아카이브",
        "공개 여부와 관계없이 내가 작성한 기록을 확인하고 수정합니다." if only_mine else
        "동아리·교내 행사·학생회의 기록에서 준비 과정과 후임자 조언을 검색하세요.",
    )
    originals = (list_activities(user_id, only_mine=True)
                 if only_mine else shared_records(user_id))
    # 공유 아카이브는 공개 기록만 표시하되, 체크리스트 진행률은 계정마다 다르게 표시한다.

    st.text_input(
        "활동 검색", placeholder="활동 제목, 소속, 목적, 배운 점 검색",
        key="browse_keyword",
    )
    filter_col, year_col, sort_col = st.columns([3, 1, 1], gap="medium")
    with filter_col:
        st.radio("분류", ["전체", *CATEGORIES], horizontal=True, key="browse_category")
    with year_col:
        years = ["전체", *sorted({item["year"] for item in originals}, reverse=True)]
        chosen_year = st.selectbox("활동 연도", years, key=f"year_{location}")
    with sort_col:
        sort = st.selectbox("정렬", ["최신순", "오래된순", "제목순"], key=f"sort_{location}")

    filtered = filtered_records(originals)
    if chosen_year != "전체":
        filtered = [item for item in filtered if item["year"] == chosen_year]
    if sort == "오래된순":
        filtered.sort(key=lambda item: (item["created_at"], item["id"]))
    elif sort == "제목순":
        filtered.sort(key=lambda item: item["title"].casefold())

    left, right = st.columns([4, 1])
    with left:
        st.caption(f"{len(filtered)}개의 기록  /  전체 {len(originals)}개")
    with right:
        if st.button("검색 초기화", key=f"clear_{location}", use_container_width=True):
            st.session_state.browse_keyword = ""
            st.session_state.browse_category = "전체"
            # 연도·정렬 위젯도 기본 상태로 되돌린다.
            st.session_state[f"year_{location}"] = "전체"
            st.session_state[f"sort_{location}"] = "최신순"
            st.rerun()

    if filtered:
        activity_grid(filtered, location)
    elif originals:
        st.info("검색 조건에 맞는 기록이 없습니다. 분류 또는 검색어를 바꿔 보세요.")
    elif only_mine:
        st.info("작성한 활동이 없습니다. '새 기록 작성'에서 첫 활동을 남겨 보세요.")
    else:
        st.info("아직 공유된 활동이 없습니다.")

    if only_mine:
        st.divider()
        backup_section(user_id)


def text_section(title, content):
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.write(content if content else "아직 작성된 내용이 없습니다.")


def task_section(record, is_owner, viewer_id):
    tasks = record["tasks"]
    done = sum(bool(task["completed"]) for task in tasks)
    percentage = completion_percent(done, len(tasks))
    st.subheader("나의 진행 체크리스트" if viewer_id is not None else "활동 체크리스트")
    st.caption(f"{done} / {len(tasks)}개 완료  ·  {percentage}%")
    if viewer_id is None:
        st.caption("로그인하면 체크 항목의 완료 상태를 내 계정에만 저장할 수 있습니다.")
    else:
        st.caption("체크 상태는 각 계정별로 저장됩니다. 다른 사람의 진행률에는 영향을 주지 않습니다.")
    st.progress(percentage / 100)
    if not tasks:
        st.info("등록된 체크리스트가 없습니다.")
    for task in tasks:
        checked = st.checkbox(
            task["task"], value=bool(task["completed"]),
            disabled=viewer_id is None,
            key=f"task_{viewer_id if viewer_id is not None else 'guest'}_{record['id']}_{task['id']}",
        )
        if viewer_id is not None and checked != bool(task["completed"]):
            if update_checklist(task["id"], viewer_id, checked):
                st.rerun()
            else:
                st.error("진행률을 저장할 수 없습니다.")
    if is_owner:
        with st.form(f"add_task_{record['id']}", clear_on_submit=True):
            new_task = st.text_input("새 체크리스트 항목", placeholder="예: 행사 전날 장비 최종 점검")
            submitted = st.form_submit_button("항목 추가")
        if submitted:
            try:
                changed = add_task(record["id"], viewer_id, new_task)
                if changed:
                    st.rerun()
                else:
                    st.error("항목을 추가할 권한이 없습니다.")
            except ValueError as error:
                st.error(str(error))


def owner_tools(record, user_id):
    st.subheader("내 기록 관리")
    st.caption("공개 범위를 바꾸거나 기록 내용을 수정할 수 있습니다.")
    visibility = st.radio(
        "공개 범위", ["비공개", "공유"],
        index=0 if record["visibility"] == "비공개" else 1,
        horizontal=True, key=f"visibility_{record['id']}",
    )
    if visibility != record["visibility"]:
        if update_visibility(record["id"], user_id, visibility):
            notify("공개 범위를 변경했습니다.")
            st.rerun()
        else:
            st.error("공개 범위를 변경할 수 없습니다.")
    with st.expander("기록 내용 수정하기", expanded=False):
        with st.form(f"edit_{record['id']}"):
            group = st.text_input("조직명", value=record["group_name"])
            title = st.text_input("활동 제목", value=record["title"])
            purpose = st.text_area("활동 목적", value=record["purpose"])
            prep = st.text_area("준비 과정", value=record["preparation"])
            outcome = st.text_area("활동 결과", value=record["outcome"])
            challenges = st.text_area("시행착오·주의사항", value=record["challenges"])
            lessons = st.text_area("배운 점·후임자 조언", value=record["lessons"])
            saved = st.form_submit_button("수정 내용 저장", type="primary")
        if saved:
            try:
                success = edit_activity(
                    record["id"], user_id,
                    group_name=group, title=title, purpose=purpose,
                    preparation=prep, outcome=outcome, challenges=challenges, lessons=lessons,
                )
                if not success:
                    st.error("수정 권한이 없습니다.")
                else:
                    notify("기록 내용을 수정했습니다.")
                    st.rerun()
            except ValueError as error:
                st.error(str(error))


def activity_detail():
    viewer_id = current_user_id()
    record_id = st.session_state.selected_id
    record = get_activity(record_id, viewer_id) if record_id is not None else None
    if st.button("← 목록으로 돌아가기", key="detail_back"):
        go_to(st.session_state.origin_page)
    if record is None:
        st.warning("기록이 없거나 열람 권한이 없습니다.")
        return

    is_owner = viewer_id is not None and record["owner_id"] == viewer_id
    st.caption(f"{record['category']}  /  {record['group_name']}  /  {record['year']}년")
    st.title(record["title"])
    info_col, button_col = st.columns([4, 1.5], vertical_alignment="center")
    with info_col:
        st.caption(f"작성자 {record['author']}  ·  {record['visibility']} 기록  ·  등록 {record['created_at'][:10]}")
        if record.get("source_id") is not None:
            st.caption("이 기록은 이전 활동을 이어받아 생성했습니다.")
    with button_col:
        if st.button("이 활동 이어받기", type="primary", use_container_width=True,
                     key=f"inherit_{record_id}"):
            copy_into_my_records(record_id, viewer_id)
        if viewer_id is None:
            st.caption("로그인하면 내 기록으로 복사할 수 있습니다.")
        elif is_owner:
            st.caption("내 기록도 새 활동으로 다시 이어받을 수 있습니다.")
    st.divider()

    st.subheader("활동 기록")
    text_section("목적 / 해결하려는 문제", record["purpose"])
    left, right = st.columns(2, gap="medium")
    with left:
        text_section("준비 과정", record["preparation"])
        text_section("시행착오 / 주의사항", record["challenges"])
    with right:
        text_section("활동 결과 / 성과", record["outcome"])
        text_section("배운 점 / 다음 사람에게", record["lessons"])
    st.divider()
    task_section(record, is_owner, viewer_id)
    if is_owner:
        st.divider()
        owner_tools(record, viewer_id)


def create_activity():
    draft = st.session_state.record_draft or {}
    inherited = draft.get("source_id") is not None
    page_heading(
        "CONTINUE A RECORD" if inherited else "NEW RECORD",
        "활동 이어받아 작성하기" if inherited else "새 활동 기록",
        "기존 활동 내용과 체크리스트가 미리 입력되어 있습니다. 자유롭게 수정하고 저장하세요." if inherited
        else "학교에서 진행한 활동을 남기고, 필요할 때 후배에게 공유하세요.",
        False,
    )
    user_id = current_user_id()
    if user_id is None:
        st.warning("왼쪽 메뉴에서 먼저 로그인하거나 회원가입해 주세요.")
        return

    if inherited:
        st.info("이 화면을 열기만 해서는 복사본이 저장되지 않습니다. 수정 후 아래의 '이어받은 활동 저장하기'를 눌러야 내 기록에 추가됩니다.")
    if st.button("← 저장하지 않고 돌아가기", key=f"cancel_record_{st.session_state.draft_revision}"):
        abandon_draft()

    revision = st.session_state.draft_revision
    prefix = f"draft_{revision}"
    st.caption("01  기본 정보")
    original_category = draft.get("category", CATEGORIES[0])
    category = st.selectbox(
        "활동 분류", CATEGORIES,
        index=CATEGORIES.index(original_category) if original_category in CATEGORIES else 0,
        key=f"{prefix}_category",
    )
    st.caption("추천 체크리스트: " + "  /  ".join(TEMPLATES[category]))
    with st.form(f"record_form_{revision}", clear_on_submit=False):
        c1, c2 = st.columns([3, 1])
        with c1:
            group = st.text_input(
                "소속 조직 / 행사명 *", value=draft.get("group_name", ""),
                placeholder="예: 과학축전 준비팀", key=f"{prefix}_group",
            )
        with c2:
            year = st.number_input(
                "활동 연도 *", 2000, 2100,
                int(draft.get("year", datetime.now().year)), 1,
                key=f"{prefix}_year",
            )
        title = st.text_input(
            "기록 제목 *", value=draft.get("title", ""),
            placeholder="예: 과학축전 체험 부스 준비 과정", key=f"{prefix}_title",
        )
        purpose = st.text_area(
            "활동 목적 *", value=draft.get("purpose", ""),
            placeholder="왜 이 활동을 시작했나요?", key=f"{prefix}_purpose",
        )
        st.divider()
        st.caption("02  활동 진행과 회고")
        prep = st.text_area(
            "준비 과정", value=draft.get("preparation", ""),
            placeholder="일정, 역할 분담, 사용한 준비물 등을 적으세요.", key=f"{prefix}_prep",
        )
        outcome = st.text_area(
            "활동 결과 / 성과", value=draft.get("outcome", ""),
            placeholder="무엇을 완성하고 어떤 결과를 얻었나요?", key=f"{prefix}_outcome",
        )
        challenges = st.text_area(
            "시행착오 / 주의사항", value=draft.get("challenges", ""),
            placeholder="실패한 시도와 다음에 주의할 점은 무엇인가요?", key=f"{prefix}_challenges",
        )
        lessons = st.text_area(
            "배운 점 / 후임자 조언", value=draft.get("lessons", ""),
            placeholder="다음 담당자에게 꼭 전하고 싶은 내용", key=f"{prefix}_lessons",
        )
        st.divider()
        st.caption("03  체크리스트 및 공개 설정")
        task_lines = st.text_area(
            "준비 체크리스트 (한 줄에 하나씩)",
            value=draft.get("tasks", "\n".join(TEMPLATES[category])),
            key=(f"{prefix}_inherited_tasks" if inherited
                 else f"{prefix}_new_tasks_{category}"),
            height=150,
        )
        visibility = st.radio(
            "공개 범위", ["비공개", "공유"], horizontal=True,
            index=0 if draft.get("visibility", "비공개") == "비공개" else 1,
            key=f"{prefix}_visibility",
            help="비공개는 내 계정에서만, 공유는 모든 방문자가 열람할 수 있습니다.",
        )
        st.caption("* 표시는 필수 항목입니다. 개인정보를 기록하지 마세요.")
        saved = st.form_submit_button(
            "이어받은 활동 저장하기" if inherited else "기록 저장하기",
            type="primary", use_container_width=True,
        )
    if saved:
        try:
            new_id = add_activity(
                user_id, category=category, group_name=group, year=year,
                title=title, purpose=purpose, preparation=prep,
                outcome=outcome, challenges=challenges, lessons=lessons,
                visibility=visibility, tasks=task_lines,
                source_id=draft.get("source_id"),
            )
            st.session_state.record_draft = None
            notify("이어받은 내용을 새 기록으로 저장했습니다." if inherited
                   else "활동을 저장했습니다. 자세한 내용과 체크리스트를 확인하세요.")
            go_to("상세 보기", new_id, "내 기록")
        except (ValueError, TypeError, OSError) as error:
            st.error(str(error))


def help_page():
    page_heading("HOW IT WORKS", "RE:CORD 이용 안내", "기록부터 다음 활동으로 이어받는 과정까지", False)
    st.subheader("RE:CORD에서 할 수 있는 일")
    for title, explanation in [
        ("1. 기록 남기기", "동아리, 교내 행사, 학생회 활동을 정해진 양식으로 작성합니다."),
        ("2. 공유 여부 선택하기", "비공개 기록은 작성자만 보고, 공유 기록은 모든 방문자가 볼 수 있습니다."),
        ("3. 이전 경험 찾아보기", "활동명·소속·목적·배운 점으로 검색하고 분류 및 연도를 선택합니다."),
        ("4. 활동 이어받기", "기존 기록을 선택하면 새 기록 작성 화면에 활동 내용과 체크리스트가 미리 입력됩니다. 저장해야 개인 기록이 생성됩니다."),
        ("5. 실제 기록 이어가기", "이어받은 기록의 체크리스트는 모두 미완료로 시작합니다. 이후 진행률을 기록합니다."),
        ("6. JSON 파일 백업", "내 기록 화면에서 내 활동 파일을 저장·읽고 백업 파일을 내려받습니다."),
    ]:
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.write(explanation)
    st.info("이 앱은 학교 수행평가용 시제품입니다. 실명·전화번호·학생번호 등 개인정보를 기록하지 마세요. 학교 공식 시스템이나 인터넷 공개 서비스로 사용하지 마세요.")


def main():
    sidebar()
    if st.session_state.notice:
        st.success(st.session_state.notice)
        st.session_state.notice = ""
    page = st.session_state.page
    if page == "홈":
        homepage()
    elif page == "공유 아카이브":
        archive()
    elif page == "내 기록":
        archive(only_mine=True)
    elif page == "기록 작성":
        create_activity()
    elif page == "상세 보기":
        activity_detail()
    else:
        help_page()


if __name__ == "__main__":
    main()
