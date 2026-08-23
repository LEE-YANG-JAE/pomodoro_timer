"""원자적 JSON 읽기/쓰기 + 프로세스 내 락.

llm_wiki 의 관례(§7.1)를 그대로 계승한다: tmp 파일에 쓴 뒤 `os.replace` 로 교체하면
같은 볼륨에서 원자적이므로, 쓰는 도중 프로세스가 죽어도 반쪽짜리 JSON 이 남지 않는다.

모든 사용자 데이터 모듈(settings·stats·playlists)이 이 모듈만 통해 디스크에 접근한다.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

# 모든 데이터 파일이 하나의 재진입 락을 공유한다. 파일 수가 4개뿐이고 쓰기가 드물어
# 파일별 락으로 쪼갤 실익이 없다. RLock 이라 같은 스레드의 중첩 획득은 안전하다.
_LOCK = threading.RLock()


def atomic_write(path: Path, data: Any) -> None:
    """`data` 를 JSON 으로 직렬화해 `path` 에 원자적으로 쓴다."""
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    """JSON 을 읽는다. 파일이 없거나 깨졌으면 `default` 를 돌려준다.

    깨진 파일에서 raise 하지 않는 것은 의도적이다 — 사용자 데이터 한 개가 손상됐다고
    앱 전체가 부팅에 실패하면 안 된다. 호출측이 기본값으로 계속 진행할 수 있어야 한다.
    """
    with _LOCK:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default


def append_capped(path: Path, key: str, item: dict, cap: int) -> dict:
    """`path` 의 JSON 객체 안 `key` 리스트에 `item` 을 붙이고 상한을 넘으면 앞을 자른다.

    llm_wiki `cards.py:_append_attempt` 와 같은 방식. 읽기-수정-쓰기 전체가 락 안에서
    일어나므로 동시 요청에서도 항목이 유실되지 않는다.
    """
    with _LOCK:
        doc = read_json(path, default=None)
        if not isinstance(doc, dict):
            doc = {"version": 1, key: []}
        items = doc.get(key)
        if not isinstance(items, list):
            items = []
        items.append(item)
        if len(items) > cap:
            items = items[-cap:]
        doc[key] = items
        atomic_write(path, doc)
        return doc
