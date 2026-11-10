"""
언제볼까?
모임 시간 선정 에이전트
===========================================
기능:
- Google Form 응답을 Google Sheet에서 읽음
- 모든 모임 인원이 참석 확인(Q1 = "참석합니다")을 완료하면,
  가능한 시간대(Q3 체크박스 그리드)에서 공통 가능 시간을 찾아
  Discord 웹훅으로 알림을 보냄

설정 (아래 4개 값만 실제 값으로 채우세요)
1. SERVICE_ACCOUNT_FILE: 서비스 계정 JSON 경로
2. SPREADSHEET_ID:    Google Sheet ID
3. DISCORD_WEBHOOK_URL: Discord 웹훅 URL
4. EXPECTED_MEMBERS:  모임 총 인원 수
===========================================
사용법:
    python when-should-we-meet.py          실제 실행
    python when-should-we-meet.py --dry    미리보기(전송 없이)
"""

import json
import sys
from datetime import datetime
import os

import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

# ===== 설정 =====
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")  # ← 경로 수정
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # ← 시트 ID 수정
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # ← 웹훅 URL 수정
EXPECTED_MEMBERS = int(os.getenv("EXPECTED_MEMBERS", "0"))  # ← 모임 인원 수 수정
SHEET_NAME = os.getenv("SHEET_NAME")  # ← 시트 이름 (기본 "Sheet1")
# ==============

# ---- Q1, Q2, Q3 질문 제목 (시트 컬럼명과 매핑) ----
Q1_TITLE = "참석할 수 있으신가요?"
Q2_TITLE = "성함을 알려주세요."
Q3_TITLE = "가능한 날짜와 시간대를 모두 골라주세요."

# ---- Q1에서 "참석" 값으로 간주할 문자열 ----
ATTEND_VALUE = "참석합니다"


def load_sheet():
    """Google Sheets API로 응답 데이터를 읽음 (2차원 리스트 반환)."""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()
    result = (
        sheet.values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A:Z",
        )
        .execute()
    )
    return result.get("values", [])


def parse_responses(rows):
    """
    시트 데이터를 파싱하여 멤버 정보 목록 반환.
    각 멤버: { "name": str, "attended": bool, "available_times": list[str] }
    """
    if not rows:
        return []

    headers = rows[0]

    def col_idx(title):
        return headers.index(title) if title in headers else None

    idx_q1 = col_idx(Q1_TITLE)
    idx_q2 = col_idx(Q2_TITLE)
    idx_q3 = col_idx(Q3_TITLE)

    members = []
    for row in rows[1:]:
        if not row:
            continue
        # 컬럼 길이가 부족하면 건너뜀
        if idx_q1 is not None and len(row) <= idx_q1:
            continue
        if idx_q2 is not None and len(row) <= idx_q2:
            continue
        if idx_q3 is not None and len(row) <= idx_q3:
            continue

        attended = False
        if idx_q1 is not None:
            attended = str(row[idx_q1]).strip() == ATTEND_VALUE

        name = ""
        if idx_q2 is not None:
            name = str(row[idx_q2]).strip()

        raw_times = ""
        if idx_q3 is not None:
            raw_times = str(row[idx_q3]).strip()

        # Q3 값 파싱:
        #   - 콤마로 구분된 문자열: "10:00, 14:00"
        #   - 또는 여러 셀에 분리된 경우: 세미콜론으로 연결된 경우도 처리
        available_times = [
            t.strip() for t in raw_times.replace(";", ",").split(",") if t.strip()
        ]

        members.append(
            {
                "name": name,
                "attended": attended,
                "available_times": available_times,
            }
        )
    return members


def find_common_time(members):
    """
    모든 참석 멤버의 공통 가능 시간을 찾음.
    없으면 None 반환. 있으면 일정 리스트 반환.
    """
    attended = [m for m in members if m["attended"]]
    if not attended:
        return None

    time_sets = [set(m["available_times"]) for m in attended]
    common = set.intersection(*time_sets) if time_sets else set()
    return sorted(common)


def send_discord_webhook(webhook_url, content):
    """Discord 웹훅으로 메시지 전송."""
    payload = {"content": content}
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


def main():
    print(f"[{datetime.now().isoformat()}] 언제볼까 시작")

    rows = load_sheet()
    if not rows:
        print("시트 데이터가 없습니다.")
        sys.exit(0)

    members = parse_responses(rows)
    print(f"시트에서 {len(members)}명 응답을 읽었습니다.")

    # — 참석 멤버 수 확인
    attended = [m for m in members if m["attended"]]
    if len(attended) < EXPECTED_MEMBERS:
        print(f"아직 참석 확인 인원 부족: {len(attended)}/{EXPECTED_MEMBERS}")
        sys.exit(0)

    # — 공통 가능 시간 계산
    common_times = find_common_time(members)
    if not common_times:
        print("공통 가능한 시간이 없습니다.")
        sys.exit(0)

    # — Discord 알림
    if len(common_times) == 1:
        msg = f"✅ 모든 인원이 참석 확인 완료! 공통 가능 시간: {common_times[0]}"
    else:
        msg = (
            f"✅ 모든 인원이 참석 확인 완료! 공통 가능 시간: {', '.join(common_times)}"
        )

    send_discord_webhook(DISCORD_WEBHOOK_URL, msg)
    print(f"알림 전송 완료: {msg}")


if __name__ == "__main__":
    main()
