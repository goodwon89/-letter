# 인재경영실 Letter — 시스템 설정 가이드

상상인그룹 인재경영실 | Notion 기반 뉴스레터 발행 시스템

---

## 📁 파일 구성

```
hr-letter-system/
├── hr_letter.py                        ← 메인 스크립트 (수정 불필요)
├── .github/
│   └── workflows/
│       └── hr_letter.yml               ← GitHub Actions 워크플로우
└── SETUP_GUIDE.md                      ← 이 파일
```

GitHub 저장소에 업로드 후 아래 설정을 완료하면 바로 사용 가능합니다.

---

## Step 1. GitHub 저장소 생성

1. GitHub에서 새 저장소 생성 (예: `hr-letter` 또는 `sangsangin-hr-letter`)
2. **Private 또는 Public** 중 선택 (Public이어야 GitHub Pages 무료 사용 가능)
3. 위 3개 파일을 저장소에 업로드

---

## Step 2. Notion Integration 설정

### 2-1. Integration 생성
1. [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations) 접속
2. **New integration** 클릭
3. 이름: `HR Letter` (임의)
4. **Submit** → **Internal Integration Token** 복사 (시작: `secret_...`)

### 2-2. 레터를 쓸 Notion 페이지 공유 설정
- 발행할 페이지 우측 상단 `...` → **Connections** → 생성한 Integration 추가
- ⚠️ 페이지마다 공유 설정이 필요합니다

---

## Step 3. Gmail 앱 비밀번호 발급

1. [Google 계정](https://myaccount.google.com) → 보안
2. **2단계 인증** 활성화 (필수)
3. 앱 비밀번호 → 앱: `메일`, 기기: `기타` → 16자리 생성

---

## Step 4. GitHub Secrets 등록

저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 이름 | 값 | 설명 |
|-------------|-----|------|
| `NOTION_TOKEN` | `secret_xxx...` | Notion Integration 토큰 |
| `GMAIL_USER` | `xxx@gmail.com` | 발신 Gmail 주소 |
| `GMAIL_APP_PASS` | `xxxx xxxx xxxx xxxx` | Gmail 앱 비밀번호 16자리 |
| `EMAIL_RECIPIENTS` | `ceo@company.com,hr@company.com` | 수신자 이메일 (쉼표 구분) |

---

## Step 5. GitHub Pages 활성화

저장소 → **Settings** → **Pages** → Source: `main` / `/ (root)` → **Save**

✅ 저장 후 약 1~2분 뒤 `https://{계정명}.github.io/{저장소명}/` 에서 아카이브 확인

---

## 레터 발행 방법 (반복 사용)

### 노션에서 레터 작성
1. Notion에서 새 페이지 생성
2. 제목(Title) + 본문 작성 (h1/h2/h3, 단락, 불릿, callout, quote 등 모두 지원)
3. Integration 연결 확인 (페이지 공유 → HR Letter Integration 추가)
4. 노션 페이지 URL 복사

### GitHub Actions에서 발행
1. 저장소 → **Actions** 탭
2. **인재경영실 Letter 발송** 워크플로우 선택
3. **Run workflow** 클릭
4. **Notion 페이지 URL** 붙여넣기 (전체 URL 가능)
5. 이메일 발송 여부 선택 (`true` / `false`)
6. **Run workflow** 실행

### 결과 확인
- 수신자들에게 이메일 발송
- GitHub Pages 아카이브 자동 업데이트
- `letters_archive.json` 에 레터 누적 저장

---

## 노션 레터 작성 팁

| 노션 블록 | 이메일/아카이브 결과 |
|-----------|---------------------|
| **제목(Title)** | 이메일 제목 배너에 표시 |
| `H1` (제목 1) | 티얼 언더라인 대제목 |
| `H2` (제목 2) | 볼드 소제목 |
| `H3` (제목 3) | 티얼 컬러 강조 소제목 |
| 단락 | 본문 텍스트 |
| 글머리 기호 목록 | 불릿 리스트 |
| 번호 매기기 목록 | 번호 리스트 |
| **콜아웃** | 티얼 강조 박스 |
| **인용** | 왼쪽 티얼 바 인용구 |
| 구분선 | 섹션 구분선 |
| 이미지 | 인라인 이미지 |
| 코드 | 다크 코드 블록 |

---

## 시스템 아키텍처

```
노션 페이지 작성
       ↓
GitHub Actions (수동 실행)
       ↓
hr_letter.py 실행
  ├─ Notion API → 페이지 제목·블록 읽기
  ├─ HTML 변환 (상상인그룹 브랜딩 적용)
  ├─ Gmail SMTP → 수신자 이메일 발송
  ├─ letters_archive.json 누적 저장 (GitHub API PUT)
  └─ index.html 업데이트 (GitHub Pages 재배포)
```

### 아카이브 JSON 스키마 (`letters_archive.json`)

```json
[
  {
    "id": "uuid",
    "number": 5,
    "date": "2026-04-10",
    "title": "레터 제목",
    "excerpt": "첫 문단 발췌 (최대 160자)",
    "notion_page_id": "원본 노션 페이지 URL",
    "html_content": "레터 본문 HTML"
  }
]
```

최신 레터가 배열 앞에 위치 (내림차순), 날짜 구분 없이 누적 저장.

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| Actions 403 Forbidden | `permissions: contents: write` | yml 파일에 포함되어 있음 ✅ |
| 이메일 미발송 (오류 없음) | `EMAIL_RECIPIENTS` 미설정 | Secrets 확인 |
| SMTP 인증 실패 | 일반 비밀번호 사용 | 앱 비밀번호로 교체 |
| 노션 읽기 실패 401 | 토큰 오류 | `NOTION_TOKEN` Secrets 확인 |
| 노션 읽기 실패 404 | 페이지 공유 안됨 | 노션 페이지 → Connections → Integration 추가 |
| Pages 빈 화면 | JSON 로드 실패 | Pages가 `main/root`로 설정되었는지 확인 |
