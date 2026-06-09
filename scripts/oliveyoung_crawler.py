"""올리브영(OliveYoung) 구매후기(GDAS) 크롤러 — 로컬 실행용

이 환경은 oliveyoung.co.kr 가 네트워크 허용목록에 없어 직접 실행이 차단됩니다.
로컬(또는 허용된 환경)에서 실행하세요. 결과는 build_voc_dashboard.py 가 바로 쓰는
표준 스키마 CSV 로 저장됩니다 (한국어라 번역 단계 불필요).

표준 출력 컬럼:
  product_id, brand, product_name, category, rating, user_id, review_date,
  option, skin_type, review_content, review_content_kr, helpful, review_id

사용:
  pip install requests beautifulsoup4
  python scripts/oliveyoung_crawler.py --config config/oliveyoung_products.json \
      --out data/oliveyoung_reviews.csv --max-pages 200

주의: 올리브영은 마크업이 종종 바뀝니다. 후기가 안 잡히면 SELECTORS 의 셀렉터를
실제 페이지(개발자도구)에 맞춰 조정하세요. (엔드포인트/파라미터도 동일)
"""
import argparse
import csv
import json
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 올리브영 구매후기 AJAX 엔드포인트 (변경 시 여기만 수정)
GDAS_URL = "https://www.oliveyoung.co.kr/store/goods/getGdasList.do"
DETAIL_URL = "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do"

# 후기 파싱 셀렉터 (마크업 변경 시 조정)
SELECTORS = {
    "review_item": "li.gdas_list, ul.review_list > li, li[id^=gdasContents]",
    "rating": ".point, .review_point span, .score",
    "date": ".date, .review_info .date, em.date",
    "user": ".info .id, .name, .user_id",
    "text": ".txt_inner, .review_cont .txt, .txt, .review_text",
    "option": ".item, .option_info, .prd_option",
    "helpful": ".btn_recom .num, .recom_area .num, .help_count",
    "skin_attr": ".tag, .type dd, .info_view .item",
}


def warm_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9",
                      "Referer": "https://www.oliveyoung.co.kr/"})
    return s


def parse_rating(node):
    # 1) "5점" 텍스트  2) width:100% 스타일(=5점)  3) 별 개수
    el = node.select_one(SELECTORS["rating"])
    if el:
        m = re.search(r'(\d(?:\.\d)?)\s*점', el.get_text())
        if m:
            return int(round(float(m.group(1))))
        style = (el.get("style") or "")
        m = re.search(r'width:\s*(\d+)', style)
        if m:
            return int(round(int(m.group(1)) / 20))
    # 별 아이콘 개수
    on = node.select(".star .on, .ico_star.on, .point .on")
    if on:
        return len(on)
    return None


def _text(node, sel):
    el = node.select_one(sel)
    return el.get_text(strip=True) if el else None


def parse_reviews(html, meta):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(SELECTORS["review_item"])
    out = []
    for it in items:
        text = _text(it, SELECTORS["text"])
        if not text:
            continue
        date = _text(it, SELECTORS["date"])
        if date:
            date = re.sub(r'[^0-9.]', '', date).strip('.')          # 2024.05.01 형태로
        skin = " / ".join(dict.fromkeys(
            x.get_text(strip=True) for x in it.select(SELECTORS["skin_attr"]) if x.get_text(strip=True)))[:80]
        out.append({
            "product_id": meta["goodsNo"], "brand": meta["brand"],
            "product_name": meta["product"], "category": meta["category"],
            "rating": parse_rating(it),
            "user_id": _text(it, SELECTORS["user"]),
            "review_date": date,
            "option": _text(it, SELECTORS["option"]),
            "skin_type": skin,
            "review_content": text,
            "review_content_kr": text,                               # 국내=한국어, 그대로
            "helpful": re.sub(r'[^0-9]', '', _text(it, SELECTORS["helpful"]) or "") or "0",
        })
    return out


def crawl_goods(goodsNo, meta, session, max_pages=200, sleep=0.4):
    rows = []
    for pg in range(1, max_pages + 1):
        params = {"goodsNo": goodsNo, "itemNo": "all_search", "pageIdx": pg,
                  "rowsPerPage": 10, "gdasSort": "05", "type": "", "point": "",
                  "keywordGdasSeqs": "", "filterValue": "", "tagId": ""}
        try:
            r = session.get(GDAS_URL, params=params,
                            headers={"X-Requested-With": "XMLHttpRequest",
                                     "Referer": f"{DETAIL_URL}?goodsNo={goodsNo}"},
                            timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  [{goodsNo}] page {pg} 실패: {e}", file=sys.stderr)
            break
        batch = parse_reviews(r.text, {**meta, "goodsNo": goodsNo})
        if not batch:
            break
        rows.extend(batch)
        print(f"  [{meta['product']}/{goodsNo}] page {pg}: +{len(batch)} (누적 {len(rows)})")
        if len(batch) < 10:
            break
        time.sleep(sleep)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/oliveyoung_products.json")
    ap.add_argument("--out", default="data/oliveyoung_reviews.csv")
    ap.add_argument("--max-pages", type=int, default=200)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    products = cfg["products"]
    session = warm_session()
    # 쿠키 워밍업
    try:
        first = next(iter(products))
        session.get(f"{DETAIL_URL}?goodsNo={first}", timeout=20)
    except Exception:  # noqa
        pass

    all_rows = []
    for goodsNo, meta in products.items():
        if goodsNo.startswith("FILL_"):
            print(f"[건너뜀] {meta['product']}: goodsNo 미입력 (config 채우세요)")
            continue
        print(f"[{meta['product']}] 수집 시작 (goodsNo={goodsNo})")
        all_rows.extend(crawl_goods(goodsNo, meta, session, args.max_pages))

    if not all_rows:
        print("수집된 후기가 없습니다. config의 goodsNo, 네트워크, SELECTORS를 확인하세요.", file=sys.stderr)
        sys.exit(2)

    for i, r in enumerate(all_rows):
        r["review_id"] = "R" + str(i + 1).zfill(6)
    cols = ["review_id", "product_id", "brand", "product_name", "category", "rating",
            "user_id", "review_date", "option", "skin_type", "helpful",
            "review_content", "review_content_kr"]
    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n[완료] {len(all_rows)}건 → {args.out}")
    print("이 CSV를 업로드하면 build_voc_dashboard.py 로 동일 포맷 대시보드를 생성합니다.")


if __name__ == "__main__":
    main()
