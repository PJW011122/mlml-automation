"""
ccf-deadlines → Notion DB 동기화 스크립트

- ccfddl/ccf-deadlines 저장소(conference/**/*.yml)를 읽는다
- conferences.txt 에 적힌 학회만 골라낸다
- 아직 지나지 않은 마감(현재 연도 이후)만 Notion DB에 upsert 한다
  (키: "ID" 속성 = <conf id>-<n>, 예: neurips26-0)

환경변수:
  NOTION_TOKEN  Notion 통합(integration) 시크릿
  NOTION_DB_ID  대상 데이터베이스 ID (32자리 hex, 하이픈 유무 무관)
  CCF_DIR       ccf-deadlines 체크아웃 경로 (기본: ./ccf)
"""

import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
import yaml

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]
CCF_DIR = os.environ.get("CCF_DIR", "ccf")
KST = timezone(timedelta(hours=9))

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


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
def parse_deadline(s: str, tz: str) -> datetime | None:
    """'2026-05-15 23:59:59' + 'UTC-8' / 'AoE' → KST aware datetime. TBD면 None."""
    if not s or str(s).strip().upper() == "TBD":
        return None
    tz = (tz or "UTC").strip()
    if tz.upper() == "AOE":
        offset = -12
    else:
        m = re.match(r"UTC([+-]\d{1,2})", tz, re.I)
        offset = int(m.group(1)) if m else 0
    naive = datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    aware = naive.replace(tzinfo=timezone(timedelta(hours=offset)))
    return aware.astimezone(KST)


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
            ccf_rank = rank.get("ccf", "") if isinstance(rank, dict) else str(rank)
            for inst in conf.get("confs", []):
                tz = inst.get("timezone", "UTC")
                for i, t in enumerate(inst.get("timeline", [])):
                    dl = parse_deadline(t.get("deadline"), tz)
                    if dl is None or dl < now - timedelta(days=7):
                        continue  # TBD 이거나 일주일 넘게 지난 마감은 제외
                    abs_dl = parse_deadline(t.get("abstract_deadline"), tz)
                    rows.append(
                        {
                            "key": f"{inst['id']}-{i}",
                            "title": f"{conf['title']} {inst['year']}",
                            "deadline": dl,
                            "abstract": abs_dl,
                            "comment": t.get("comment", "") or "",
                            "rank": ccf_rank,
                            "sub": conf.get("sub", ""),
                            "link": inst.get("link", ""),
                            "place": inst.get("place", ""),
                            "date": inst.get("date", ""),
                        }
                    )
    return rows


# ---------- Notion ----------
def notion_query_by_key(key: str) -> str | None:
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
        headers=HEADERS,
        json={"filter": {"property": "ID", "rich_text": {"equals": key}}},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()["results"]
    return res[0]["id"] if res else None


def to_props(row: dict) -> dict:
    props = {
        "학회": {"title": [{"text": {"content": row["title"]}}]},
        "ID": {"rich_text": [{"text": {"content": row["key"]}}]},
        "마감일": {"date": {"start": row["deadline"].isoformat(), "time_zone": "Asia/Seoul"}},
        "CCF": {"select": {"name": row["rank"] or "N"}},
        "분야": {"select": {"name": row["sub"] or "MX"}},
        "비고": {"rich_text": [{"text": {"content": row["comment"][:2000]}}]},
        "개최": {"rich_text": [{"text": {"content": f"{row['date']} / {row['place']}"[:2000]}}]},
    }
    if row["link"]:
        props["링크"] = {"url": row["link"]}
    if row["abstract"]:
        props["초록 마감"] = {"date": {"start": row["abstract"].isoformat(), "time_zone": "Asia/Seoul"}}
    return props


def upsert(row: dict) -> str:
    page_id = notion_query_by_key(row["key"])
    body = {"properties": to_props(row)}
    if page_id:
        r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=HEADERS, json=body, timeout=30)
        action = "update"
    else:
        body["parent"] = {"database_id": NOTION_DB_ID}
        r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=body, timeout=30)
        action = "create"
    if not r.ok:
        print(f"[error] {row['key']}: {r.status_code} {r.text}", file=sys.stderr)
        r.raise_for_status()
    return action


def main():
    wanted = load_wanted()
    rows = collect(wanted)
    print(f"{len(rows)} deadlines to sync for {len(wanted)} conferences")
    counts = {"create": 0, "update": 0}
    for row in sorted(rows, key=lambda r: r["deadline"]):
        counts[upsert(row)] += 1
        print(f"  {row['deadline']:%Y-%m-%d %H:%M}  {row['title']:<16} {row['comment']}")
    print(f"done: {counts}")


if __name__ == "__main__":
    main()
