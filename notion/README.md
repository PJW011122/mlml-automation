# notion

[ccfddl/ccf-deadlines](https://github.com/ccfddl/ccf-deadlines)의 학회 마감일을 매주 Notion DB로 가져온다. 서버 없이 GitHub Actions cron으로 돈다.

## 1. Notion DB 준비

`논문 제출일` 페이지 안에 데이터베이스를 만들고 속성을 **이름·타입까지 정확히** 맞춘다. 스크립트가 이 이름으로 쓴다.

| 속성 이름 | 타입 | 비고 |
| --- | --- | --- |
| 학회 | 제목(Title) | 예: `NEURIPS 2026` |
| ID | 텍스트 | upsert 키. 손대지 말 것 |
| 마감일 | 날짜 | KST로 들어감. Notion Calendar 연동은 이 속성으로 |
| 초록 마감 | 날짜 | abstract deadline 있을 때만 채워짐 |
| CCF | 선택 | A / B / C / N |
| 분야 | 선택 | AI, DB, NW … (ccf sub 코드) |
| 링크 | URL | 학회 홈페이지 |
| 개최 | 텍스트 | 날짜 / 장소 |
| 비고 | 텍스트 | round 설명 등 |

원하면 `담당자`(사람), `상태`(선택) 같은 열을 추가해도 된다. 스크립트는 자기가 아는 열만 건드리므로 사람이 채운 열은 보존된다.

## 2. Notion 통합 만들기

1. https://www.notion.so/my-integrations → **새 통합** → 이름 `ccf-sync`, 워크스페이스 선택, 권한은 콘텐츠 읽기·업데이트·삽입
2. 발급된 **Internal Integration Secret** 복사 → `NOTION_TOKEN`
3. 위에서 만든 DB를 전체 페이지로 열고 오른쪽 위 **…** → **연결(Connections)** → `ccf-sync` 추가
4. DB URL에서 ID 복사 → `NOTION_DB_ID`
   `https://www.notion.so/<workspace>/`**`a1b2c3…32자리`**`?v=…` 에서 `?v=` 앞의 32자리

## 3. GitHub 저장소

1. 이 폴더를 새 저장소로 push (공개 저장소면 Actions 무료·무제한)
2. **Settings → Secrets and variables → Actions → New repository secret**
   - `NOTION_TOKEN`
   - `NOTION_DB_ID`
3. **Actions 탭 → Sync CCF deadlines to Notion → Run workflow** 로 한 번 수동 실행해서 DB에 들어오는지 확인
4. 이후 매주 월요일 09:00 KST 자동 실행

## 4. 학회 추가·삭제

`conferences.txt` 수정 후 push. 학회명은 ccf-deadlines의 `title` 필드와 같아야 한다(대소문자 무관). 이름이 궁금하면 https://ccfddl.com 에서 확인.

## 동작 규칙

- 마감이 TBD이거나 7일 넘게 지난 항목은 건너뜀
- 같은 `ID`가 있으면 업데이트, 없으면 새로 생성. 마감일이 바뀌면 자동 반영
- ccf-deadlines에서 항목이 사라져도 Notion에서 지우지는 않음(수동 삭제)

## 로컬 테스트

```bash
git clone https://github.com/ccfddl/ccf-deadlines ccf
pip install requests pyyaml
NOTION_TOKEN=secret_xxx NOTION_DB_ID=xxx python sync.py
```
