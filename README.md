# Colorgram 틴트 · Qoo10 일본 리뷰 VOC 대시보드

Qoo10 일본(`qoo10.jp`)에서 **colorgram ティント(콜로그램 틴트)** 리뷰를 수집·번역하고,
평점·만족도·옵션·키워드·리뷰 원문을 한눈에 보는 **인터랙티브 대시보드**입니다.
상단에 **인사이트 결론**이 자동으로 요약됩니다.

> 검색어: `colorgram ティント`
> ([Qoo10 검색 결과](https://www.qoo10.jp/gmkt.inc/Mobile/Search/Default.aspx?keyword=colorgram+%E3%83%86%E3%82%A3%E3%83%B3%E3%83%88))

## 구성

| 경로 | 설명 |
|------|------|
| `index.html` | **대시보드 (메인 산출물)**. 의존성 없는 단일 HTML — 더블클릭으로 바로 열림 |
| `scripts/qoo10_crawler.py` | Qoo10 리뷰 크롤러 + Gemini 번역 스크립트 (참고/실행용) |
| `data/sample_review_result.csv` | 크롤러 출력 형식 예시 + 대시보드 업로드 테스트용 샘플 |

## 사용법

### 1) 대시보드만 빠르게 보기
`index.html` 을 브라우저에서 엽니다. 데이터 미업로드 시 **샘플 데이터(예시)** 로 모든 차트가 렌더링됩니다.

### 2) 실데이터로 분석
1. 로컬(또는 `qoo10.jp` 가 허용된 환경)에서 크롤러 실행 → `review_result.csv` 생성
   ```bash
   python scripts/qoo10_crawler.py   # PRODUCT_LIST, OUTPUT_PATH 수정 후
   ```
2. 생성된 `review_result.csv` 를 `index.html` 화면의 업로드 영역에 **드래그&드롭**
3. KPI · 차트 · 인사이트 결론이 실데이터로 즉시 갱신됩니다.
   (모든 처리는 브라우저 안에서만 수행되며 외부 서버로 전송되지 않습니다.)

## 대시보드가 보여주는 것

- **인사이트 결론** (상단): 종합 판정 + 강점/약점 자동 요약
- **KPI**: 총 리뷰 수, 평균 평점, 긍정 비율, 최다 옵션
- **차트**: 평점 분포 · 긍·부정 비중 · 월별 리뷰 추이 · 옵션별 리뷰 수 Top10 · 세부 만족도(보습/텍스처/향) · 키워드 빈도 Top20
- **리뷰 원문 탐색**: 평점/옵션 필터 + 키워드 검색 + 한·일 원문 동시 표시

## 입력 데이터 스키마 (`review_result.csv`)

크롤러가 생성하는 컬럼을 그대로 사용합니다. 번역 컬럼(`*_kr`)이 있으면 우선 사용합니다.

| 컬럼 | 의미 |
|------|------|
| `review_id` | 리뷰 ID |
| `product_id` | 상품(gd_no) |
| `rating` | 별점(1~5) |
| `review_date` | 작성일 (`YYYY.MM.DD` 등) |
| `option` / `option_kr` | 구매 옵션(컬러/타입) |
| `moisturizing` · `texture` · `scent` (+`*_kr`) | 보습력 · 텍스처 · 향 만족도 |
| `review_content` / `review_content_jp_kr` | 번역된 리뷰 본문 |
| `review_content_jp` | 일본어 원문 |

## 데이터 출처 안내

- **실데이터**: 업로드한 `review_result.csv` (Qoo10 크롤러 출력).
- **샘플 데이터**: 공개 리뷰 플랫폼(LIPS·@cosme·モノシル·mybest 등)에서 확인된 실제
  Colorgram 틴트 평가 경향(윤기·지속력·보습 호평, 일부 입술 자극·색 호불호·용량 아쉬움)을
  반영해 구성한 **예시**이며, 실제 Qoo10 집계 수치가 아닙니다.

## 참고

이 클라우드 실행 환경은 네트워크 정책상 `qoo10.jp` 접근이 차단되어 있어 크롤러를
환경 내에서 직접 실행할 수 없습니다. 크롤링은 로컬 환경에서 수행 후 CSV만 업로드하세요.
