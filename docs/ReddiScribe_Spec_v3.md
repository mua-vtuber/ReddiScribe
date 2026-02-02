# ReddiScribe Technical Specification v3.0

> **Status**: Approved for implementation
> **Date**: 2026-02-03
> **Scope**: Full rewrite - clean architecture from scratch

---

## 1. Project Identity

**ReddiScribe** - 비영어권 사용자를 위한 로컬 AI 기반 Reddit 분석 및 작성 보조 도구.

### 1.1 Core Principles

| Principle | Description |
|-----------|-------------|
| **No API Key** | Reddit API 승인 없이 Public JSON Endpoint로 동작 |
| **Local AI** | 모든 AI 연산은 로컬 Ollama에서 수행. 외부 클라우드 전송 없음 |
| **Privacy First** | 수집 데이터는 로컬 SQLite에만 저장 |
| **Never Freeze** | 모든 네트워크/AI 작업은 백그라운드 스레드에서 실행 |

### 1.2 Target Environment

| Component | Spec |
|-----------|------|
| OS | Windows 10/11 |
| GPU | NVIDIA RTX 4080 Super (16GB VRAM) |
| RAM | 96GB |
| Python | 3.11+ |
| AI Runtime | Ollama (local) |

### 1.3 Dependencies

```toml
[project]
dependencies = [
    "PyQt6>=6.6.0",
    "requests>=2.31.0",
    "PyYAML>=6.0.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-qt>=4.3.0",
]
```

> `praw`, `keyring`, `pywin32`는 v3에서 제거. Reddit 접근은 `requests`로 직접 수행. 시크릿 저장 대상 없음.

---

## 2. Architecture

### 2.1 Layer Diagram

```
+------------------------------------------------------------------+
|                         Presentation Layer                        |
|  MainWindow -> WriterWidget / ReaderWidget / SettingsWidget       |
|  (PyQt6 Widgets - UI rendering only, no business logic)          |
+------------------------------------------------------------------+
         |                    |                    |
         v                    v                    v
+------------------------------------------------------------------+
|                         Service Layer                             |
|  WriterService / ReaderService                                   |
|  (Business logic, orchestration, error handling)                 |
+------------------------------------------------------------------+
         |                    |                    |
         v                    v                    v
+------------------------------------------------------------------+
|                         Adapter Layer                             |
|  RedditAdapter (ABC)  ->  PublicJSONAdapter                      |
|  LLMAdapter (ABC)     ->  OllamaAdapter                         |
+------------------------------------------------------------------+
         |                    |                    |
         v                    v                    v
+------------------------------------------------------------------+
|                         Infrastructure                            |
|  ConfigManager / I18nManager / DatabaseManager / Logger          |
+------------------------------------------------------------------+
```

> `DatabaseManager`는 Infrastructure에 직접 위치. SQLite 이외 DB 교체 계획 없으므로 별도 ABC 불필요.
> Settings는 ConfigManager를 직접 사용. 별도 ConfigService 없음 (단순 CRUD에 서비스 레이어 과잉).

### 2.2 Key Architectural Decisions

**Service Layer 도입**: UI와 Adapter 사이에 Service Layer를 둔다.
- View는 Service만 호출. Adapter를 직접 참조하지 않음.
- Service가 어댑터 선택, 에러 변환, 캐시 확인을 담당.
- 어댑터 교체 시 View 코드 변경 불필요.

**Dependency Injection**: 어댑터는 생성자 주입. 직접 인스턴스화 금지.
```python
# Good
class ReaderService:
    def __init__(self, reddit: RedditAdapter, llm: LLMAdapter, db: DatabaseManager):
        ...

# Bad - 직접 결합 금지
class ReaderWidget:
    def __init__(self):
        self.reddit = PublicJSONAdapter()
```

**Error Propagation**: 에러는 예외로 전파. 문자열 yield 금지.
```python
# Good
raise LLMConnectionError("Ollama is not running")

# Bad
yield "[Error: LLM Connection Failed]"
```

**Thread Safety**: 모든 Singleton에 `threading.RLock` 적용. `get()`과 `set()` 모두 락 보호.

**Path Resolution**: 모든 리소스 경로는 프로젝트 루트 기준 절대경로. CWD 의존 금지.
```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
LOCALE_DIR = PROJECT_ROOT / "src" / "resources" / "locales"
DB_DIR = PROJECT_ROOT / "db"   # 없으면 자동 생성
LOG_DIR = PROJECT_ROOT / "logs" # 없으면 자동 생성
```

---

## 3. Data Transfer Objects (DTOs)

서비스-뷰 간 데이터 계약. 모든 DTO는 `dataclass`로 정의.

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class PostDTO:
    id: str                          # Reddit post ID (e.g., "8xwlg")
    title: str
    selftext: str = ""               # 본문 (link post면 빈 문자열)
    author: str = "[deleted]"
    subreddit: str = ""
    score: int = 0                   # 추천 수 (근사값, 퍼징 있음)
    num_comments: int = 0
    url: str = ""
    permalink: str = ""
    created_utc: float = 0.0
    is_self: bool = True

@dataclass
class CommentDTO:
    id: str
    author: str = "[deleted]"
    body: str = ""                   # Raw markdown
    score: int = 0
    created_utc: float = 0.0
    depth: int = 0                   # 중첩 깊이 (0 = 최상위)
    parent_id: str = ""              # 부모 ID (t3_* 또는 t1_*)
    children: list['CommentDTO'] = field(default_factory=list)
    more_count: int = 0              # kind:"more"일 때 접힌 댓글 수

@dataclass
class SummaryDTO:
    post_id: str
    model_type: str                  # 'summary', 'logic', 'persona'
    text: str
    locale: str = "ko_KR"
```

---

## 4. Reddit Data Access

### 4.1 Public JSON Endpoints

Reddit은 모든 공개 페이지 URL 뒤에 `.json`을 붙이면 JSON 응답을 반환한다. API 키 불필요.

**Subreddit Posts:**
```
GET https://www.reddit.com/r/{subreddit}/{sort}.json?limit={n}&raw_json=1
```

| Sort | URL | Extra Params |
|------|-----|-------------|
| Hot | `/r/{sub}/hot.json` | - |
| New | `/r/{sub}/new.json` | - |
| Top | `/r/{sub}/top.json` | `t=hour\|day\|week\|month\|year\|all` |
| Rising | `/r/{sub}/rising.json` | - |

**Post + Comments:**
```
GET https://www.reddit.com/r/{subreddit}/comments/{post_id}/.json?raw_json=1&sort=top&limit=50
```

Response: Array of 2 Listings.
- `[0].data.children[0].data` = Post 본문
- `[1].data.children` = Top-level 댓글 배열

**Query Parameters:**

| Param | Type | Description | Default |
|-------|------|-------------|---------|
| `limit` | int | 결과 수 (1-100) | 25 |
| `after` | string | 페이지네이션 커서 (e.g., `t3_abc123`) | - |
| `t` | string | 기간 필터 (top sort 전용) | - |
| `sort` | string | 댓글 정렬 (best/top/new/controversial) | best |
| `raw_json` | int | 1이면 HTML entity encoding 방지. **항상 포함** | 0 |

### 4.2 Response -> DTO 매핑

**Post JSON -> PostDTO:**
```python
PostDTO(
    id=data["id"],               # "8xwlg"
    title=data["title"],
    selftext=data.get("selftext", ""),
    author=data.get("author", "[deleted]"),
    subreddit=data["subreddit"],
    score=data["score"],         # 퍼징됨 - 근사값
    num_comments=data["num_comments"],
    url=data["url"],
    permalink=data["permalink"],
    created_utc=data["created_utc"],
    is_self=data["is_self"],
)
```

**Comment JSON -> CommentDTO (재귀):**
```python
def parse_comment(item: dict, max_depth: int = 5) -> Optional[CommentDTO]:
    if item["kind"] == "more":
        return CommentDTO(
            id=item["data"]["id"],
            more_count=item["data"]["count"],
            depth=item["data"].get("depth", 0),
        )
    if item["kind"] != "t1":
        return None

    data = item["data"]
    children = []
    replies = data.get("replies")

    # replies가 빈 문자열("")이면 자식 없음. dict면 재귀 파싱.
    if isinstance(replies, dict) and data.get("depth", 0) < max_depth:
        for child in replies["data"]["children"]:
            parsed = parse_comment(child, max_depth)
            if parsed:
                children.append(parsed)

    return CommentDTO(
        id=data["id"],
        author=data.get("author", "[deleted]"),
        body=data.get("body", ""),
        score=data["score"],
        created_utc=data["created_utc"],
        depth=data.get("depth", 0),
        parent_id=data.get("parent_id", ""),
        children=children,
    )
```

> `max_depth=5`는 UI 렌더링 제한이며 Reddit API 파라미터가 아님. Reddit은 전체 트리를 반환하고, 클라이언트가 깊이를 잘라낸다.

### 4.3 Stealth & Rate Limiting

**필수 헤더:**
```yaml
User-Agent: "desktop:kr.reddiscribe:v{version} (by /u/ReddiScribeApp)"
Accept: "application/json"
Accept-Language: "en-US,en;q=0.9"
```

> Platform prefix는 `desktop:` 사용 (데스크톱 앱이므로).

**Rate Limit 규칙:**
- 비인증 접근: **분당 ~10회** (IP 기반, Reddit이 동적 조정하므로 정확한 수치 아님)
- 초과 시 429 Too Many Requests 반환
- Reddit이 봇으로 판단하면 JSON 대신 HTML을 반환할 수 있음

**Rate Limiter 구현 - Minimum Interval 방식:**
```
단순 간격 제한 (Token bucket 불필요 - 버스트 기능이 필요없고 꾸준한 간격이 중요):
- 모든 요청 간 최소 request_interval_sec (기본 6초) 간격 강제
- 마지막 요청 timestamp 저장 -> 다음 요청 시 elapsed 체크 -> 부족하면 대기
- 429 응답 시 exponential backoff: 12s -> 24s -> 48s (max_retries 횟수까지)
- HTML 응답 수신 시 (JSON 파싱 실패): 30초 대기 후 1회 재시도
```

### 4.4 Known Gotchas

| Issue | Handling |
|-------|----------|
| `replies`가 빈 문자열 `""` | `None`이 아님. `isinstance(replies, dict)` 체크 |
| `kind: "more"` 객체 | OAuth 없이 펼치기 불가. "N개 댓글 더보기" 텍스트로 표시 |
| `score` 퍼징 | Reddit이 스팸 방지용으로 정확한 값을 숨김. 근사치로 사용 |
| `raw_json=1` 누락 시 | `&amp;` 등 HTML entity가 섞임. 항상 포함 |
| 삭제된 콘텐츠 | `author: "[deleted]"`, `selftext: "[removed]"`. 필터링 처리 |
| Private 서브레딧 | 403 반환. `SubredditPrivateError` 발생 -> UI에 안내 |
| JSON 대신 HTML 반환 | Reddit이 봇 의심 시 발생. Content-Type 체크 후 대기+재시도 |

---

## 5. LLM Configuration

### 5.1 Model Roles

| Role | Model | num_ctx | Purpose |
|------|-------|---------|---------|
| `logic` | qwen2.5-coder:32b | 32768 | 한->영 초안 번역 (Stage 1) |
| `persona` | llama3.1:70b | 8192 | Reddit 톤 리라이팅 (Stage 2) |
| `summary` | llama3.1:8b | 8192 | 게시글/댓글 요약 |

> summary 모델의 num_ctx는 8192. 3문장 요약에 128K는 불필요하며 VRAM 과다 점유.
> 참고 파일(`docs/settings_사용모델_참고용.yaml`)은 v2 설정이며 v3에서 변경됨.

### 5.2 Ollama API

```
POST http://localhost:11434/api/generate
```

```json
{
  "model": "llama3.1:8b",
  "prompt": "...",
  "stream": true,
  "options": {
    "num_ctx": 8192,
    "temperature": 0.7,
    "num_predict": 4096
  }
}
```

> Config의 `llm.generation.max_tokens`는 Ollama의 `num_predict` 파라미터에 매핑된다.
> Ollama는 `max_tokens`를 사용하지 않으므로 어댑터가 변환 책임.

Streaming response: 각 줄이 JSON 객체. `{"response": "token", "done": false}`

### 5.3 Prompt Templates

**Summary Prompt (Reader):**
```
You are a summarization assistant. Summarize the following Reddit post in {target_language}.

Rules:
- Write exactly 3 concise sentences
- Capture the main argument, key details, and conclusion
- Output ONLY in {target_language}. Do not mix languages.
- Do not add commentary or opinions

Title: {title}
Content: {selftext}
```

**Language Contamination Detection:**
```python
def is_language_contaminated(text: str, expected_locale: str) -> bool:
    """한국어 요약을 요청했는데 영어가 돌아온 경우 감지."""
    if expected_locale != "ko_KR" or len(text) < 20:
        return False
    korean_chars = len(re.findall(r'[가-힣]', text))
    total_alpha = len(re.findall(r'[a-zA-Z가-힣]', text))
    if total_alpha == 0:
        return False
    korean_ratio = korean_chars / total_alpha
    return korean_ratio < 0.3  # 한국어 비율 30% 미만이면 오염
```

재시도 시 프롬프트 강화:
```
IMPORTANT: You MUST respond entirely in Korean (한국어).
Do not write any English words except proper nouns.
(원본 프롬프트 반복)
```

**Drafting Prompt (Writer Stage 1):**
```
Translate the following Korean text to English.

Rules:
- Preserve the logical structure and meaning
- Use natural English grammar, not literal translation
- Keep technical terms accurate
- Do not add explanations or commentary
- Output ONLY the English translation

Korean text:
{input_text}
```

**Polishing Prompt (Writer Stage 2):**
```
Rewrite the following English text to sound natural for a Reddit post.

Rules:
- Use casual, conversational tone appropriate for Reddit
- Add common Reddit expressions where natural (e.g., "IMO", "FWIW")
- Keep the core meaning intact
- Do not over-use slang - keep it readable
- Match the tone to the subreddit context if provided
- Output ONLY the rewritten text

Original English:
{draft_text}
```

### 5.4 Error Handling

| Error | Exception | UI Message (i18n key) |
|-------|-----------|----------------------|
| Connection refused | `OllamaNotRunningError` | `errors.ollama_not_running` |
| Model not found | `ModelNotFoundError` | `errors.model_not_found` |
| Timeout | `LLMTimeoutError` | `errors.llm_timeout` |
| Generation interrupted | N/A (Worker.stop()) | 미완성 결과 DB 저장 안 함 |

> Timeout 기본값: config `llm.providers.ollama.timeout` (기본 120초).
> 70B 모델은 첫 토큰까지 시간이 걸리므로 충분한 여유 필요.

---

## 6. Service Layer API

### 6.1 ReaderService

```python
class ReaderService:
    def __init__(self, reddit: RedditAdapter, llm: LLMAdapter, db: DatabaseManager):
        ...

    def fetch_posts(self, subreddit: str, sort: str = "hot",
                    limit: int = 25) -> list[PostDTO]:
        """서브레딧 게시글 fetch. Rate limit 적용됨.
        Raises: RedditFetchError, RateLimitError, SubredditNotFoundError, SubredditPrivateError
        """

    def fetch_comments(self, post_id: str, subreddit: str,
                       limit: int = 50) -> list[CommentDTO]:
        """게시글 댓글 fetch. 재귀 파싱, max_depth=5.
        Raises: RedditFetchError, RateLimitError
        """

    def get_summary(self, post: PostDTO, locale: str = "ko_KR") -> Optional[str]:
        """DB 캐시 확인. 캐시 히트면 텍스트 반환, 미스면 None."""

    def generate_summary(self, post: PostDTO, locale: str = "ko_KR",
                         stream: bool = True) -> Iterator[str]:
        """LLM으로 요약 생성. 스트리밍 yield.
        완료 후 자동으로 DB 저장 (오염 감지 시 저장 안 함).
        Raises: OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """

    def delete_summary(self, post_id: str, locale: str = "ko_KR") -> None:
        """캐시된 요약 삭제 (Refresh용)."""
```

### 6.2 WriterService

```python
class WriterService:
    def __init__(self, llm: LLMAdapter):
        ...

    def draft(self, korean_text: str, stream: bool = True) -> Iterator[str]:
        """Stage 1: 한국어 -> 영어 초안 (logic model).
        Raises: OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """

    def polish(self, english_draft: str, stream: bool = True) -> Iterator[str]:
        """Stage 2: 영어 초안 -> Reddit 톤 리라이팅 (persona model).
        Raises: OllamaNotRunningError, ModelNotFoundError, LLMTimeoutError
        """
```

---

## 7. Feature Specifications

### 7.1 Writer Tab - 2-Stage Pipeline

```
[Korean Input] -> Stage 1 (Drafting) -> [English Draft] -> Stage 2 (Polishing) -> [Reddit-ready English]
```

**UI Layout:**
```
+---------------------------------------------------------------+
|  ✍️ Writer (i18n: writer.header)                              |
+---------------------------------------------------------------+
|  [Korean Input Area]                                          |
|  placeholder: i18n: writer.placeholder                        |
+---------------------------------------------------------------+
|  [Translate ➡️]  [Draft Only ☑️]           [Stop ■]           |
+---------------------------------------------------------------+
|  Stage 1 Draft (i18n: writer.draft_label)                     |
|  [English draft streams here...]                              |
+---------------------------------------------------------------+
|  Stage 2 Final (i18n: writer.final_label)                     |
|  [Polished Reddit-ready text streams here...]                 |
+---------------------------------------------------------------+
|  [📋 Copy to Clipboard]                                       |
+---------------------------------------------------------------+
```

**UI Flow:**
1. 사용자가 한국어 입력 후 "Translate" 클릭
2. "Translate" 버튼 비활성화, "Stop" 버튼 활성화
3. Stage 1 실행 -> Draft 영역에 실시간 스트리밍
4. Stage 1 완료 -> "Draft Only" 체크 시 여기서 종료
5. 자동으로 Stage 2 시작 -> Final 영역에 스트리밍
6. 완료 -> 버튼 상태 복원, "Copy to Clipboard" 활성화

**Stop 동작:**
- Stage 1 진행 중 Stop -> Stage 1 중단, Stage 2 시작 안 함. 부분 Draft 표시만 유지.
- Stage 2 진행 중 Stop -> Stage 2 중단. 부분 결과 표시 유지. Stage 1 결과는 그대로.

### 7.2 Reader Tab - Smart View

**Layout:**
```
+------------------+---------------------------------------------+
|  📂 Subreddits   |  📜 Posts                    [Sort: ▾ Hot]  |
|  (from config)   |  [Title]  [↑Score]  [💬Comments]            |
|                   +---------------------------------------------+
|  [+ Add]         |  📖 Summary (i18n: reader.summary)          |
|  [- Remove]      |  [AI 요약 또는 "생성 중..." 스피너]           |
|                   |  ─────────────────────────────────          |
|                   |  📄 Original (i18n: reader.original)        |
|                   |  [원문 본문]                                 |
|                   +---------------------------------------------+
|                   |  💬 Comments (i18n: reader.comments)        |
|                   |  [Collapsible comment tree]   [🔄 Refresh]  |
+------------------+---------------------------------------------+
```

**Subreddit List:**
- `config.reddit.subreddits`에서 로드. 하드코딩 금지.
- UI에서 추가(`+`) / 제거(`-`). 변경 시 config에 저장.
- 추가 시 validation: 빈 문자열 거부, 중복 거부. 존재 여부 확인은 실제 fetch 시점에 위임.
- 기본값: `["python", "programming", "learnpython"]`

**Post Fetch (Async):**
- 서브레딧 클릭 -> 기존 목록 즉시 클리어 + 로딩 표시
- `RedditFetchWorker`(QThread)에서 비동기 fetch
- Sort selector: Hot / New / Top / Rising (기본: Hot)
- fetch limit: 25 (config에 추가하지 않음 - 하드코딩 허용. UI 복잡도 대비 이점 없음)
- 실패 시 에러 메시지 UI에 표시 (빈 목록으로 조용히 실패하지 않음)
- v1.0에서 페이지네이션(Load More) 미구현. 첫 25개만 표시.

**Summary:**
- 포스트 클릭 시 DB 캐시 확인 (`ReaderService.get_summary()`)
- 캐시 히트: 즉시 표시
- 캐시 미스: `GenerationWorker`로 요약 생성, 토큰 스트리밍
- 완료 시에만 DB 저장. 중단 시 저장 안 함.
- 오염 감지 -> 프롬프트 강화 후 자동 1회 재시도 -> 재시도도 오염 시 결과 표시 + 경고 배너 (저장은 안 함)
- Refresh 버튼: 캐시 삭제 후 재생성

**Comment Tree:**
- 포스트 클릭 시 별도 요청으로 댓글 fetch (`ReaderService.fetch_comments()`)
- 댓글은 DB에 저장하지 않음 (매번 fresh fetch, 캐싱 가치 낮음)
- 계층형 들여쓰기로 렌더링 (depth에 따라 왼쪽 패딩)
- `replies` 재귀 파싱, 렌더링 max_depth=5 (API가 아닌 클라이언트 측 제한)
- `kind: "more"` -> "N개 댓글 더보기" 비활성 텍스트 표시
- 각 댓글에 author, score, body 표시

### 7.3 Settings Tab

**모든 라벨은 i18n 키로 관리. 하드코딩 금지.**

| Setting Group | Items |
|---------------|-------|
| Application | Locale (ko_KR / en_US), Theme (dark / light) |
| LLM | Logic model name, Persona model name, Summary model name, Ollama host, Timeout |
| Reddit | 요청 간격(초), Mock mode 토글 |
| Advanced | Log level |

> Subreddit 목록은 Reader 탭에서 직접 관리 (Settings에서 분리).

**Save 동작:**
1. "Save" 클릭 시 SettingsWidget이 변경된 값을 dict로 수집
2. `ConfigManager.update(changes: dict)` 호출 -> 메모리 반영 + 1회 디스크 기록
3. Locale 변경 감지 시 `I18nManager.load_locale()` + `MainWindow.retranslate_ui()` 호출

**Config Validation (ConfigManager 내부):**

| Field | Validation |
|-------|-----------|
| `app.locale` | `ko_KR` 또는 `en_US`. 나머지 무시 (기존값 유지) |
| `reddit.request_interval_sec` | int, 최소 3. 미만이면 3으로 강제 |
| `llm.generation.temperature` | float, 0.0~2.0. 범위 밖이면 0.7 |
| `llm.providers.ollama.timeout` | int, 최소 30. 미만이면 30으로 강제 |
| YAML 파싱 실패 | 에러 로그 + 빈 config로 fallback (기본값 사용) |

---

## 8. Data Model

### 8.1 Database Schema

**Table: `posts`**
```sql
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    subreddit     TEXT NOT NULL,
    title         TEXT NOT NULL,
    selftext      TEXT DEFAULT '',
    author        TEXT DEFAULT '[deleted]',
    url           TEXT,
    permalink     TEXT,
    score         INTEGER DEFAULT 0,
    num_comments  INTEGER DEFAULT 0,
    created_utc   REAL,
    fetched_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Table: `summaries`**
```sql
CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT NOT NULL,
    model_type  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    locale      TEXT NOT NULL DEFAULT 'ko_KR',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id),
    UNIQUE(post_id, model_type, locale)
);
```

> 댓글은 DB에 저장하지 않음. 매번 fresh fetch.

### 8.2 Data Integrity Rules

| Rule | Implementation |
|------|---------------|
| 중복 수집 방지 | Post ID를 PK로 사용. `INSERT OR IGNORE` |
| 요약 중복 방지 | `(post_id, model_type, locale)` UNIQUE. `ON CONFLICT DO UPDATE` |
| Atomic Save | AI 생성이 100% 완료된 후에만 DB에 커밋. 중단 시 저장 안 함 |
| 캐시 우선 | 이미 요약된 포스트는 DB 캐시 사용. 사용자 수동 갱신만 재생성 |

---

## 9. Project Structure

```
ReddiScribe/
  pyproject.toml                     # Dependencies, metadata, build config
  config/
    settings.yaml                    # Runtime config (created on first run if missing)
  src/
    __init__.py
    main.py                          # Entry point, DI wiring, app startup sequence
    core/
      __init__.py
      config_manager.py              # YAML config, thread-safe singleton
      i18n_manager.py                # Locale JSON loader, thread-safe singleton
      database.py                    # SQLite manager, thread-safe singleton
      logger.py                      # Rotating file + console, sensitive data masking
      exceptions.py                  # Custom exception hierarchy
      types.py                       # DTO dataclasses (PostDTO, CommentDTO, SummaryDTO)
    adapters/
      __init__.py
      reddit_adapter.py              # ABC: get_subreddit_posts, get_post_comments
      public_json_adapter.py         # requests + stealth headers + rate limiter
      llm_adapter.py                 # ABC: generate (streaming iterator)
      ollama_adapter.py              # Ollama REST API, exception-based errors
    services/
      __init__.py
      reader_service.py              # Reddit fetch + cache + summarize orchestration
      writer_service.py              # 2-stage translation pipeline orchestration
    gui/
      __init__.py
      main_window.py                 # Shell: sidebar + stacked views
      workers.py                     # QThread workers (RedditFetchWorker, GenerationWorker)
      widgets/
        __init__.py
        reader_widget.py             # Reader UI only (delegates to ReaderService via workers)
        writer_widget.py             # Writer UI only (delegates to WriterService via workers)
        settings_widget.py           # Settings UI with full i18n, batch save
    resources/
      locales/
        ko_KR.json
        en_US.json
  tests/
    __init__.py
    conftest.py                      # Shared fixtures (mock adapters, temp config, temp db)
    test_config_manager.py
    test_i18n_manager.py
    test_database.py
    test_public_json_adapter.py      # Mock HTTP responses
    test_ollama_adapter.py           # Mock HTTP responses
    test_reader_service.py
    test_writer_service.py
  docs/
    settings_사용모델_참고용.yaml      # v2 config reference (outdated, do not use for implementation)
```

> `db/`와 `logs/` 디렉터리는 런타임에 자동 생성. 프로젝트 구조에 포함하지 않음.

---

## 10. Configuration Schema

```yaml
app:
  locale: ko_KR                # ko_KR | en_US
  theme: dark                  # dark | light
  version: 1.0.0
  log_level: INFO

llm:
  default_provider: ollama
  providers:
    ollama:
      host: http://localhost:11434
      timeout: 120             # seconds. 70B 모델 첫 토큰 대기 고려
  models:
    logic:
      name: qwen2.5-coder:32b
      num_ctx: 32768
    persona:
      name: llama3.1:70b
      num_ctx: 8192
    summary:
      name: llama3.1:8b
      num_ctx: 8192
  generation:
    temperature: 0.7
    max_tokens: 4096           # -> Ollama num_predict에 매핑

reddit:
  subreddits:
    - python
    - programming
    - learnpython
  request_interval_sec: 6      # Minimum seconds between requests
  max_retries: 3               # 429 retry count before giving up
  mock_mode: false             # true -> 네트워크 없이 가짜 데이터 반환

data:
  db_path: db/history.db       # PROJECT_ROOT 기준 상대경로로 해석됨

security:
  mask_logs: true              # 로그에서 URL, token 등 마스킹
```

> `settings.yaml`이 없으면 위 기본값으로 자동 생성.
> `db_path`는 상대경로로 작성하되, `ConfigManager`가 `PROJECT_ROOT` 기준으로 절대경로 해석.

---

## 11. Error Handling Strategy

### 11.1 Custom Exception Hierarchy

```
ReddiScribeError (base)
  +-- NetworkError
  |     +-- RedditFetchError
  |     +-- RateLimitError (429)
  |     +-- SubredditNotFoundError (404)
  |     +-- SubredditPrivateError (403)
  +-- LLMError
  |     +-- OllamaNotRunningError
  |     +-- ModelNotFoundError
  |     +-- LLMTimeoutError
  +-- DataError
        +-- DatabaseError
        +-- ConfigError
```

### 11.2 Error Flow

```
Adapter: raise specific exception
    -> Service: catch, log, re-raise
        -> Worker: catch, emit error_occurred signal with user-friendly i18n key
            -> Widget: display localized error message in UI
```

**절대 하지 않을 것:**
- 에러를 문자열로 yield
- 빈 리스트 반환으로 조용히 실패
- 로그에만 기록하고 UI에 알리지 않음

---

## 12. Threading Model

| Operation | Thread | Mechanism |
|-----------|--------|-----------|
| UI 렌더링 | Main thread | PyQt6 event loop |
| Reddit fetch | Background | `RedditFetchWorker(QThread)` |
| LLM generation | Background | `GenerationWorker(QThread)` |
| DB read/write | Main thread | Signal 슬롯에서 실행 |
| Config save | Main thread | 동기 실행 (빈번하지 않음) |

**DB 접근 규칙: Worker는 절대 DB에 직접 접근하지 않는다.**
모든 DB 연산은 Worker의 signal을 받는 메인 스레드 슬롯에서 수행.
SQLite는 single-thread 모드로 사용.

**Worker Signal Contracts:**

```python
class RedditFetchWorker(QThread):
    posts_ready = pyqtSignal(list)       # list[PostDTO]
    comments_ready = pyqtSignal(list)    # list[CommentDTO]
    error_occurred = pyqtSignal(str)     # i18n error key
    progress = pyqtSignal(str)           # status message

class GenerationWorker(QThread):
    token_received = pyqtSignal(str)     # 개별 토큰
    finished_signal = pyqtSignal(str)    # 완성된 전체 텍스트
    error_occurred = pyqtSignal(str)     # i18n error key
```

---

## 13. Application Startup Sequence

```python
def main():
    # 1. Resolve PROJECT_ROOT
    # 2. ConfigManager 초기화 (settings.yaml 로드, 없으면 기본값 생성)
    # 3. Logger 초기화 (config에서 log_level 읽기)
    # 4. I18nManager 초기화 (config에서 locale 읽기)
    # 5. DatabaseManager 초기화 (테이블 생성/마이그레이션)
    # 6. Adapter 생성 (PublicJSONAdapter, OllamaAdapter)
    # 7. Service 생성 (ReaderService, WriterService) <- 어댑터 주입
    # 8. QApplication 생성
    # 9. MainWindow 생성 <- 서비스 주입
    # 10. window.show()
    # 11. Event loop 진입
```

> Ollama 연결 확인은 startup에서 하지 않음. 실제 LLM 호출 시점에 에러 처리.

---

## 14. Mock Mode

개발/테스트용. `config.reddit.mock_mode: true` 시 활성화.

**PublicJSONAdapter mock 동작:**
- `get_subreddit_posts()` -> 고정된 가짜 PostDTO 5개 반환 (인라인 생성, fixture 파일 불필요)
- `get_post_comments()` -> 가짜 CommentDTO 3개 (depth 0-1) 반환
- 네트워크 요청 없음, rate limiter 무시

**OllamaAdapter mock은 없음.** Ollama가 실제로 돌아야 LLM 기능 테스트 가능.
테스트에서는 `unittest.mock`으로 어댑터 자체를 mock.

---

## 15. I18n Key Structure

```json
{
  "app": { "title": "..." },
  "nav": { "write": "...", "read": "...", "settings": "..." },
  "status": { "initializing": "...", "settings_saved": "...", "language_changed": "..." },
  "reader": {
    "subreddits": "...", "posts": "...", "summary": "...",
    "original": "...", "comments": "...", "generating": "...",
    "refresh": "...", "add_sub": "...", "remove_sub": "...",
    "no_posts": "...", "loading": "..."
  },
  "writer": {
    "header": "...", "placeholder": "...", "translate_btn": "...",
    "draft_label": "...", "final_label": "...", "draft_only": "...",
    "copy_btn": "...", "stop_btn": "...", "copied": "..."
  },
  "settings": {
    "header": "...", "app_group": "...", "lang_label": "...",
    "theme_label": "...", "llm_group": "...", "logic_label": "...",
    "persona_label": "...", "summary_label": "...", "host_label": "...",
    "timeout_label": "...", "reddit_group": "...", "interval_label": "...",
    "mock_label": "...", "save_btn": "...", "advanced_group": "...",
    "log_level_label": "..."
  },
  "errors": {
    "ollama_not_running": "...", "model_not_found": "...",
    "llm_timeout": "...", "reddit_fetch_failed": "...",
    "rate_limited": "...", "subreddit_not_found": "...",
    "subreddit_private": "...", "language_contaminated": "..."
  }
}
```

---

## 16. Implementation Phases

### Phase 1: Foundation + Tests
- [ ] pyproject.toml, 디렉터리 구조 생성
- [ ] `core/exceptions.py` - Custom exception hierarchy
- [ ] `core/types.py` - DTO dataclasses
- [ ] `core/config_manager.py` - Thread-safe, 절대경로, validation, `update()` 메서드
- [ ] `core/i18n_manager.py` - Thread-safe, 절대경로
- [ ] `core/logger.py` - Sensitive data masking
- [ ] `core/database.py` - Thread-safe singleton, schema 초기화
- [ ] Locale JSON 파일 (ko_KR, en_US) - 전체 키 구조
- [ ] **Tests**: test_config_manager, test_i18n_manager, test_database

### Phase 2: Adapters + Tests
- [ ] `adapters/reddit_adapter.py` - ABC
- [ ] `adapters/public_json_adapter.py` - Stealth headers, rate limiter, mock mode
- [ ] `adapters/llm_adapter.py` - ABC
- [ ] `adapters/ollama_adapter.py` - Exception-based errors, streaming
- [ ] **Tests**: test_public_json_adapter (mock HTTP), test_ollama_adapter (mock HTTP)

### Phase 3: Services + Workers + Tests
- [ ] `services/reader_service.py` - fetch + cache + summarize + 오염 감지
- [ ] `services/writer_service.py` - 2-stage pipeline
- [ ] `gui/workers.py` - RedditFetchWorker, GenerationWorker
- [ ] **Tests**: test_reader_service, test_writer_service (mock adapters)

### Phase 4: GUI
- [ ] `gui/main_window.py` - Sidebar, stacked views, retranslate_ui, DI wiring
- [ ] `gui/widgets/reader_widget.py` - Async fetch, summary streaming, comment tree
- [ ] `gui/widgets/writer_widget.py` - 2-stage UI, copy to clipboard
- [ ] `gui/widgets/settings_widget.py` - Full i18n, batch save, validation feedback
- [ ] `main.py` - Startup sequence, DI assembly

### Phase 5: Integration QA
- [ ] 전체 플로우 수동 테스트 (subreddit -> post -> summary -> cache)
- [ ] Writer 2-stage 파이프라인 동작 확인
- [ ] 언어 오염 감지 + 재시도 검증
- [ ] Rate limiter 429 대응 검증
- [ ] Locale 전환 시 전체 UI 업데이트 확인
- [ ] UI 프리징 없음 확인 (모든 네트워크/LLM 작업)
- [ ] settings.yaml 없는 상태에서 첫 실행 확인
- [ ] Mock mode 동작 확인

---

## 17. Out of Scope (v1.0에서 하지 않는 것)

| Feature | Reason |
|---------|--------|
| Reddit 게시/댓글 작성 | API 키 필요. Clipboard 복사로 대체 |
| OAuth 인증 | API 승인 거절됨. Public JSON으로 대체 |
| 사용자 계정 관리 | 필요 없음 |
| 실시간 알림 | 과도한 요청 유발 가능 |
| `more` 댓글 펼치기 | OAuth 필요. 초기 로드된 댓글만 표시 |
| 다국어 3개 이상 | ko_KR, en_US만 지원 |
| Post 페이지네이션 | v1.0에서는 첫 25개만. v2.0에서 "Load More" 추가 |
| 윈도우 상태 저장 | v1.0에서는 미구현. 항상 기본 크기로 시작 |
| 테마 시스템 구현 | config에 필드는 있으나 v1.0에서 dark 고정. v2.0에서 구현 |
