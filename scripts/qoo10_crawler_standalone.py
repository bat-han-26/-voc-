"""Qoo10 일본 리뷰 크롤러 (단독 실행 버전 / standalone)

원본 ``qoo10_crawler.py`` 는 사내 모듈(crawler.base / core.openai_client /
config.settings)에 의존해 이 레포 단독으로는 실행되지 않습니다. 이 파일은
``requests`` + ``beautifulsoup4`` 만으로 동작하며, 결과를 대시보드(index.html)가
기대하는 스키마의 ``review_result.csv`` 로 바로 저장합니다.

번역(일→한)은 선택입니다.
  --translate 옵션 + GEMINI_API_KEY 환경변수가 있으면 Gemini REST API로 번역하고,
  없으면 review_content 에 일본어 원문을 그대로 넣습니다(차트는 정상 동작).

필요 네트워크 허용목록(allowlist):
  - www.qoo10.jp                       (리뷰 수집)
  - generativelanguage.googleapis.com  (번역, --translate 사용 시에만)

사용 예:
  python scripts/qoo10_crawler_standalone.py \\
      --product 1057459512 --product 1135003693 --product 1036494829 \\
      --out data/review_result.csv --translate

  # 검색 키워드로 상품 자동 탐색:
  python scripts/qoo10_crawler_standalone.py \\
      --search "colorgram ティント" --out data/review_result.csv --translate

  # 이어서 크롤링 (기존 CSV에 추가):
  python scripts/qoo10_crawler_standalone.py \\
      --product 1057459512 --out data/review_result.csv --resume --translate
"""
import argparse
import csv
import json
import os
import queue
import re
import sys
import threading
import time
from urllib.parse import urlparse, quote

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,ko;q=0.8,en;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
AJAX_HEADERS = {
    **HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
}

REVIEW_URL = ("https://www.qoo10.jp/gmkt.inc/Goods/GoodsReviewAjaxAppend.aspx"
              "?gd_no={gd}&group_code=2&page_no={pg}&page_size={size}&sort_type=N")
SEARCH_URL = "https://www.qoo10.jp/gmkt.inc/Search/Default.aspx?keyword={kw}"

OUTPUT_COLS = [
    "review_id", "product_id", "rating", "user_id", "review_date",
    "option", "option_kr", "moisturizing", "moisturizing_kr",
    "texture", "texture_kr", "scent", "scent_kr",
    "review_content", "review_content_jp",
]


# ──────────────────────────────────────────────
# 네트워크 유틸
# ──────────────────────────────────────────────

def _request_with_retry(fn, max_retries=4, base_sleep=1.0):
    """fn() 호출 실패 시 지수 백오프로 재시도."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == max_retries:
                raise
            wait = base_sleep * (2 ** attempt)
            print(f"  재시도 {attempt + 1}/{max_retries} (대기 {wait:.0f}s): {e}", file=sys.stderr)
            time.sleep(wait)
        except requests.HTTPError as e:
            # 4xx는 재시도해도 소용없으므로 바로 raise
            raise


def _warm_session(session, gd_no):
    """상품 페이지를 GET해 세션 쿠키를 채웁니다 (봇 차단 완화)."""
    try:
        url = f"https://www.qoo10.jp/g/{gd_no}"
        session.get(url, headers={**HEADERS, "Referer": "https://www.qoo10.jp/"}, timeout=20)
    except Exception:
        pass


# ──────────────────────────────────────────────
# 파싱
# ──────────────────────────────────────────────

def _text(node, sel):
    try:
        return node.select_one(sel).get_text(strip=True)
    except AttributeError:
        return None


def parse_reviews(html, gd_no):
    """리뷰 HTML 조각 → dict 리스트"""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for review in soup.find_all("li", recursive=False):
        score = _text(review, ".review_score .score")
        user_id = _text(review, ".review_user_info span:nth-of-type(1)")
        date = _text(review, ".review_user_info span:nth-of-type(2)")

        option_info = {}
        for span in review.select(".review_user_type span"):
            t = span.get_text(strip=True)
            if ":" in t:
                k, v = [x.strip() for x in t.split(":", 1)]
                option_info[k] = v

        eval_list = {}
        for item in review.select(".review_eval_list li"):
            span = item.find("span")
            if not span:
                continue
            key = span.get_text(strip=True)
            sib = span.next_sibling
            eval_list[key] = sib.strip() if sib and isinstance(sib, str) else None

        out.append({
            "product_id": gd_no,
            "rating": score,
            "user_id": user_id,
            "review_date": date,
            "option": option_info.get("オプション"),
            "moisturizing": eval_list.get("保湿力"),
            "texture": eval_list.get("テクスチャー"),
            "scent": eval_list.get("香り"),
            "review_content_jp": _text(review, ".review_txt"),
        })
    return out


# ──────────────────────────────────────────────
# 크롤링
# ──────────────────────────────────────────────

def crawl_product(gd_no, max_pages=50, page_size=500, sleep=0.6, session=None):
    s = session or requests.Session()
    _warm_session(s, gd_no)

    all_rows = []
    referer = f"https://www.qoo10.jp/g/{gd_no}"
    for pg in range(1, max_pages + 1):
        url = REVIEW_URL.format(gd=gd_no, pg=pg, size=page_size)
        hdrs = {**AJAX_HEADERS, "Referer": referer}
        try:
            r = _request_with_retry(lambda: s.post(url, headers=hdrs, timeout=20))
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  [{gd_no}] page {pg} 실패: {e}", file=sys.stderr)
            break

        rows = parse_reviews(r.content, gd_no)
        if not rows:
            break
        all_rows.extend(rows)
        print(f"  [{gd_no}] page {pg}: +{len(rows)} (누적 {len(all_rows)})")
        if len(rows) < page_size:
            break
        time.sleep(sleep)

    return all_rows


def search_product_ids(keyword, session=None, max_pages=3):
    """검색 결과 페이지네이션으로 상품 gd_no 수집."""
    s = session or requests.Session()
    ids = set()
    for page in range(1, max_pages + 1):
        url = SEARCH_URL.format(kw=quote(keyword)) + (f"&page={page}" if page > 1 else "")
        try:
            r = _request_with_retry(lambda: s.get(url, headers=HEADERS, timeout=20))
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"검색 실패 (page {page}): {e}", file=sys.stderr)
            break
        found = set()
        found |= set(re.findall(r"/g/(\d{6,})", r.text))
        found |= set(re.findall(r"goodscode=(\d{6,})", r.text))
        found |= set(re.findall(r"gd_no=(\d{6,})", r.text))
        if not found - ids:
            break  # 새 상품 없으면 종료
        ids |= found
    return sorted(ids)


def load_existing_ids(csv_path):
    """기존 CSV에서 이미 수집된 (product_id, user_id, review_date) 집합 반환."""
    existing = set()
    if not os.path.exists(csv_path):
        return existing
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                key = (row.get("product_id", ""), row.get("user_id", ""), row.get("review_date", ""))
                existing.add(key)
    except Exception:
        pass
    return existing


# ──────────────────────────────────────────────
# 번역 (선택)
# ──────────────────────────────────────────────

SYS_PROMPT = (
    "다음 일본어 화장품 리뷰 항목들을 한국어로 자연스럽고 감성까지 살려 번역해 주세요. "
    "어조·뉘앙스를 유지하고, 결과는 동일한 키를 가진 JSON 한 개로만 반환하세요. "
    "키: option, moisturizing, texture, scent, review_content_jp"
)


def translate_gemini(row, api_key, model="gemini-2.5-flash", max_retries=3):
    """단일 리뷰를 Gemini REST API로 번역. 실패 시 None 반환."""
    fields = {k: row.get(k) for k in
              ["option", "moisturizing", "texture", "scent", "review_content_jp"]}
    payload = {
        "system_instruction": {"parts": [{"text": SYS_PROMPT}]},
        "contents": [{"parts": [{"text": json.dumps(fields, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=60)
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(txt)
        except Exception as e:
            if attempt == max_retries:
                return None
            time.sleep(2 ** attempt)
    return None


def translate_all(rows, api_key, thread_count=80, model="gemini-2.5-flash"):
    """멀티스레드로 전체 리뷰 번역."""
    todo_q = queue.Queue()
    lock = threading.Lock()
    done_count = [0]
    total = len(rows)

    for i, row in enumerate(rows):
        todo_q.put((i, row))

    def worker():
        while True:
            try:
                seq, row = todo_q.get_nowait()
            except queue.Empty:
                return
            try:
                tr = translate_gemini(row, api_key, model=model)
                if tr:
                    for k, v in tr.items():
                        row[f"{k}_kr"] = v
            except Exception as e:
                print(f"  [{seq}] 번역 실패: {e}", file=sys.stderr)
            finally:
                with lock:
                    done_count[0] += 1
                    if done_count[0] % 50 == 0 or done_count[0] == total:
                        print(f"  번역 진행: {done_count[0]}/{total}")
                todo_q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(thread_count)]
    for t in threads:
        t.start()
    todo_q.join()
    print(f"번역 완료: {total}건")


# ──────────────────────────────────────────────
# CSV 저장
# ──────────────────────────────────────────────

def save_csv(rows, out_path, append=False):
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    mode = "a" if append else "w"
    write_header = not append or not os.path.exists(out_path)
    with open(out_path, mode, newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"저장 완료 → {out_path} ({len(rows)}건{'추가' if append else ''})")


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Qoo10 일본 리뷰 단독 크롤러")
    ap.add_argument("--product", action="append", default=[],
                    help="상품 gd_no 또는 상품 URL (여러 번 지정 가능)")
    ap.add_argument("--search", help="검색 키워드로 상품 자동 탐색")
    ap.add_argument("--search-pages", type=int, default=3,
                    help="검색 페이지 수 (기본 3)")
    ap.add_argument("--out", default="data/review_result.csv", help="출력 CSV 경로")
    ap.add_argument("--resume", action="store_true",
                    help="기존 CSV에 신규 리뷰만 추가 (중복 건너뜀)")
    ap.add_argument("--translate", action="store_true",
                    help="GEMINI_API_KEY 로 일→한 번역 수행")
    ap.add_argument("--translate-threads", type=int, default=80,
                    help="번역 병렬 스레드 수 (기본 80)")
    ap.add_argument("--model", default="gemini-2.5-flash",
                    help="Gemini 모델 ID (기본 gemini-2.5-flash)")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--page-size", type=int, default=500)
    ap.add_argument("--sleep", type=float, default=0.6,
                    help="페이지 간 대기 시간(초, 기본 0.6)")
    args = ap.parse_args()

    session = requests.Session()

    gd_list = []
    for p in args.product:
        gd_list.append(urlparse(p).path.split("/")[-1] if "/" in p else p)
    if args.search:
        found = search_product_ids(args.search, session, max_pages=args.search_pages)
        print(f"검색 '{args.search}' → 상품 {len(found)}개 발견: {found}")
        gd_list.extend(found)
    gd_list = [g for g in dict.fromkeys(gd_list) if g]

    if not gd_list:
        print("상품이 없습니다. --product 또는 --search 를 지정하세요.", file=sys.stderr)
        sys.exit(1)

    # 재개 모드: 기존 리뷰 key 셋 로드
    existing_keys = load_existing_ids(args.out) if args.resume else set()
    if args.resume and existing_keys:
        print(f"이어서 수집: 기존 {len(existing_keys)}건 건너뜀")

    rows = []
    for gd in gd_list:
        print(f"수집 시작: {gd}")
        product_rows = crawl_product(gd, args.max_pages, args.page_size,
                                     sleep=args.sleep, session=session)
        if existing_keys:
            before = len(product_rows)
            product_rows = [
                r for r in product_rows
                if (r["product_id"], r["user_id"], r["review_date"]) not in existing_keys
            ]
            print(f"  [{gd}] 중복 제거: {before} → {len(product_rows)}건")
        rows.extend(product_rows)

    print(f"총 신규 리뷰 {len(rows)}건 수집")

    if not rows:
        print("수집된 리뷰가 없습니다. (Qoo10 봇 차단 또는 잘못된 gd_no 가능)", file=sys.stderr)
        sys.exit(2)

    # 번역
    api_key = os.environ.get("GEMINI_API_KEY")
    if args.translate and api_key:
        print(f"Gemini 번역 시작… ({args.translate_threads}스레드)")
        translate_all(rows, api_key,
                      thread_count=args.translate_threads,
                      model=args.model)
    elif args.translate:
        print("GEMINI_API_KEY 가 없어 번역을 건너뜁니다.", file=sys.stderr)

    # ID 부여 및 review_content 정리
    start_idx = len(existing_keys) + 1
    for i, row in enumerate(rows):
        row["review_id"] = "R" + str(start_idx + i).zfill(5)
        row["review_content"] = row.get("review_content_jp_kr") or row.get("review_content_jp")

    save_csv(rows, args.out, append=args.resume)
    print("이 CSV 를 index.html 대시보드에 드래그&드롭하면 실데이터로 갱신됩니다.")


if __name__ == "__main__":
    main()
