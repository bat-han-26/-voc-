# Colorgram 틴트 · Qoo10 일본 리뷰 VOC 대시보드

Qoo10 일본(`qoo10.jp`)에서 **colorgram ティント(컬러그램 틴트)** 및 경쟁 틴트 브랜드 리뷰를
수집·번역·LLM 분류하고, 상단 **인사이트 결론**과 함께 경쟁 VOC를 분석하는 대시보드입니다.

> 검색어: `colorgram ティント`
> ([Qoo10 검색 결과](https://www.qoo10.jp/gmkt.inc/Mobile/Search/Default.aspx?keyword=colorgram+%E3%83%86%E3%82%A3%E3%83%B3%E3%83%88))

## 두 가지 산출물

1. **간편 대시보드** (`index.html`) — 단일 제품 리뷰 CSV를 드래그&드롭하면 즉시 분석. 의존성 없음.
2. **경쟁 VOC 딥다이브 파이프라인** (`scripts/pipeline_*` + `templates/`) — 컬러그램 vs 경쟁사
   여러 브랜드를 수집 → Gemini로 리뷰를 다차원 분류 → 레퍼런스급 8섹션 대시보드 생성.

## 구성

| 경로 | 설명 |
|------|------|
| `index.html` | 간편 대시보드 (단일 CSV 업로드형, 의존성 없음) |
| `config/products.json` | **브랜드·상품(gd_no)·분석 차원 설정** (여기를 채우고 시작) |
| `scripts/pipeline_1_crawl.py` | [1] 멀티 브랜드 리뷰 수집 → `data/reviews_raw.csv` |
| `scripts/pipeline_2_enrich.py` | [2] Gemini 리뷰 분류·번역 → `data/reviews_enriched.jsonl` |
| `scripts/pipeline_3_build.py` | [3] 집계 + 대시보드 생성 → `output/dashboard.html` |
| `scripts/run_pipeline.py` | 1→2→3 일괄 실행 오케스트레이터 |
| `templates/dashboard_template.html` | 딥다이브 대시보드 템플릿 (Chart.js, 데이터 주입형) |
| `scripts/qoo10_crawler_standalone.py` | 단일/단독 크롤러 (간편 대시보드용 CSV 생성) |
| `scripts/qoo10_crawler.py` | 원본 크롤러 (사내 모듈 의존, 참고용) |
| `data/sample_review_result.csv` | 간편 대시보드 업로드 테스트용 샘플 |

## 경쟁 VOC 딥다이브 — 실행법

```bash
pip install -r requirements.txt
# 1) config/products.json 에 브랜드별 Qoo10 gd_no 채우기
# 2) 번역·분류용 키
export GEMINI_API_KEY=...
# 3) 전체 파이프라인 실행 (수집 → 강화 → 빌드)
python scripts/run_pipeline.py
# → output/dashboard.html 완성
```

단계별 실행도 가능합니다:
```bash
python scripts/pipeline_1_crawl.py            # 수집
python scripts/pipeline_2_enrich.py           # LLM 강화(재개 가능)
python scripts/pipeline_3_build.py            # 대시보드 생성
```

### 딥다이브 대시보드 8개 섹션
인사이트 결론(자동 도출) · 제품별 비교 · 경쟁 포지셔닝 맵(지속력×자극/보습) ·
장단점 키워드(브랜드별, 클릭 시 샘플 인용) · 소비자 프로파일(입술타입/사용상황/구매트리거/감성) ·
재구매 vs 이탈 + 이탈사유 히트맵 · 브랜드간 이동(전환 패턴) · 생생 목소리(verbatim).

### LLM 강화 차원 (`pipeline_2_enrich.py`)
리뷰별로 감성 · 입술타입 · 사용상황 · 감성태그 · 구매결정 트리거 · 구매동기 ·
재구매/이탈 시그널 · 이탈사유 · 전환 브랜드 · 장단점 키워드 · 대표인용 여부를 태깅.

### 필요 네트워크 허용목록
`www.qoo10.jp` (수집), `generativelanguage.googleapis.com` (강화). 현재 클라우드 환경은
`qoo10.jp` 가 차단되어 있어, 로컬 또는 허용목록이 설정된 환경에서 실행하세요.

## 사용법

### 1) 대시보드만 빠르게 보기
`index.html` 을 브라우저에서 엽니다. 데이터 미업로드 시 **샘플 데이터(예시)** 로 모든 차트가 렌더링됩니다.

### 2) 실데이터로 분석
1. 로컬(또는 `qoo10.jp` 가 허용된 환경)에서 크롤러 실행 → `review_result.csv` 생성
   ```bash
   pip install requests beautifulsoup4
   # 상품 gd_no 들을 지정 (번역 원하면 --translate + GEMINI_API_KEY)
   python scripts/qoo10_crawler_standalone.py \
       --product 1057459512 --product 1135003693 --product 1036494829 \
       --out data/review_result.csv --translate
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
