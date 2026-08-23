"""server/terms.py 단위 테스트 — 네트워크 없음.

실행:  .venv\\Scripts\\python.exe qa\\test_terms.py

★ 가장 중요한 검사는 §무해성이다. clean_title / to_korean 을 생성기에서 server/terms.py 로
  옮겼는데, 그 이동이 media_catalog.json 의 출력을 바꾸지 않았음을 **기존 카탈로그로**
  증명한다. 바뀌었다면 다음 생성 때 모든 제목이 조용히 달라진다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from server import terms  # noqa: E402

checks = 0


def check(label: str, cond: bool) -> None:
    global checks
    checks += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ok  {label}")


print("== 라이선스 판정 ==")
# ★ NC/ND 를 먼저 봐야 한다 — by-nc-sa 가 by-sa 로 새면 상업적 이용 금지 음원이 통과한다
check("by-nc-sa -> blocked (by-sa 로 새지 않는다)",
      terms.license_kind("https://creativecommons.org/licenses/by-nc-sa/3.0/") == "blocked")
check("by-nc -> blocked",
      terms.license_kind("https://creativecommons.org/licenses/by-nc/4.0/") == "blocked")
check("by-nd -> blocked",
      terms.license_kind("https://creativecommons.org/licenses/by-nd/4.0/") == "blocked")
check("by-nc-nd -> blocked",
      terms.license_kind("https://creativecommons.org/licenses/by-nc-nd/4.0/") == "blocked")
check("by-sa -> by-sa",
      terms.license_kind("https://creativecommons.org/licenses/by-sa/3.0/") == "by-sa")
check("by -> by",
      terms.license_kind("https://creativecommons.org/licenses/by/4.0/") == "by")
check("publicdomain/mark -> pd",
      terms.license_kind("http://creativecommons.org/publicdomain/mark/1.0/") == "pd")
check("publicdomain/zero -> pd",
      terms.license_kind("http://creativecommons.org/publicdomain/zero/1.0/") == "pd")
check("Commons 'Public domain' -> pd", terms.license_kind("Public domain") == "pd")
check("'CC BY-SA 4.0' -> by-sa", terms.license_kind("CC BY-SA 4.0") == "by-sa")
check("'CC BY-NC 3.0' -> blocked", terms.license_kind("CC BY-NC 3.0") == "blocked")
check("None -> unknown", terms.license_kind(None) == "unknown")
check("빈 문자열 -> unknown", terms.license_kind("") == "unknown")
check("알 수 없는 URL -> unknown",
      terms.license_kind("https://example.com/some-license") == "unknown")

print("== 저작자 표시 의무 ==")
# ★ 예전 catalog.credits() 는 URL 을 startswith("CC-BY") 로 판정해 **항상 False** 였다
check("CC BY 는 표시 의무 있음",
      terms.requires_attribution("https://creativecommons.org/licenses/by/4.0/") is True)
check("CC BY-SA 는 표시 의무 있음",
      terms.requires_attribution("https://creativecommons.org/licenses/by-sa/3.0/") is True)
check("PD 는 표시 의무 없음",
      terms.requires_attribution("http://creativecommons.org/publicdomain/mark/1.0/") is False)
check("NC 는 애초에 추가 불가라 표시 의무도 없음",
      terms.requires_attribution("https://creativecommons.org/licenses/by-nc/4.0/") is False)
check("배지 한국어",
      terms.license_badge_ko("https://creativecommons.org/licenses/by-sa/3.0/") == "CC BY-SA")
check("추가 가능 집합", terms.LICENSE_ADDABLE == {"pd", "by", "by-sa"})

print("== 한국어 → 검색어 자동 반전 ==")
check("반전 쌍이 20개 이상", len(terms.KO_TO_EN) >= 20)
for ko, en in [("바흐", "Bach"), ("쇼팽", "Chopin"), ("야상곡", "Nocturne"),
               ("협주곡", "Concerto"), ("전주곡", "Prelude"), ("모음곡", "Suite")]:
    check(f"{ko} -> {en}", terms.KO_TO_EN.get(ko) == en)
# 역참조가 있는 항목(장조/단조)은 반전 불가 — 조용히 빠져야 한다
check("역참조 패턴은 반전하지 않는다",
      not any("장조" in k or "단조" in k for k in terms.KO_TO_EN))

lookup = terms.ko_lookup()
keys = list(lookup)
check("보충어가 합쳐진다 (클래식)", lookup.get("클래식") == "classical")
check("보충어가 합쳐진다 (실내악)", lookup.get("실내악") == "chamber music")
# ★ 긴 키가 먼저여야 '현악사중주' 가 '사중주' 로 잘못 잘리지 않는다
check("긴 키 우선 정렬", keys.index("현악사중주") < keys.index("사중주"))
check("긴 키 우선 정렬 (클래식음악 > 클래식)",
      keys.index("클래식음악") < keys.index("클래식"))

print("== 제목 정리 ==")
check("archive.org 형식 — 마지막 조각",
      terms.clean_title(
          "Kimiko Ishizaka - Bach- Well-Tempered Clavier, Book 1 - "
          "01 Prelude No. 1 in C major, BWV 846.mp3", segment="last")
      == "Prelude No. 1 in C major, BWV 846")
check("Commons 형식 — 첫 조각",
      terms.clean_title('"A Winter Prelude" - Concert Band - '
                        'United States Air Force Band.mp3', segment="first")
      == "A Winter Prelude")
check("따옴표 잔여물 제거",
      '"' not in terms.clean_title('"Brindisi" from La Traviata - Singing Sergeants - '
                                   'United States Air Force Band.mp3', segment="first"))
check("뒤에 붙는 연주자 설명 제거",
      "played by" not in terms.clean_title(
          '"Jack Tar", played by the U.S. Naval Academy Band.mp3', segment="first"))

print("== 한국어화 ==")
check("작곡가 + 악곡 + 조성",
      terms.to_korean(terms.clean_title("Bach Prelude in C major")) == "바흐 전주곡 C장조")
# ★ 소유격에서 캡처를 되돌리지 않으면 작곡가 이름이 통째로 사라진다
check("소유격 — 이름이 살아남는다",
      terms.to_korean("Grieg's Suite for Strings").startswith("그리그의"))
check("대응어가 없으면 원제 유지",
      terms.to_korean("Contrapunctus 1") == "Contrapunctus 1")

print("== parse_length ==")
check("초 표기", terms.parse_length("253.4") == 253)
check("mm:ss 표기", terms.parse_length("4:13") == 253)
check("hh:mm:ss 표기", terms.parse_length("1:04:13") == 3853)
check("None", terms.parse_length(None) is None)
check("쓰레기 값", terms.parse_length("abc") is None)

print("== ★ 무해성 — 카탈로그 출력이 바뀌지 않았다 ==")
cat_path = ROOT / "media_catalog.json"
if not cat_path.exists():
    print("  ..  media_catalog.json 이 없어 건너뜀 (qa/build_catalog.py 를 먼저 실행하세요)")
else:
    doc = json.loads(cat_path.read_text(encoding="utf-8"))
    by_source = {s["source_id"]: s for s in doc["sources"]}
    sampled = 0
    for t in doc["tracks"]:
        src = by_source.get(t["source_id"], {})
        segment = "last" if src.get("provider") == "archive.org" else "first"
        # title_orig 는 clean_title 의 결과이므로, 거기에 to_korean 을 다시 적용하면
        # 저장된 title_ko 와 정확히 같아야 한다.
        if terms.to_korean(t["title_orig"]) != t["title_ko"]:
            raise AssertionError(
                f"FAIL: 한국어화가 달라짐 — {t['id']}\n"
                f"  저장됨: {t['title_ko']!r}\n"
                f"  재계산: {terms.to_korean(t['title_orig'])!r}")
        sampled += 1
    checks += 1
    print(f"  ok  전체 {sampled}곡의 title_ko 가 재계산 결과와 일치한다")

    # 라이선스 판정이 실제 카탈로그에서 합리적인가
    kinds = {terms.license_kind(s.get("license")) for s in doc["sources"]}
    checks += 1
    assert kinds <= {"pd", "by", "by-sa"}, f"카탈로그에 사용 불가 라이선스가 있다: {kinds}"
    print(f"  ok  카탈로그의 모든 소스가 추가 가능한 라이선스 ({sorted(kinds)})")

print(f"\nterms OK - {checks} checks passed")
