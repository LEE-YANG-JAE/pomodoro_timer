# API 레퍼런스

기본 주소 `http://127.0.0.1:8025`. 모든 본문은 JSON (업로드만 multipart).

**공통 규칙**
- 시간 필드는 **초 단위** (`focus_seconds`, `break_seconds`). 분은 UI 표시 단위일 뿐이다.
- 모든 요청 모델은 `extra="forbid"` — 알 수 없는 키는 422.
- `/api/*` 의 상태 변경 요청은 cross-site `Origin` 이면 **403** (`OriginGuardMiddleware`).
  `Origin` 헤더가 없는 요청(curl, 앱)은 통과한다.

---

## 설정 `/api/settings`

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/settings` | 전체 설정 |
| PUT | `/api/settings` | **그룹 단위 교체** (부분 병합 아님) |
| POST | `/api/settings/reset` | `{confirm:true}` → 기본값 복원 |

`PUT` 은 `timer` / `audio` / `records` / `ui` / `media` 중 수정한 그룹을 **통째로** 보낸다.
병합 규칙이 없으니 병합 버그도 없다. 응답:

```json
{ "settings": { ... }, "changed": ["timer"], "recomputed_sessions": 0 }
```

`records.day_start_hour` 가 바뀌면 기존 기록의 `local_date` 를 **동기로 소급 재계산**하고
그 건수를 `recomputed_sessions` 로 돌려준다.

### `timer` 그룹 — 사이클 세트

```json
{
  "sets": [
    { "focus_seconds": 1500, "break_seconds": 300, "label": null }
  ],
  "repeat": true,
  "auto_start_break": true,
  "auto_start_focus": false,
  "interruption_policy": "ask"
}
```

- `sets` — 1~24개. 한 세트 = 집중 한 번 + 뒤따르는 휴식 한 번
- `focus_seconds` 1~10800, `break_seconds` 1~7200 (하한이 1초인 것은 테스트가 3초 사이클을
  돌릴 수 있게 하기 위함 — 사람이 쓰는 최소값은 UI 가 1분으로 강제한다)
- `interruption_policy` — `ask` | `extend` | `ignore`

옛 설정(`focus_seconds`/`short_break_seconds`/`long_break_seconds`/`cycles_until_long_break`)은
로드 시 동등한 세트 목록으로 자동 변환된다 (25/5/15 × 4회 → `[25/5, 25/5, 25/5, 25/15]`).

### `audio` 그룹 (발췌)

```json
{
  "focus_playlist_id": "focus", "break_playlist_id": "break",
  "music_volume": 55, "chime_volume": 70, "silent_mode": false,
  "shuffle_focus": false, "shuffle_break": true, "crossfade_seconds": 3,
  "chime_variant": "bell", "chime_enabled": true, "duck_on_chime": true,
  "noise_enabled": false, "noise_type": "brown",
  "noise_volume": 35, "noise_phases": "focus"
}
```

`chime_variant` — `bell`|`soft`|`digital` · `noise_type` — `brown`|`pink`|`white`|`rain`|`waves`|`fan`
`noise_phases` — `focus`(집중할 때만) | `all`

---

## 기록 `/api/stats`

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/stats/sessions` | 세션 1건 기록 (**`client_id` upsert**) |
| GET | `/api/stats/summary?days=14&today=` | 오늘+N일+스트릭 묶음 — 프론트 기본 호출 |
| GET | `/api/stats/today?today=` | 오늘 요약 |
| GET | `/api/stats/series?days=7&today=` | N일 시계열 (빈 날도 0으로 채움) |
| GET | `/api/stats/streak?today=` | 연속 달성 일수 |
| GET | `/api/stats/sessions?limit=&offset=&date=` | 원시 로그 |
| DELETE | `/api/stats/sessions/{id}` | 1건 삭제 |
| POST | `/api/stats/reset` | `{confirm:true}` → 전체 삭제 |

### ★ 타임존 계약

`started_at` / `ended_at` 은 **offset-aware** 여야 한다. naive 값은 **422**.

```json
{
  "phase": "focus",
  "started_at": "2026-08-22T23:41:00.000+09:00",
  "ended_at":   "2026-08-23T00:06:00.000+09:00",
  "planned_seconds": 1500, "actual_seconds": 1500,
  "completed": true, "cycle_index": 0, "interruptions": 0,
  "client_id": "s_mt4bu0cx_s2a135"
}
```

`Date#toISOString()` 의 `Z`(UTC)를 보내면 **자정 근처 세션이 하루 어긋난다.**
프론트는 `utils.js` 의 `toLocalISO()` 로 로컬 오프셋을 붙인다.

`today` 쿼리도 **클라이언트의 로컬 날짜**(`YYYY-MM-DD`)를 보낸다. 생략하면 서버 로컬 날짜로 열화한다.

### 집계 규칙

- **뽀모도로 개수** = `phase == "focus" AND completed`
- **총 집중 시간** = 위 세션들의 `actual_seconds` 합
- 중도 포기는 개수/시간에 넣지 않고 `aborted_count` / `aborted_seconds` 로 별도 보고
- 휴식은 기록하지 않는다 (프론트가 POST 하지 않음)
- `streak` 은 오늘 기록이 없어도 어제까지 이어졌으면 유지하고 `includes_today: false` 로 알린다

---

## 음원 `/api/media`

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/media/catalog?tier=` | 카탈로그 + 트랙별 `ready` |
| GET | `/api/media/credits` | 출처·라이선스 (크레딧 화면용) |
| GET | `/api/media/status` | **진행률 폴링** |
| POST | `/api/media/download` | `{tier:"core"\|"extra"\|"all", track_ids?:[]}` |
| POST | `/api/media/download/cancel` | 중단 (`.part` 보존 → 다음에 이어받음) |
| GET | `/api/media/tracks?ready_only=` | 전체 트랙 (카탈로그 ∪ 사용자) |
| PATCH | `/api/media/tracks/{id}` | 제목·작곡가·재생시간 수정 (사용자 트랙만) |
| DELETE | `/api/media/tracks/{id}` | 삭제 + 재생목록 캐스케이드 |
| POST | `/api/media/upload` | multipart `file` |
| GET | `/api/dirs?path=` | 폴더 브라우저 (이름·크기만) |
| GET | `/api/media/scan-folder?path=` | 폴더 내 오디오 미리보기 |
| POST | `/api/media/import-folder` | `{folder, names:[]}` → `media/user/` 로 복사 |

`GET /api/media/status`:

```json
{
  "active": true,
  "job": { "job_id":"...", "status":"running", "total":38, "done":12,
           "failed":0, "bytes_done":..., "bytes_total":...,
           "current_track_id":"usaf-03", "current_title_ko":"..." },
  "ready_count": 46, "catalog_count": 78, "has_any_ready": true,
  "message_ko": "음원 내려받는 중 12/38 — ..."
}
```

---

## 할 일 `/api/tasks`

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/tasks?today=&days=1` | 목록 + `active_task_id` + `totals` |
| POST | `/api/tasks` | 생성 (`{name, est_pomodoros?, note?, created_at?}`) |
| PATCH | `/api/tasks/{tid}` | 수정 / 완료 토글 |
| DELETE | `/api/tasks/{tid}` | 삭제 |
| PUT | `/api/tasks/order` | 재정렬 — **빠진 id 는 살려 둔다** (재생목록과 반대) |
| PUT | `/api/tasks/active` | 선택 / 해제 (`{"task_id": null}` 은 정상 경로) |
| POST | `/api/tasks/clear-completed` | 완료 항목 정리 |

★ `done_pomodoros` 는 **저장되지 않는다.** 응답에서 세션 로그를 세어 파생한다:
`phase=="focus" ∧ completed ∧ task_id == t.id`. 그래서 `POST /api/stats/reset` 이
모든 작업의 개수를 정확히 0 으로 만들고, 오프라인 큐 재전송이 이중 계산되지 않는다.

`created_at` / `at` 은 **offset-aware** 여야 한다 (세션과 같은 계약). naive 는 422.

---

## 음원 검색 `/api/media/search`

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/media/search/presets` | 추천 검색 6개 |
| GET | `/api/media/search?q=&page=&rows=&preset=` | 앨범 목록 + `query.notice_ko` |
| GET | `/api/media/search/item/{identifier}` | 곡 목록 + `addable` + `reason_ko` |
| POST | `/api/media/search/add` | `{identifier, names[], playlists[], download}` |

★ **서버는 클라이언트의 `url`/`bytes`/`sha1` 을 받지 않는다.** `identifier` + `names` 만 받고
메타데이터를 다시 가져와 모든 값을 파생한다. `names` 는 방금 받은 파일 목록에 있을 때만
통과하므로 임의 URL 주입이 불가능하다.

라이선스 게이트는 **추가 시점**에 건다(검색 시점 `licenseurl` 은 100건 중 5건만 존재).
`pd`/`by`/`by-sa` 만 통과하고 나머지는 **409**.

| 상태 | 코드 |
|---|---|
| 빈/과길이 질의 | 400 |
| 잘못된 식별자 | 400 |
| 다크 처리된 항목 (`/metadata` 가 `{}`) | 404 |
| 라이선스 미확인 / NC·ND / 샘플만 제공 | 409 |
| 요청 과다 | 429 |
| 응답 파싱 실패 (`response` 키 없음) | 502 |
| 연결 실패 | 504 |

추가한 곡은 `data/tracks.json`(`origin: "search"`) + `media/user/` 로 간다 —
`media_catalog.json` 은 생성물이라 거기 넣으면 재생성 때 지워진다.

---

## 재생목록 `/api/playlists`

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/playlists` | 목록 (`count`, `ready_count` 포함) |
| POST | `/api/playlists` | `{name_ko}` |
| GET | `/api/playlists/{pid}` | 단건 + 트랙 상세 |
| PUT | `/api/playlists/{pid}` | `{name_ko?, track_ids?}` — **배열 순서가 곧 재생 순서** |
| DELETE | `/api/playlists/{pid}` | 삭제 (`focus`/`break` 는 **400**) |
| POST | `/api/playlists/{pid}/tracks` | `{track_ids}` 추가 |
| DELETE | `/api/playlists/{pid}/tracks/{tid}` | 제거 |

재정렬 전용 엔드포인트는 없다 — `PUT` 에 전체 배열을 보낸다. 코드 경로가 하나뿐이라
인덱스 스왑 버그가 생길 자리가 없다.

---

## 정적 · 기타

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | SPA |
| GET | `/health` | `{"ok":true}` |
| GET | `/media/{subdir}/{file}` | **오디오 실파일 — HTTP Range 지원 (200 / 206 / 416)** |
| GET | `/static/{path}` | UI 자산 (no-cache) |

`/media` 는 Starlette `StaticFiles` 마운트다. `FileResponse` 가 Range 를 완전 구현하므로
`<audio>` 탐색이 그냥 동작한다 — 커스텀 엔드포인트가 없다.

```bash
curl.exe -s -D - -o NUL -H "Range: bytes=0-1023" http://127.0.0.1:8025/media/catalog/wtc1-01.mp3
```
