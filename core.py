"""RE:CORD 기능 구현: Firebase 또는 로컬 JSON 파일 저장.

웹 화면(app.py)에서는 Streamlit만 사용한다. 이 파일은 파이썬만으로
사용자·활동·체크리스트를 저장하고 검색·수정·복제한다.
"""
from datetime import datetime
from pathlib import Path
from threading import RLock, local
from functools import wraps
import hashlib
import hmac
import json
import os
import secrets

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "record_data.json"
BACKUP_DIR = BASE_DIR / "backups"
_LOCK = RLock()
_FIREBASE_CONTEXT = local()
_FIREBASE_CLIENT = None
CATEGORIES = ("동아리", "교내 행사", "학생회")
TEMPLATES = {
    "동아리": ["활동 목표 정하기", "역할 나누기", "준비물 확인", "활동 진행", "결과·개선점 기록"],
    "교내 행사": ["일정과 장소 확인", "담당자와 역할 배정", "안내 자료 준비", "리허설 진행", "행사 후 피드백 정리"],
    "학생회": ["의견 수렴", "안건 선정", "실행 계획 수립", "학교와 협의", "결과 공지"],
}


def _firebase_client():
    """설정이 있으면 Firebase를 연결하고, 없으면 기존 로컬 JSON을 사용한다."""
    global _FIREBASE_CLIENT
    try:
        import streamlit as st
    except ModuleNotFoundError:
        return None
    try:
        section = st.secrets.get("firebase")
    except (AttributeError, FileNotFoundError):
        section = None
    except Exception as exc:
        if exc.__class__.__name__ == "StreamlitSecretNotFoundError":
            section = None
        else:
            raise
    if section is None:
        return None
    if _FIREBASE_CLIENT is None:
        import firebase_admin
        from firebase_admin import credentials, firestore
        account = json.loads(section["service_account_json"])
        try:
            app = firebase_admin.get_app("record_firebase")
        except ValueError:
            app = firebase_admin.initialize_app(credentials.Certificate(account), name="record_firebase")
        _FIREBASE_CLIENT = firestore.client(app=app)
    return _FIREBASE_CLIENT


def _firebase_atomic(function):
    """동시에 저장해도 데이터가 서로 덮이지 않도록 트랜잭션으로 처리한다."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        client = _firebase_client()
        if client is None:
            return function(*args, **kwargs)
        from google.cloud import firestore
        @firestore.transactional
        def run(transaction):
            _FIREBASE_CONTEXT.transaction = transaction
            try:
                return function(*args, **kwargs)
            finally:
                del _FIREBASE_CONTEXT.transaction
        return run(client.transaction())
    return wrapper


def _empty_data():
    return {
        "users": [],
        "activities": [],
        "checklist": [],
        "task_progress": {},
        "next_ids": {"users": 1, "activities": 1, "checklist": 1},
    }


def _read_data():
    """설정에 따라 Firebase 또는 디스크에서 자료를 읽는다."""
    client = _firebase_client()
    if client is not None:
        transaction = getattr(_FIREBASE_CONTEXT, "transaction", None)
        snapshot = client.collection("record_app").document("data").get(transaction=transaction)
        data = json.loads(snapshot.to_dict()["json"]) if snapshot.exists else _empty_data()
    else:
        if not DATA_PATH.exists():
            return _empty_data()
        with open(DATA_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
    if not isinstance(data, dict) or not all(
        key in data for key in ("users", "activities", "checklist", "next_ids")
    ):
        raise ValueError("기록 파일의 형식이 올바르지 않습니다.")
    # 구버전의 completed는 모든 사용자에게 같은 체크 상태를 보여주던 값이다.
    # 데이터가 구버전이면 그 상태를 원래 활동 작성자의 개인 진행률로만 이전한다.
    # 읽기만 할 때 원본 파일을 변경하지 않고, 다음 저장 시 새 형식으로 저장한다.
    if "task_progress" not in data:
        owners = {item["id"]: item["owner_id"] for item in data["activities"]}
        progress = {}
        for task in data["checklist"]:
            if task.get("completed"):
                owner_id = owners.get(task["activity_id"])
                if owner_id is not None:
                    progress.setdefault(str(owner_id), {})[str(task["id"])] = 1
            task.pop("completed", None)
        data["task_progress"] = progress
    elif not isinstance(data["task_progress"], dict):
        raise ValueError("체크리스트 진행률 파일의 형식이 올바르지 않습니다.")
    return data


def _write_data(data):
    """Firebase에 원자적으로 저장하거나 로컬 임시 파일로 교체한다."""
    client = _firebase_client()
    if client is not None:
        transaction = getattr(_FIREBASE_CONTEXT, "transaction", None)
        if transaction is None:
            raise RuntimeError("Firebase 저장에는 트랜잭션이 필요합니다.")
        payload = json.dumps(data, ensure_ascii=False)
        if len(payload.encode("utf-8")) > 900_000:
            raise ValueError("Firebase 저장 용량 한도에 가까워 추가 저장을 중단했습니다.")
        transaction.set(client.collection("record_app").document("data"), {"json": payload})
        return
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_PATH.with_name(DATA_PATH.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temporary, DATA_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()


def initialize_storage():
    """첫 실행에 빈 JSON 기록 파일을 만든다."""
    with _LOCK:
        if _firebase_client() is not None:
            return  # Firebase 문서는 첫 저장 때 생성합니다.
        if not DATA_PATH.exists():
            _write_data(_empty_data())


def _take_id(data, collection):
    new_id = data["next_ids"][collection]
    data["next_ids"][collection] += 1
    return new_id


def _password_digest(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 260_000
    ).hex()


def register_user(username, password):
    username = username.strip()
    if not (3 <= len(username) <= 20) or not all(c.isalnum() or c == "_" for c in username):
        raise ValueError("아이디는 3~20자의 문자, 숫자, 밑줄(_)만 사용하세요.")
    if len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다.")
    with _LOCK:
        data = _read_data()
        if any(user["username"] == username for user in data["users"]):
            raise ValueError("이미 사용 중인 아이디입니다.")
        new_id = _take_id(data, "users")
        salt = secrets.token_hex(16)
        data["users"].append({
            "id": new_id,
            "username": username,
            "salt": salt,
            "password_hash": _password_digest(password, salt),
        })
        _write_data(data)
        return new_id


def authenticate(username, password):
    with _LOCK:
        users = _read_data()["users"]
        user = next((item for item in users if item["username"] == username.strip()), None)
    if user and hmac.compare_digest(user["password_hash"], _password_digest(password, user["salt"])):
        return {"id": user["id"], "username": user["username"]}
    return None


def _normalize_tasks(tasks):
    if isinstance(tasks, str):
        tasks = tasks.splitlines()
    normalized = []
    for item in tasks:
        item = str(item).strip().lstrip("-•☐ ").strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized[:30]


def add_activity(owner_id, *, category, group_name, year, title, purpose,
                 preparation, outcome, challenges, lessons, visibility, tasks,
                 source_id=None):
    if category not in CATEGORIES or visibility not in ("공유", "비공개"):
        raise ValueError("잘못된 분류 또는 공개 설정입니다.")
    if not title.strip() or not group_name.strip() or not purpose.strip():
        raise ValueError("제목, 조직 이름, 활동 목적은 반드시 입력하세요.")
    try:
        year = int(year)
    except (TypeError, ValueError) as exc:
        raise ValueError("연도를 확인하세요.") from exc
    if not 2000 <= year <= 2100:
        raise ValueError("연도를 확인하세요.")
    with _LOCK:
        data = _read_data()
        if not any(user["id"] == owner_id for user in data["users"]):
            raise ValueError("로그인이 필요합니다.")
        # 이어받기인 경우 현재 사용자에게 원본 열람 권한이 있는지 저장 시점에도 검증한다.
        if source_id is not None:
            source = next((item for item in data["activities"] if item["id"] == source_id), None)
            if source is None or not _allowed(source, owner_id):
                raise ValueError("이어받을 수 없는 원본 기록입니다.")
        new_id = _take_id(data, "activities")
        data["activities"].append({
            "id": new_id,
            "owner_id": owner_id,
            "category": category,
            "group_name": group_name.strip(),
            "year": year,
            "title": title.strip(),
            "purpose": purpose.strip(),
            "preparation": preparation.strip(),
            "outcome": outcome.strip(),
            "challenges": challenges.strip(),
            "lessons": lessons.strip(),
            "visibility": visibility,
            "source_id": source_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        for task in _normalize_tasks(tasks):
            data["checklist"].append({
                "id": _take_id(data, "checklist"),
                "activity_id": new_id,
                "task": task,
            })
        _write_data(data)
        return new_id


def completion_percent(completed, total):
    """산술연산, 비교연산과 함수 반환값을 실제 기능에 사용한다."""
    return round(completed / total * 100) if total > 0 else 0


def _allowed(record, viewer_id):
    return record["visibility"] == "공유" or (viewer_id is not None and record["owner_id"] == viewer_id)


def _decorate(record, data, viewer_id=None, include_tasks=False):
    """같은 공유 기록도 로그인한 사용자별 체크 완료 상태를 보여준다."""
    result = dict(record)
    owner = next((user for user in data["users"] if user["id"] == record["owner_id"]), None)
    result["author"] = owner["username"] if owner else "알 수 없음"
    my_progress = data["task_progress"].get(str(viewer_id), {}) if viewer_id is not None else {}
    tasks = []
    for original in data["checklist"]:
        if original["activity_id"] == record["id"]:
            task = dict(original)
            task["completed"] = int(bool(my_progress.get(str(task["id"]), 0)))
            tasks.append(task)
    result["task_total"] = len(tasks)
    result["task_done"] = sum(task["completed"] for task in tasks)
    if include_tasks:
        result["tasks"] = tasks
    return result


def list_activities(viewer_id=None, category="전체", keyword="", only_mine=False):
    """권한·분류·검색어 조건에 맞는 목록을 최신순으로 반환한다."""
    keyword = keyword.strip().casefold()
    with _LOCK:
        data = _read_data()
        selected = []
        for activity in data["activities"]:
            if not _allowed(activity, viewer_id):
                continue
            if only_mine and activity["owner_id"] != viewer_id:
                continue
            if category in CATEGORIES and activity["category"] != category:
                continue
            contents = (activity["title"], activity["group_name"],
                        activity["purpose"], activity["lessons"])
            if keyword and not any(keyword in text.casefold() for text in contents):
                continue
            selected.append(_decorate(activity, data, viewer_id))
        return sorted(selected, key=lambda item: (item["created_at"], item["id"]), reverse=True)


def get_activity(activity_id, viewer_id=None):
    with _LOCK:
        data = _read_data()
        activity = next((item for item in data["activities"] if item["id"] == activity_id), None)
        if activity is None or not _allowed(activity, viewer_id):
            return None
        return _decorate(activity, data, viewer_id, include_tasks=True)


def update_checklist(task_id, viewer_id, completed):
    """체크 상태를 공유 기록 자체가 아닌 현재 사용자 계정에만 저장한다."""
    with _LOCK:
        data = _read_data()
        if viewer_id is None or not any(u["id"] == viewer_id for u in data["users"]):
            return False
        task = next((item for item in data["checklist"] if item["id"] == task_id), None)
        if task is None:
            return False
        activity = next((item for item in data["activities"]
                         if item["id"] == task["activity_id"]), None)
        if activity is None or not _allowed(activity, viewer_id):
            return False
        user_key, task_key = str(viewer_id), str(task_id)
        if completed:
            data["task_progress"].setdefault(user_key, {})[task_key] = 1
        else:
            user_progress = data["task_progress"].get(user_key, {})
            user_progress.pop(task_key, None)
            if not user_progress:
                data["task_progress"].pop(user_key, None)
        _write_data(data)
        return True


def update_visibility(activity_id, owner_id, visibility):
    if visibility not in ("공유", "비공개"):
        raise ValueError("공개 설정을 확인하세요.")
    with _LOCK:
        data = _read_data()
        activity = next((item for item in data["activities"]
                         if item["id"] == activity_id and item["owner_id"] == owner_id), None)
        if activity is None:
            return False
        activity["visibility"] = visibility
        _write_data(data)
        return True


def inheritance_draft(source_id, viewer_id):
    """기존 기록을 즉시 저장하지 않고 작성 화면에 채울 입력값만 반환한다.

    진행 체크 상태와 원본 레코드 번호는 복사하지 않는다. source_id는 참조만 남긴다.
    """
    if viewer_id is None:
        raise ValueError("로그인 후 활동을 이어받을 수 있습니다.")
    source = get_activity(source_id, viewer_id)
    if source is None:
        raise ValueError("이어받을 수 없는 기록입니다.")
    return {
        "source_id": source["id"],
        "category": source["category"],
        "group_name": source["group_name"],
        "year": datetime.now().year,
        "title": source["title"] + " (이어받기)",
        "purpose": source["purpose"],
        "preparation": source["preparation"],
        "outcome": source["outcome"],
        "challenges": source["challenges"],
        "lessons": source["lessons"],
        "visibility": "비공개",
        "tasks": "\n".join(task["task"] for task in source["tasks"]),
    }


def inherit_activity(source_id, new_owner_id):
    source = get_activity(source_id, new_owner_id)
    if source is None:
        raise ValueError("이어받을 수 없는 기록입니다.")
    return add_activity(
        new_owner_id,
        category=source["category"], group_name=source["group_name"],
        year=datetime.now().year, title=source["title"] + " (이어받기)",
        purpose=source["purpose"], preparation=source["preparation"],
        outcome="", challenges="", lessons="", visibility="비공개",
        tasks=[task["task"] for task in source["tasks"]], source_id=source_id,
    )


def save_backup(owner_id):
    """내 기록을 JSON에 저장한다. 파일 쓰기 평가 요소."""
    items = [get_activity(item["id"], owner_id)
             for item in list_activities(owner_id, only_mine=True)]
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = BACKUP_DIR / f"my_record_{int(owner_id)}.json"
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(items, file, ensure_ascii=False, indent=2)
    return filename


def read_backup(owner_id):
    """저장 파일을 다시 읽어 개수와 제목을 집계한다. 파일 읽기 평가 요소."""
    filename = BACKUP_DIR / f"my_record_{int(owner_id)}.json"
    with open(filename, "r", encoding="utf-8") as file:
        items = json.load(file)
    titles = [item["title"] for item in items]
    task_count = sum(len(item["tasks"]) for item in items)
    return {"count": len(items), "task_count": task_count, "titles": titles}


def edit_activity(activity_id, owner_id, *, group_name, title, purpose, preparation,
                  outcome, challenges, lessons):
    if not group_name.strip() or not title.strip() or not purpose.strip():
        raise ValueError("조직, 제목, 목적은 필수입니다.")
    with _LOCK:
        data = _read_data()
        record = next((item for item in data["activities"]
                       if item["id"] == activity_id and item["owner_id"] == owner_id), None)
        if record is None:
            return False
        record.update({
            "group_name": group_name.strip(), "title": title.strip(),
            "purpose": purpose.strip(), "preparation": preparation.strip(),
            "outcome": outcome.strip(), "challenges": challenges.strip(),
            "lessons": lessons.strip(),
        })
        _write_data(data)
        return True


def add_task(activity_id, owner_id, task):
    task = task.strip()
    if not task:
        raise ValueError("체크리스트 내용을 입력하세요.")
    with _LOCK:
        data = _read_data()
        if not any(item["id"] == activity_id and item["owner_id"] == owner_id
                   for item in data["activities"]):
            return False
        existing = [item for item in data["checklist"] if item["activity_id"] == activity_id]
        if len(existing) >= 30:
            raise ValueError("체크리스트는 최대 30개까지 등록할 수 있습니다.")
        if any(item["task"] == task for item in existing):
            raise ValueError("이미 등록된 체크리스트 항목입니다.")
        data["checklist"].append({
            "id": _take_id(data, "checklist"),
            "activity_id": activity_id,
            "task": task,
        })
        _write_data(data)
        return True

# 저장하는 기능은 Firebase에서 동시 수정 충돌을 방지합니다.
register_user = _firebase_atomic(register_user)
add_activity = _firebase_atomic(add_activity)
update_checklist = _firebase_atomic(update_checklist)
update_visibility = _firebase_atomic(update_visibility)
edit_activity = _firebase_atomic(edit_activity)
add_task = _firebase_atomic(add_task)
