# CLAUDE.md — 작업자 가이드

이 문서는 Claude(또는 다른 작업자)가 이 코드베이스를 빠르게 파악하기 위한 메타 가이드다.
엔드포인트 명세는 [API.md](API.md), 사용법은 [README.md](README.md) 참조.

---

## 1. 프로젝트 한 줄

**클래식 음악과 백색 소음이 함께하는 뽀모도로 타이머 웹앱.**
FastAPI 백엔드 + 바닐라 JS 프론트엔드(빌드 없음, npm 없음). 기본 `127.0.0.1:8025`.
음원은 퍼블릭 도메인/CC 라이선스 곡을 1회 내려받아 로컬에서 서빙한다.

진입점은 `launcher.py` 하나 — venv 자동 생성 → 의존성 설치 → FastAPI 실행 → 브라우저 오픈.

---

## 2. 디렉토리 구조

```
pomodoro/
├── launcher.py                 # 단일 진입점 (루트의 유일한 .py)
├── media_catalog.json          # 트랙 매니페스트 (커밋됨, qa/build_catalog.py 가 생성)
├── server/
│   ├── app.py                  # create_app() — 라우터 4 + StaticFiles 2 + 미들웨어 2
│   ├── config.py               # 경로/포트/상한 상수 + ensure_dirs()
│   ├── bootstrap.py            # 의존성 설치 + 디렉터리 + 시드 (★ 네트워크 없음)
│   ├── storage.py              # atomic_write · read_json · _LOCK
│   ├── settings.py             # 설정 모델 + 로드/저장/마이그레이션
│   ├── stats.py                # 세션 로그 + 오늘/N일/스트릭 집계
│   ├── catalog.py              # media_catalog.json 로드 + 검증
│   ├── media.py                # 다운로드 잡(스레드) + 진행률 + ready 스캔
│   ├── playlists.py            # 재생목록 + 트랙 레지스트리 CRUD
│   ├── tasks.py                # 오늘 할 일 (done 개수는 저장하지 않는다)
│   ├── terms.py                # 제목 정리·한국어화·라이선스 판정 (생성기와 공유)
│   ├── search.py               # archive.org 런타임 검색
│   ├── input_limits.py         # 업로드 크기·확장자·매직바이트·용량예산
│   └── routes/{settings,stats,media,playlists,tasks,search}.py
├── ui/
│   ├── index.html · style.css · boot.js · main.js
│   ├── package.json            # {"type":"module"} — node --check 용 (npm 의존성 아님)
│   └── modules/
│       ├── state.js            # 전역 상태 + 이벤트 버스 + localStorage 헬퍼
│       ├── utils.js            # DOM 헬퍼 · 포맷터 · 토스트 · 모달 · 테마
│       ├── api.js              # REST 클라이언트
│       ├── timer.js            # ★ 타이머 엔진 (DOM/오디오를 모른다)
│       ├── audio.js            # 음악 재생 그래프 · 크로스페이드 · 더킹 · 소음 버스
│       ├── chime.js            # 알림음 합성 (파일 없음)
│       ├── noise.js            # 백색 소음/주변음 합성 (파일 없음)
│       ├── ui.js               # 렌더링 · 집중 모드 · 복구 모달 · 접근성
│       ├── stats.js            # 기록 화면 + SVG 차트 + 오프라인 큐
│       ├── settings.js         # 설정 화면 + 세트 편집기
│       ├── playlist.js         # 음악 화면 · 업로드 · 폴더 가져오기 · 크레딧
│       ├── tasks.js            # 할 일 목록 · 활성 작업
│       ├── search.js           # 음원 검색 (앨범 → 곡 선택)
│       ├── notify.js           # 탭 제목 · 파비콘 · 알림 · Wake Lock
│       └── shortcuts.js        # 전역 단축키
├── qa/
│   ├── build_catalog.py        # 카탈로그 생성기 (유지보수 전용)
│   ├── test_terms.py · test_stats.py · test_settings.py
│   ├── test_tasks.py · test_search.py
├── data/                       # gitignore — settings/sessions/playlists/tracks.json
└── media/                      # gitignore — catalog/ · user/
```

---

## 3. 꼭 지켜야 할 관습

### 3.1 경로는 **호출 시점**에 config 에서 읽는다

```python
def _sessions_path() -> Path:
    return config.DATA_DIR / "sessions.json"    # O

SESSIONS_FILE = config.DATA_DIR / "sessions.json"   # X — 테스트 격리 불가
```

테스트가 `config.DATA_DIR` 를 임시 폴더로 monkeypatch 해서 격리하는 유일한 조건이다.
`config.catalog_media_dir()` / `user_media_dir()` 도 같은 이유로 상수가 아니라 함수다.

### 3.2 원자적 JSON 쓰기

모든 사용자 데이터는 `server/storage.py` 를 통해서만 디스크에 닿는다.
tmp 파일에 쓴 뒤 `os.replace` — 쓰는 도중 죽어도 반쪽짜리 JSON 이 남지 않는다.

### 3.3 시간 필드는 **초 단위**

`focus_seconds`, `break_seconds`. 분은 UI 표시 단위일 뿐이다.
덕분에 테스트가 `PUT /api/settings {"timer":{"sets":[{"focus_seconds":3,...}]}}` 만으로
3초짜리 전체 사이클을 돌릴 수 있어 **테스트 전용 코드 경로가 0개**다.
API 하한이 1초인 것도 이 때문이며, 사람이 쓰는 최소값(1분)은 UI input 이 강제한다.

### 3.4 와이어는 전부 `snake_case`

JS 에서도 API 페이로드는 snake_case 를 그대로 쓴다. 경계에서 이름을 바꾸지 않으므로
변환 버그가 생길 자리가 없다.

### 3.5 ★ 타임존 — `toLocalISO()` 를 쓴다

`Date#toISOString()` 은 항상 UTC(`Z`)를 낸다. 서버는 받은 타임스탬프의 오프셋으로
"그 세션이 어느 날짜에 속하는지"를 파생하므로, UTC 를 보내면 **자정 근처 세션이 하루
어긋난다** (KST 8/23 00:06 은 UTC 로 8/22).
→ `ui/modules/utils.js` 의 `toLocalISO()` 가 로컬 오프셋(`+09:00`)을 붙인다.
→ 서버는 `AwareDatetime` 으로 naive 값을 **422 로 거부**한다.

### 3.6 프론트 상태 객체를 **캡처하지 말고 인덱스로 다시 찾는다**

설정을 저장하면 서버 응답으로 `state.settings` 가 통째로 교체된다. 렌더 시점에 잡아 둔
객체 참조는 곧 버려진 사본이 되고, 거기에 값을 쓰면 조용히 사라진다.
(세트 편집기에서 "집중은 바뀌는데 휴식은 안 바뀌던" 버그의 원인.)
→ `settings.js` 의 `at(i) => state.settings.timer.sets[i]` 패턴 참조.

### 3.7 이벤트 버스로만 모듈 간 통신

`timer.js` 는 DOM 도 오디오도 모른다. `state.js` 의 `on()/emit()` 만 쓴다.
덕분에 순환 import 가 0개이고 타이머 엔진을 단독으로 검증할 수 있다.

★ `main.js` 에서 **`wireEvents()` 가 `initTimer()` 보다 먼저** 와야 한다.
`initTimer()` 는 복원 즉시 `timer:phase-start` / `timer:tick` 을 쏘는데, 그때 구독자가
없으면 첫 화면이 그려지지 않는다.

---

## 4. 핵심 설계

### 4.1 사이클 세트

한 세트 = **집중 한 번 + 뒤따르는 휴식 한 번**. 기본은 1세트(25분/5분)이고 사용자가
자유롭게 추가·복제·삭제·순서변경한다. `repeat` 가 켜져 있으면 마지막 세트 뒤 첫 세트로.

예전의 (집중/짧은휴식/**긴 휴식** + 반복 횟수) 모델은 제거했다 — "3번째 세트만 길게
쉬고 싶다" 같은 요구를 표현할 수 없었기 때문이다. 세트 목록이면 그대로 적으면 된다.
`settings._migrate()` 가 옛 설정을 동등한 세트 목록으로 변환한다.

### 4.2 타이머는 데드라인 기반

`setInterval` 로 남은 시간을 *차감*하지 않는다 — 브라우저는 백그라운드 탭의 타이머를
1초 이상(종종 1분)으로 스로틀하고 가려진 탭은 아예 얼린다.
**절대 시각 `endsAt` 하나만 진실원**으로 두고 매 틱마다 `endsAt - Date.now()` 를 재계산한다.

절전 복구: 갭이 `GRACE_MS`(120초) 이하면 자동 진행, 넘으면 복구 모달을 띄워
`완료로 기록 / 기록하지 않고 새로 시작 / 이어서 계속` 을 묻는다.
기록할 때 `ended_at` 은 **`endsAt` 이지 `Date.now()` 가 아니다.**

### 4.3 오디오 그래프

```
deck0 ─┐
       ├─ musicBus ─┐
deck1 ─┘            │
noise ── noiseBus ──┼─ master → destination
chime ──────────────┘
```

- **소음은 음악과 독립 레이어** — 음악 없이 소음만, 또는 클래식 위에 얹어 쓸 수 있다
- 차임·소음 모두 **합성**한다 (파일 0바이트, 404 불가, 라이선스 0)
- 차임은 비조화 배음(관종 모드비 1 : 2.76 : 5.40 : 8.93)을 쓰고 **정규화로 클리핑을 막는다**
  — `HEADROOM` 값은 `renderChimeOffline()` 로 peak < 1.0 을 확인하며 정한 것이다
- 소음 버퍼는 **루프 이음매를 등파워 크로스페이드로 제거**한다 (브라운 노이즈는 저역이
  강해 이음매가 "툭" 하는 클릭으로 들린다)

★ **오토플레이 언락**: 시작 버튼 핸들러가 제스처를 필요로 하는 모든 것(AudioContext
생성·`resume()`·알림 권한)을 소비하는 유일한 지점이다.

★ **`play()` 실패를 전부 "차단"으로 처리하면 안 된다.** `AbortError`(크로스페이드로
소스 교체), 디코드 실패, 404 모두 reject 한다. `NotAllowedError` 이면서 AudioContext 가
running 이 아닐 때만 자동재생 차단이다. (소리가 나는데 차단 배너가 뜨던 버그의 원인.)

### 4.4 기록

롤업을 저장하지 않고 append-only 원시 로그에서 매번 계산한다. 롤업은 드리프트할 수 있는
캐시이고, 여기서의 드리프트는 곧 사용자가 가장 신경 쓰는 숫자(연속 달성 일수)가 조용히
틀리는 것이다. 5만 건 상한에서 전체 스캔은 10~30ms.

- **뽀모도로 개수** = `phase=="focus" and completed`. 중도 포기는 세지 않고 `aborted_*` 로 별도 보고
- **연속 일수** — 오늘 기록이 없어도 어제까지 이어졌으면 유지하고 `includes_today` 를 함께 반환
  (오전 9시에 12일 연속이 0 으로 보이면 틀렸고 사기를 꺾는다)
- `day_start_hour` 변경 시 `PUT /api/settings` 안에서 **동기 1회 재계산** (소급 적용)
- `client_id` 가 클라이언트 생성 멱등키 — 오프라인 큐가 재전송해도 중복되지 않는다

### 4.5 음원 카탈로그는 **생성**한다

`qa/build_catalog.py` 가 archive.org metadata API 와 Wikimedia Commons API 를 실제로
조회해 `media_catalog.json` 을 만든다. 손으로 쓰지 않는 이유:

- 그럴듯해 보이는 archive.org 식별자 6개 중 **3개가 사용 불가**였다
  (MP3 0개 / 4.5GB 단일 ZIP / 마이크 원본 스템)
- archive.org 의 라이선스 메타데이터는 사용자 입력이라 누락이 흔하다 —
  Musopen 컬렉션 34개 중 **28개가 `licenseurl` 자체가 없었다**
  → 라이선스가 확인되지 않은 항목은 **거부**한다

생성기에서 실제로 물렸던 함정 3가지:
1. Commons 의 상위 카테고리(`Category:United States Air Force Band`)에는 **사진만** 있다.
   오디오는 `Category:Audio files of music by ...` 에 있다.
2. Wikimedia 는 `imageinfo` 의 `url` 뒤에 **UTM 추적 파라미터**를 붙인다
   (`....mp3?utm_source=...`). 쿼리를 떼지 않으면 확장자 검사가 전부 실패한다.
3. 파일명이 길어 GET URL 에 50개를 담으면 길이 제한에 걸리고, MediaWiki 는 그걸
   **HTTP 200 + error 객체**로 돌려주므로 조용히 0건이 된다. → 배치 20개 + error 검사.

**Wikimedia 는 파일 다운로드도 rate-limit 한다** (실제로 32곡이 429 로 실패했다).
`server/media.py` 의 `_HOST_MIN_INTERVAL` 이 호스트별 간격을 두고 429 는 백오프 재시도한다.

### 4.6 작업(할 일) — done 개수를 저장하지 않는다

```
done_pomodoros = |{s ∈ sessions : phase=="focus" ∧ completed ∧ task_id == t.id}|
```

§4.4 와 같은 이유다. 저장하면 세션 삭제·기록 초기화·오프라인 큐 재전송에서 조용히
드리프트한다. 파생시키면 증가 엔드포인트가 아예 필요 없다 — `POST /api/stats/sessions` 에
`task_id` 를 담는 것이 곧 증가이고, `client_id` upsert 가 이미 중복을 막는다.

(프론트는 이 값을 더 이상 화면에 노출하지 않는다 — 체크박스와 혼동돼 "체크해도 안
올라간다" 는 오해를 샀다. `est_pomodoros`/`done_pomodoros` 자체는 API 응답에 남아 있다.)

**아카이브를 만들지 않는다.** "To Do Today" 만 만든다 — 전체 활동 목록은 태스크 매니저의
일이다. 미완료는 자동 이월되고 완료 항목은 다음 날 목록에서 빠진다(파일에는 14일 남는다).

★ `reorder_tasks` 는 `playlists.update_playlist` 와 **의도적으로 다르다.** 재생목록은
요청에 없는 트랙을 배정 해제하지만, 작업은 **빠진 id 를 살려 둔다.** 다른 탭에서 방금
추가한 작업을 낡은 클라이언트가 지우면 그건 데이터 손실이다.

★ `timer.js` 는 작업을 모른다. 이음매는 `main.js` 의 `withActiveTask(d.session)` 하나뿐이다.
★ 프론트 핸들러는 작업 객체도 **인덱스도** 붙잡지 않는다 — 목록은 서버 응답으로 교체되고
  인덱스는 재정렬로 바뀐다. `id` 만 붙잡고 이벤트 시점에 다시 찾는다.
★ **작업 입력은 절대 시작의 전제 조건이 아니다.** 작업 0개로 시작 버튼이 즉시 동작해야
  한다 — 이게 이 기능의 성패를 가르는 단 하나의 회귀 검사다.

### 4.7 흐름 보호

이탈 원인 1위가 "알림이 몰입을 끊는 것" 이라 아래를 넣었다.

- **+5분 연장** (`E`) — 데드라인 기반이라 `endsAt += 300_000` 이면 끝. `plannedMs` 도
  함께 늘려 기록이 "25분 계획했는데 30분 했다" 가 아니라 정직하게 남는다.
- **종료 30초 전 예고** — 토스트가 아니라 링 색만 바꾼다 (`body[data-ending-soon]`).
- **휴식 시작 유예** — 자동 시작이 켜져 있어도 `timer:hold` 를 쏘고 UI 가 20초 카운트다운을
  띄운다. 0:00 에 휴식이 자동 시작되면 그 앞부분을 하던 일 마무리에 쓰게 되어
  5분 휴식이 실제로는 2분이 된다("휴식 침식").
- **차임 램프** — 계단식이 아니라 음악 아래에서 시작해 커진다. 휴식이 끝날 때는 더 또렷하게
  (한 번 더 울린다) — 돌아오는 게 멈추는 것보다 어렵다.

### 4.8 음원 검색 (archive.org)

**저장 위치가 설계의 핵심이다.** 검색으로 추가한 곡은 `data/tracks.json`(`origin: "search"`)
＋ `media/user/` 로 간다. `media_catalog.json` 은 **생성물**이라 거기 append 하면
`qa/build_catalog.py` 재실행이 사용자 추가분을 통째로 날린다.

★ `POST /api/media/search/add` 는 클라이언트의 `url`/`bytes`/`sha1` 을 **절대 받지 않는다.**
`identifier` + `names` 만 받고 메타데이터를 다시 가져와 서버가 파생한다. ① 검색 시점
라이선스는 신뢰 불가(100건 중 5건만 존재) ② 그 사이 항목이 다크 처리될 수 있음
③ `names` 가 방금 받은 파일 목록에 있을 때만 통과 → 임의 URL 주입 불가.

실측으로 확인한 함정 — `qa/test_search.py` 가 전부 회귀 검사로 고정한다:

| # | 함정 |
|---|---|
| 1 | `licenseurl:(publicdomain)` 은 **0건**. 와일드카드 필수 |
| 2 | 슬래시 미이스케이프 시 쿼리가 깨지고 응답에 **`response` 키가 없다** (200인데) |
| 3 | LibriVox 오디오북이 작곡가 쿼리를 잠식 → `-collection:librivoxaudio` 필수 |
| 4 | `stream_only`/`samples_only`/`loggedin` 은 메타데이터에 MP3 가 있어도 다운로드 404. 단서는 `MP3 Sample` 포맷 |
| 5 | `/metadata/<id>` 가 다크 항목에 **HTTP 200 + `{}`** |
| 6 | 다운로드는 `files[].name` 정확히 — 표시 제목이면 404 |
| 7 | **파일 단위** 필터. 4GB/744파일 앨범에 쓸 만한 MP3 가 104개 |
| 8 | VBR 파생본의 `length` 가 틀릴 수 있다 (4.4MB에 18:10) → 형제 중앙값·비트레이트로 교차검증 |

**한국어 검색은 동작하지 않는다** — `클래식`은 모스부호 파일, `피아노`는 K-pop 커버가 나온다.
`terms.ko_lookup()` 으로 영어로 바꿔 검색하고 **무엇으로 바꿨는지 알린다**.
사전은 `KO_TERMS` 에서 자동 반전하므로 손으로 두 번 쓰지 않는다.

### 4.9 UI 구성 원칙 — 컨트롤을 한 곳에만 둔다

설정이 30개 컨트롤까지 늘어난 적이 있어 아래 규칙으로 정리했다.

1. **메인 화면에 있는 것은 설정에 두지 않는다.** 음악 음량·소음 켜기/종류/음량·재생 순서는
   타이머 화면 전용이다. 두 곳에 두면 동기화 코드가 생기고 어느 쪽이 진실인지 헷갈린다.
2. **불리언 두 개보다 선택지 하나.** `auto_start_break`/`auto_start_focus` 체크박스 두 개는
   "휴식만 자동 / 모두 자동 / 직접 시작" 3지선다 하나로 묶었다 (`autoStartMode()` 가 변환).
   `chime_enabled` 도 종류 목록의 "사용 안 함" 항목으로 흡수했다.
3. **거의 안 바꾸는 것은 `<details class="advanced">` 로 접는다.** 지우지 않고 접는다 —
   기능은 남기되 첫 화면을 단순하게 유지한다.
4. **상단바는 탭이 전부다.** 브랜드 텍스트는 `visually-hidden`(스크린리더용으로만 남김),
   도움말 버튼은 고급 설정으로 옮겼다.

★ `.advanced:not([open]) > *:not(summary) { display: none }` 규칙이 반드시 필요하다.
`.fields`/`.row-actions` 에 준 `display: flex` 가 브라우저의 기본 접힘 처리를 이겨서,
이 규칙이 없으면 접어도 내용이 그대로 보인다.

### 4.10 보안

- 기본 바인딩 **`127.0.0.1`** — 이 백엔드는 파일 업로드·서버측 폴더 브라우저·임의 경로
  복사 엔드포인트를 갖는다. 인증 없이 LAN 에 여는 건 나쁜 거래다
- `CORSMiddleware(allow_origins=["*"])` **대신** 순수 ASGI `OriginGuardMiddleware` —
  브라우저는 multipart POST 에 preflight 를 보내지 않으므로 CORS 헤더만으로는 쓰기를 못 막는다
- 업로드 저장 파일명은 **사용자 입력에서 파생하지 않는다** (`u-<uuid12>.<ext>`)
- 심링크 검사는 반드시 `resolve()` **전에** 한다

---

## 5. 검증

```bash
# 파이썬 문법 + 앱 임포트 (네트워크 0건, 1초 이내)
.venv/Scripts/python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8'), str(p)) for p in pathlib.Path('server').rglob('*.py')]"
.venv/Scripts/python.exe -c "from server.app import create_app; print(len(create_app().openapi()['paths']),'paths')"

# JS 문법 (ui/package.json 의 type:module 덕분에 동작)
for f in ui/main.js ui/boot.js ui/modules/*.js; do node --check "$f" || exit 1; done

# 백엔드 단위
.venv/Scripts/python.exe qa/test_stats.py       # 54 checks
.venv/Scripts/python.exe qa/test_settings.py    # 44 checks
.venv/Scripts/python.exe qa/test_tasks.py       # 42 checks
.venv/Scripts/python.exe qa/test_search.py      # 56 checks (네트워크 0건 — urlopen 모킹)
.venv/Scripts/python.exe qa/test_terms.py       # 46 checks

# 카탈로그 재생성 (네트워크 필요, 수 분 소요)
.venv/Scripts/python.exe qa/build_catalog.py
```

브라우저에서 확인할 것:
- `<audio>` 스크러버를 중간으로 끌면 Network 탭에 **206 Partial Content** 가 떠야 한다
- `renderChimeOffline(id)` / `renderNoiseOffline(id)` 로 RMS > 0.01, peak < 1.0 확인
- 3초 세트로 바꿔 전체 사이클(집중→차임→휴식→다음 세트)을 20초 안에 확인

라우터 목록은 `create_app().openapi()['paths']` 로 확인한다.
`app.routes` 를 직접 순회하면 이 FastAPI 버전에서 `_IncludedRouter` 안을 못 들여다본다.

---

## 6. 알려진 제약

1. **Wikimedia rate-limit** — 휴식 음원(미군 군악대)은 한 번에 다 못 받을 수 있다.
   실패해도 앱은 정상 동작하며 "전체 내려받기" 를 다시 누르면 이어받는다.
2. **재생시간은 브라우저가 채운다** — 서버에서 MP3 헤더를 파싱하지 않는다.
   `audio.js:backfillDuration()` 이 `loadedmetadata` 에서 `PATCH /api/media/tracks/{id}` 로
   보낸다. 업로드·폴더 가져오기·검색(길이가 의심스러운 파생본) 세 경로가 같은 구멍을 쓰므로
   한 곳에서 닫았다. 즉, **한 번 재생하기 전까지는 재생시간이 비어 있다.**
3. **LAN 접속(`http://`)은 secure context 가 아니다** — 알림·Wake Lock·MediaSession 이
   다른 기기에서 동작하지 않는다. 설정 화면이 해당 토글을 비활성화하고 이유를 밝힌다.
4. **CC BY-SA 음원은 재인코딩 금지** — 확장 세트에 포함된다. 받은 MP3 를 그대로
   저장·서빙하며 크레딧 화면에 출처를 표시한다.
