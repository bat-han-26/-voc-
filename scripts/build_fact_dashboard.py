"""100% 팩트 대시보드 생성기 — 실제 Qoo10 review_result.csv 만 사용 (제품/브랜드 분리)

추론/합성 일절 없음. 데이터에 실재하는 컬럼만 집계하고, config/product_map.json 의
product_id↔브랜드/제품 매핑으로 명확히 분리한다.
"""
import argparse
import json
import re
from collections import Counter

import pandas as pd

STOP = set("그리고 하지만 그래서 너무 정말 진짜 조금 약간 이거 저는 제가 근데 해서 했어요 있어요 없어요 "
           "같아요 거예요 예요 이에요 합니다 입니다 에서 으로 하고 면서 니까 만큼 처럼 보다 부터 까지 "
           "이라 그게 것 수 때 좀 더 꽤 잘 안 못 왜 또 은 는 이 가 을 를 에 의 도 과 와 로 만 요 듯 중 후 전 및 거 게 데 써 봄 "
           "사용 제품 구매 생각 느낌 정도 그냥 약간 같은 많이 계속 다시 우선 일단 역시 완전 "
           "사용하기 사용했 구매했 구입 구입했 있어서 생각보다 기대돼 기대 처음 도착 도착했 배송 다음 이번 하나 "
           "좋았 좋아 같습니다 했습니다 받았 였어요 였습니다 거예 네요 어요 아요 해서요 아직 사용하".split())

GOOD = {"만족합니다", "만족", "매우만족", "좋아요"}
MID = {"보통", "보통입니다"}
BAD = {"별로예요", "만족하지 않습니다", "건조합니다", "별로", "불만족"}
SKIP_SAT = {"", "해당 없음", "없음", "정보 없음", "만족도 없음", "nan", "None"}


def clean_opt(s):
    if not isinstance(s, str):
        return None
    s = re.sub(r'^(선택\d*|색상\d*|옵션\d*)\s*[:：]\s*', '', s).strip()
    s = re.sub(r'\b(선택\d*|색상\d*|옵션\d*)\s*[:：]\s*', ' ', s)   # 다축 옵션의 2번째 접두 제거
    s = re.sub(r'\[[^\]]*\]\s*', '', s)
    s = re.sub(r'^\d+[.\s]*', '', s).strip()
    return s or None


def tokenize_ko(text):
    text = re.sub(r'[^가-힣a-zA-Z0-9\s]', ' ', str(text))
    out = []
    for tok in text.split():
        w = re.sub(r'(이에요|예요|에요|어요|아요|해요|네요|구요|는데|지만|으로|에서|에게|이라|라서|이고|'
                   r'하고|들이|들을|들은|들의|이|가|은|는|을|를|에|의|도|과|와|로|만|요)$', '', tok.strip())
        if len(w) < 2 or w in STOP or w.isdigit():
            continue
        out.append(w)
    return out


def sat_bucket(v):
    s = str(v).strip()
    if s in SKIP_SAT:
        return None
    if s in GOOD or ("만족" in s and "않" not in s) or "좋" in s:
        return "good"
    if s in MID or "보통" in s:
        return "mid"
    if s in BAD or "만족하지" in s or "별로" in s or "건조" in s:
        return "bad"
    return None


def agg_group(g):
    """한 그룹(제품 또는 브랜드)의 팩트 집계."""
    n = len(g)
    out = {"count": n, "avg": round(g["rating"].mean(), 2),
           "pos": int((g["rating"] >= 4).sum()), "neu": int((g["rating"] == 3).sum()),
           "neg": int((g["rating"] <= 2).sum())}
    out["pos_pct"] = round(out["pos"] / n * 100, 1)
    rc = g["rating"].value_counts().to_dict()
    out["rating_dist"] = [{"score": s, "count": int(rc.get(s, 0))} for s in [5, 4, 3, 2, 1]]
    # 월별 추이
    d = pd.to_datetime(g["review_date"], format="%Y.%m.%d", errors="coerce")
    ym = d.dt.strftime("%Y-%m")
    tc = ym.value_counts().sort_index()
    ta = g.assign(ym=ym).groupby("ym")["rating"].mean().round(2)
    out["trend"] = [{"month": k, "count": int(v), "avg": float(ta.get(k, 0))} for k, v in tc.items()]
    out["date_min"] = str(d.min().date()) if d.notna().any() else "-"
    out["date_max"] = str(d.max().date()) if d.notna().any() else "-"
    # 인기 옵션
    opt = g["option_kr"].dropna().map(clean_opt).dropna()
    out["opt_top"] = [{"label": k, "count": int(v)} for k, v in Counter(opt).most_common(15)]
    out["opt_n"] = int(g["option_kr"].nunique())
    # 세부 만족도
    sat = {}
    for axis, col in [("보습력", "moisturizing_kr"), ("텍스처", "texture_kr"), ("향", "scent_kr")]:
        b = Counter()
        for v in g[col].dropna():
            k = sat_bucket(v)
            if k:
                b[k] += 1
        sat[axis] = {"good": b["good"], "mid": b["mid"], "bad": b["bad"], "n": sum(b.values())}
    out["sat"] = sat
    # 키워드 (번역분)
    trans = g[g["review_content_kr"].notna() & (g["review_content_kr"].astype(str).str.strip() != "")]
    def kw(sub):
        c = Counter()
        for t in sub["review_content_kr"]:
            for w in set(tokenize_ko(t)):
                c[w] += 1
        return [{"kw": k, "count": v} for k, v in c.most_common(18)]
    out["kw_high"] = kw(trans[trans["rating"] >= 4])
    out["kw_low"] = kw(trans[trans["rating"] <= 3])
    out["trans_n"] = len(trans)
    out["high_n"] = int((trans["rating"] >= 4).sum())
    out["low_n"] = int((trans["rating"] <= 3).sum())
    # 리뷰 표본
    sample = []
    for _, r in trans.head(200).iterrows():
        sample.append({"rating": int(r["rating"]), "date": str(r["review_date"]),
                       "option": clean_opt(r.get("option_kr")) or "",
                       "kr": str(r["review_content_kr"])[:200],
                       "jp": str(r.get("review_content", ""))[:160]})
    out["sample"] = sample
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/review_result.csv")
    ap.add_argument("--map", default="config/product_map.json")
    ap.add_argument("--out", default="colorgram_wakemake_qoo10_dashboard.html")
    ap.add_argument("--template", default="templates/fact_dashboard_template.html")
    args = ap.parse_args()

    pmap = json.load(open(args.map, encoding="utf-8"))["products"]
    df = pd.read_csv(args.inp, encoding="utf-8-sig", low_memory=False)
    df = df[df["rating"].apply(lambda x: str(x).strip().isdigit())].copy()
    df["rating"] = df["rating"].astype(int)
    df["pid"] = df["product_id"].astype(str)
    df["brand"] = df["pid"].map(lambda p: pmap.get(p, {}).get("brand", "미상"))

    n = len(df)
    mapped = df[df["pid"].isin(pmap)]
    unmapped = sorted(set(df["pid"]) - set(pmap))

    # 제품별
    products = []
    for pid, meta in pmap.items():
        g = df[df["pid"] == pid]
        if not len(g):
            continue
        products.append({"id": pid, **{k: meta[k] for k in ("brand", "product", "category", "color", "url")},
                         **agg_group(g)})
    products.sort(key=lambda x: -x["count"])

    # 브랜드별
    brands = []
    bc = json.load(open(args.map, encoding="utf-8")).get("brand_colors", {})
    for bname, g in df[df["brand"] != "미상"].groupby("brand"):
        a = agg_group(g)
        brands.append({"name": bname, "color": bc.get(bname, "#64748b"),
                       "count": a["count"], "avg": a["avg"], "pos_pct": a["pos_pct"],
                       "products": sorted(set(g["pid"].map(lambda p: pmap.get(p, {}).get("product", p))))})
    brands.sort(key=lambda x: -x["count"])

    # 인사이트 (전부 데이터 도출)
    cats = " / ".join(dict.fromkeys(p["category"] for p in products))
    brand_summary = ", ".join(f"{b['name']} {len(b['products'])}종" for b in brands)
    insights = [
        f"이 데이터는 <b>{len(brands)}개 브랜드 · {len(products)}개 제품</b>, 총 <b>{n:,}건</b> 리뷰입니다 "
        f"({brand_summary} · 카테고리: {cats}).",
    ]
    for p in products:
        insights.append(f"<b>{p['brand']} {p['product']}</b> ({p['category']}): "
                        f"{p['count']:,}건 · 평균 <b>{p['avg']}점</b> · 긍정 {p['pos_pct']}% · "
                        f"고평점 키워드 {', '.join(k['kw'] for k in p['kw_high'][:3]) or '-'}")
    if unmapped:
        insights.append(f"⚠ 매핑되지 않은 product_id: {', '.join(unmapped)} (브랜드 미지정)")

    dash = {
        "meta": {"total": n, "mapped": len(mapped), "brand_count": len(brands),
                 "product_count": len(products), "avg": round(df["rating"].mean(), 2),
                 "categories": cats,
                 "date_min": str(pd.to_datetime(df['review_date'], format='%Y.%m.%d', errors='coerce').min().date()),
                 "date_max": str(pd.to_datetime(df['review_date'], format='%Y.%m.%d', errors='coerce').max().date()),
                 "unmapped": unmapped},
        "insights": insights, "brands": brands, "products": products,
    }

    tpl = open(args.template, encoding="utf-8").read()
    payload = json.dumps(dash, ensure_ascii=False)
    payload = (payload.replace("</", "<\\/").replace("<!--", "<\\!--")
                      .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    open(args.out, "w", encoding="utf-8").write(tpl.replace("/*__DASH_DATA__*/null", payload))
    print(f"완료 → {args.out}")
    print(f"  총 {n:,}건 · 브랜드 {len(brands)} · 제품 {len(products)}")
    for p in products:
        print(f"   - {p['brand']} {p['product']}: {p['count']:,}건 평균 {p['avg']} (번역 {p['trans_n']:,})")
    if unmapped:
        print("  매핑 안 됨:", unmapped)


if __name__ == "__main__":
    main()
