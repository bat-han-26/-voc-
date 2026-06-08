"""[1단계] 멀티 브랜드 Qoo10 리뷰 수집

config/products.json 의 브랜드별 product_ids 를 순회하며 리뷰를 수집하고
data/reviews_raw.csv (brand 컬럼 포함) 로 저장합니다.

사용:
  python scripts/pipeline_1_crawl.py
  python scripts/pipeline_1_crawl.py --config config/products.json --out data/reviews_raw.csv

필요 네트워크 허용목록: www.qoo10.jp
"""
import argparse
import csv
import json
import os
import sys
import time

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


def parse_reviews(html, gd_no, brand):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for review in soup.find_all("li", recursive=False):
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
            sib = span.next_sibling
            eval_list[span.get_text(strip=True)] = sib.strip() if sib and isinstance(sib, str) else None
        out.append({
            "brand": brand,
            "product_id": gd_no,
            "rating": _text(review, ".review_score .score"),
            "user_id": _text(review, ".review_user_info span:nth-of-type(1)"),
            "review_date": _text(review, ".review_user_info span:nth-of-type(2)"),
            "option": option_info.get("オプション"),
            "moisturizing": eval_list.get("保湿力"),
            "texture": eval_list.get("テクスチャー"),
            "scent": eval_list.get("香り"),
            "review_content_jp": _text(review, ".review_txt"),
        })
    return out


def crawl_product(gd_no, brand, session, max_pages=50, page_size=500, sleep=0.6):
    rows = []
    for pg in range(1, max_pages + 1):
        url = REVIEW_URL.format(gd=gd_no, pg=pg, size=page_size)
        try:
            r = session.post(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"    [{brand}/{gd_no}] page {pg} 실패: {e}", file=sys.stderr)
            break
        batch = parse_reviews(r.content, gd_no, brand)
        if not batch:
            break
        rows.extend(batch)
        print(f"    [{brand}/{gd_no}] page {pg}: +{len(batch)} (누적 {len(rows)})")
        if len(batch) < page_size:
            break
        time.sleep(sleep)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/products.json")
    ap.add_argument("--out", default="data/reviews_raw.csv")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--page-size", type=int, default=500)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    session = requests.Session()
    all_rows = []
    for brand in cfg["brands"]:
        ids = brand.get("product_ids") or []
        if not ids:
            print(f"[건너뜀] {brand['name']}: product_ids 비어 있음 (config에 gd_no 채우세요)")
            continue
        print(f"[{brand['name']}] 상품 {len(ids)}개 수집")
        for gd in ids:
            all_rows.extend(crawl_product(gd, brand["name"], session,
                                          args.max_pages, args.page_size))

    if not all_rows:
        print("수집된 리뷰가 없습니다. config의 product_ids 와 네트워크 허용목록을 확인하세요.",
              file=sys.stderr)
        sys.exit(2)

    cols = ["brand", "product_id", "rating", "user_id", "review_date", "option",
            "moisturizing", "texture", "scent", "review_content_jp"]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n[1단계 완료] {len(all_rows)}건 → {args.out}")


if __name__ == "__main__":
    main()
