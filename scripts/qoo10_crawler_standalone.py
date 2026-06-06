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
  python scripts/qoo10_crawler_standalone.py \
      --product 1057459512 --product 1135003693 --product 1036494829 \
      --out data/review_result.csv --translate

  # 검색 키워드로 상품 자동 탐색(베스트 에포트):
  python scripts/qoo10_crawler_standalone.py --search "colorgram ティント" --out data/review_result.csv
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "ja,en;q=0.8,ko;q=0.6",
    "Referer": "https://www.qoo10.jp/",
    "X-Requested-With": "XMLHttpRequest",
}

REVIEW_URL = ("https://www.qoo10.jp/gmkt.inc/Goods/GoodsReviewAjaxAppend.aspx"
              "?gd_no={gd}&group_code=2&page_no={pg}&page_size={size}&sort_type=N")


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


def crawl_product(gd_no, max_pages=50, page_size=500, sleep=0.6, session=None):
    s = session or requests.Session()
    all_rows = []
    for pg in range(1, max_pages + 1):
        url = REVIEW_URL.format(gd=gd_no, pg=pg, size=page_size)
        try:
            r = s.post(url, headers=HEADERS, timeout=20)
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


def search_product_ids(keyword, session=None):
    """검색 페이지에서 상품 gd_no 추출(베스트 에포트). 차단 시 빈 리스트."""
    s = session or requests.Session()
    url = ("https://www.qoo10.jp/gmkt.inc/Search/Default.aspx?keyword="
           + requests.utils.quote(keyword))
    try:
        r = s.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"검색 실패: {e}", file=sys.stderr)
        return []
    ids = set(re.findall(r"/g/(\d{6,})", r.text))
    ids |= set(re.findall(r"goodscode=(\d{6,})", r.text))
    ids |= set(re.findall(r"gd_no=(\d{6,})", r.text))
    return sorted(ids)


# ---------------- 번역 (선택) ----------------
SYS_PROMPT = (
    "다음 일본어 화장품 리뷰 항목들을 한국어로 자연스럽고 감성까지 살려 번역해 주세요. "
    "어조·뉘앙스를 유지하고, 결과는 동일한 키를 가진 JSON 한 개로만 반환하세요. "
    "키: option, moisturizing, texture, scent, review_content_jp"
)


def translate_gemini(row, api_key, model="gemini-2.5-flash"):
    fields = {k: row.get(k) for k in
              ["option", "moisturizing", "texture", "scent", "review_content_jp"]}
    payload = {
        "system_instruction": {"parts": [{"text": SYS_PROMPT}]},
        "contents": [{"parts": [{"text": json.dumps(fields, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(txt)


def main():
    ap = argparse.ArgumentParser(description="Qoo10 일본 리뷰 단독 크롤러")
    ap.add_argument("--product", action="append", default=[],
                    help="상품 gd_no 또는 상품 URL (여러 번 지정 가능)")
    ap.add_argument("--search", help="검색 키워드로 상품 자동 탐색")
    ap.add_argument("--out", default="data/review_result.csv", help="출력 CSV 경로")
    ap.add_argument("--translate", action="store_true",
                    help="GEMINI_API_KEY 로 일→한 번역 수행")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--page-size", type=int, default=500)
    args = ap.parse_args()

    session = requests.Session()

    gd_list = []
    for p in args.product:
        gd_list.append(urlparse(p).path.split("/")[-1] if "/" in p else p)
    if args.search:
        found = search_product_ids(args.search, session)
        print(f"검색 '{args.search}' → 상품 {len(found)}개 발견: {found}")
        gd_list.extend(found)
    gd_list = [g for g in dict.fromkeys(gd_list) if g]

    if not gd_list:
        print("상품이 없습니다. --product 또는 --search 를 지정하세요.", file=sys.stderr)
        sys.exit(1)

    rows = []
    for gd in gd_list:
        print(f"수집 시작: {gd}")
        rows.extend(crawl_product(gd, args.max_pages, args.page_size, session=session))
    print(f"총 리뷰 {len(rows)}건 수집")

    if not rows:
        print("수집된 리뷰가 없습니다. (Qoo10 봇 차단 또는 잘못된 gd_no 가능)", file=sys.stderr)
        sys.exit(2)

    # 번역
    api_key = os.environ.get("GEMINI_API_KEY")
    if args.translate and api_key:
        print("Gemini 번역 시작…")
        for i, row in enumerate(rows):
            try:
                tr = translate_gemini(row, api_key)
                for k, v in tr.items():
                    row[f"{k}_kr"] = v
                if (i + 1) % 20 == 0:
                    print(f"  번역 {i + 1}/{len(rows)}")
            except Exception as e:
                print(f"  {i} 번역 실패: {e}", file=sys.stderr)
    elif args.translate:
        print("GEMINI_API_KEY 가 없어 번역을 건너뜁니다.", file=sys.stderr)

    # 표준 스키마로 정리
    for i, row in enumerate(rows):
        row["review_id"] = "R" + str(i + 1).zfill(5)
        row["review_content"] = row.get("review_content_jp_kr") or row.get("review_content_jp")

    cols = ["review_id", "product_id", "rating", "user_id", "review_date",
            "option", "option_kr", "moisturizing", "moisturizing_kr",
            "texture", "texture_kr", "scent", "scent_kr",
            "review_content", "review_content_jp"]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"저장 완료 → {args.out} ({len(rows)}건)")
    print("이 CSV 를 index.html 대시보드에 드래그&드롭하면 실데이터로 갱신됩니다.")


if __name__ == "__main__":
    main()
