"""
ccf-deadlines → Notion DB 동기화 스크립트 (v2)

- ccfddl/ccf-deadlines 저장소(conference/**/*.yml)를 읽는다
- conferences.txt 에 적힌 학회만 골라낸다
- 지나지 않은 마감만 Notion DB에 upsert 한다 (키: ID 속성 = <conf id>-<n>)

v2 변경점
- Notion API 2025-09-03 (데이터 소스 모델) 사용. 다중 데이터 소스 DB도 첫 번째 소스에 기록
- 시작 시 DB 속성 이름·타입을 검사해서 틀린 것을 한 번에 알려줌
- HTTP 에러 시 Notion이 보낸 메시지를 그대로 출력

환경변수:
  NOTION_TOKEN  Notion 통합(integration) 시크릿
  NOTION_DB_ID  데이터베이스 ID (32자리 hex). 데이터 소스 ID를 넣어도 동작함
  CCF_DIR       ccf-deadlines 체크아웃 경로 (기본: ../ccf)
"""

import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml

# ---------- 설정 ----------
# Notion DB의 속성 이름. DB 열 이름을 바꿨으면 여기만 고치면 된다.
PROP = {
    "title": "학회",        # 제목(Title)
    "key": "ID",            # 텍스트 — upsert 키
    "deadline": "마감일",    # 날짜
    "abstract": "초록 마감일",  # 날짜 (선택)
    "rank": "Tier",         # 선택 — CCF 등급 A/B/C/N
    "sub": "분야",          # 선택
    "link": "링크",         # URL
    "venue": "개최",        # 텍스트
    "note": "비고",         # 텍스트
}
# 속성별 요구 타입 (검사용)
PROP_TYPE = {
    "title": "title", "key": "rich_text", "deadline": "date", "abstract": "date",
    "rank": "select", "sub": "select", "link": "url", "venue": "rich_text", "note": "rich_text",
}
OPTIONAL = {"abstract", "link", "venue", "note", "sub", "rank"}  # 없어도 동작(그 열만 건너뜀)

NOTION_TOKEN = os.environ.get("NOTION_TOKEN") or sys.exit("NOTION_TOKEN is empty")
NOTION_DB_ID = os.environ.get("NOTION_DB_ID") or sys.exit("NOTION_DB_ID is empty")
CCF_DIR = os.environ.get("CCF_DIR", "../ccf")
KST = timezone(timedelta(hours=9))
API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2025-09-03",
    "Content-Type": "application/json",
}


def req(method: str, path: str, **kw) -> dict:
    r = requests.request(method, f"{API}{path}", headers=HEADERS, timeout=30, **kw)
    if not r.ok:
        print(f"\n[notion {r.status_code}] {method} {path}\n{r.text}\n", file=sys.stderr)
        r.raise_for_status()
    return r.json()


# ---------- 관심 학회 목록 ----------
def load_wanted() -> set[str]:
    wanted = set()
    with open("conferences.txt", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                wanted.add(line.upper())
    return wanted


# ---------- 시간대 변환 ----------
def parse_deadline(s, tz: str):
    """'2026-05-15 23:59:59' + 'UTC-8' / 'AoE' → KST aware datetime. TBD면 None."""
    if not s or str(s).strip().upper() == "TBD":
        return None
    tz = (tz or "UTC").strip()
    if tz.upper() == "AOE":
        offset = -12
    else:
        m = re.match(r"UTC([+-]\d{1,2})", tz, re.I)
        offset = int(m.group(1)) if m else 0
    try:
        naive = datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone(timedelta(hours=offset))).astimezone(KST)


# ---------- ccf-deadlines 읽기 ----------
def collect(wanted: set[str]) -> list[dict]:
    rows = []
    now = datetime.now(KST)
    for path in glob.glob(os.path.join(CCF_DIR, "conference", "**", "*.yml"), recursive=True):
        with open(path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                print(f"[skip] {path}: {e}", file=sys.stderr)
                continue
        if not isinstance(data, list):
            continue
        for conf in data:
            title = str(conf.get("title", "")).upper()
            if title not in wanted:
                continue
            rank = conf.get("rank", {})
            ccf_rank = rank.get("ccf", "") if isinstance(rank, dict) else str(rank or "")
            for inst in conf.get("confs", []):
                tz = inst.get("timezone", "UTC")
                for i, t in enumerate(inst.get("timeline", [])):
                    dl = parse_deadline(t.get("deadline"), tz)
                    if dl is None or dl < now - timedelta(days=7):
                        continue
                    rows.append(
                        {
                            "key": f"{inst['id']}-{i}",
                            "title": f"{conf['title']} {inst['year']}",
                            "deadline": dl,
                            "abstract": parse_deadline(t.get("abstract_deadline"), tz),
                            "note": t.get("comment", "") or "",
                            "rank": ccf_rank or "N",
                            "sub": conf.get("sub", "") or "MX",
                            "link": inst.get("link", "") or "",
                            "venue": f"{inst.get('date', '')} / {inst.get('place', '')}".strip(" /"),
                        }
                    )
    return rows


# ---------- Notion ----------
def resolve_data_source() -> tuple[str, dict]:
    """DB ID → 첫 번째 데이터 소스 ID + 속성 스키마. 데이터 소스 ID를 직접 넣은 경우도 처리."""
    r = requests.get(f"{API}/databases/{NOTION_DB_ID}", headers=HEADERS, timeout=30)
    if r.ok:
        sources = r.json().get("data_sources", [])
        if not sources:
            sys.exit("이 데이터베이스에 데이터 소스가 없습니다.")
        if len(sources) > 1:
            names = ", ".join(s.get("name", "?") for s in sources)
            print(f"[warn] 데이터 소스가 {len(sources)}개 ({names}) — 첫 번째에 기록합니다.", file=sys.stderr)
        ds_id = sources[0]["id"]
    else:
        # 데이터 소스 ID를 직접 넣었을 수도 있음
        r2 = requests.get(f"{API}/data_sources/{NOTION_DB_ID}", headers=HEADERS, timeout=30)
        if not r2.ok:
            print(f"\n[notion {r.status_code}] GET /databases/{NOTION_DB_ID}\n{r.text}\n", file=sys.stderr)
            sys.exit(
                "NOTION_DB_ID 로 데이터베이스를 찾지 못했습니다. 확인할 것:\n"
                "  1) 페이지 ID가 아니라 DB 자체의 ID인지 (인라인 DB면 ⋮⋮ → 페이지로 열기 후 URL의 ?v= 앞 32자리)\n"
                "  2) DB의 ⋯ → 연결 에 이 통합이 추가되어 있는지"
            )
        ds_id = NOTION_DB_ID
    schema = req("GET", f"/data_sources/{ds_id}")["properties"]
    return ds_id, schema


def check_schema(schema: dict) -> set[str]:
    """속성 이름·타입 검사. 문제 있으면 전부 모아서 출력 후 종료. 사용 가능한 논리 키 집합 반환."""
    problems, usable = [], set()
    for logical, name in PROP.items():
        want = PROP_TYPE[logical]
        if name not in schema:
            if logical in OPTIONAL:
                print(f"[info] 속성 '{name}' 없음 — 이 열은 건너뜀", file=sys.stderr)
            else:
                problems.append(f"  - 속성 '{name}' 이(가) 없습니다 (타입: {want})")
            continue
        have = schema[name]["type"]
        if have != want:
            problems.append(f"  - 속성 '{name}' 타입이 {have} 입니다. {want} 여야 합니다")
            continue
        usable.add(logical)
    if problems:
        print("Notion DB 속성이 스크립트와 맞지 않습니다:\n" + "\n".join(problems), file=sys.stderr)
        print("\n현재 DB 속성: " + ", ".join(f"{k}({v['type']})" for k, v in schema.items()), file=sys.stderr)
        sys.exit(1)
    return usable


def to_props(row: dict, usable: set[str]) -> dict:
    p = {
        PROP["title"]: {"title": [{"text": {"content": row["title"]}}]},
        PROP["key"]: {"rich_text": [{"text": {"content": row["key"]}}]},
        PROP["deadline"]: {"date": {"start": row["deadline"].isoformat(), "time_zone": "Asia/Seoul"}},
    }
    if "abstract" in usable and row["abstract"]:
        p[PROP["abstract"]] = {"date": {"start": row["abstract"].isoformat(), "time_zone": "Asia/Seoul"}}
    if "rank" in usable:
        p[PROP["rank"]] = {"select": {"name": row["rank"]}}
    if "sub" in usable:
        p[PROP["sub"]] = {"select": {"name": row["sub"]}}
    if "link" in usable and row["link"]:
        p[PROP["link"]] = {"url": row["link"]}
    if "venue" in usable and row["venue"]:
        p[PROP["venue"]] = {"rich_text": [{"text": {"content": row["venue"][:2000]}}]}
    if "note" in usable and row["note"]:
        p[PROP["note"]] = {"rich_text": [{"text": {"content": row["note"][:2000]}}]}
    return p


def find_page(ds_id: str, key: str):
    res = req(
        "POST",
        f"/data_sources/{ds_id}/query",
        json={"filter": {"property": PROP["key"], "rich_text": {"equals": key}}, "page_size": 1},
    )["results"]
    return res[0]["id"] if res else None


def upsert(ds_id: str, row: dict, usable: set[str]) -> str:
    page_id = find_page(ds_id, row["key"])
    props = to_props(row, usable)
    if page_id:
        req("PATCH", f"/pages/{page_id}", json={"properties": props})
        return "update"
    req("POST", "/pages", json={"parent": {"type": "data_source_id", "data_source_id": ds_id}, "properties": props})
    return "create"


def main():
    wanted = load_wanted()
    rows = collect(wanted)
    print(f"{len(rows)} deadlines to sync for {len(wanted)} conferences")

    ds_id, schema = resolve_data_source()
    usable = check_schema(schema)

    counts = {"create": 0, "update": 0}
    for row in sorted(rows, key=lambda r: r["deadline"]):
        counts[upsert(ds_id, row, usable)] += 1
        print(f"  {row['deadline']:%Y-%m-%d %H:%M}  {row['title']:<16} {row['note']}")
    print(f"done: {counts}")


if __name__ == "__main__":
    main()