"""범용 VOC 대시보드 빌더 (설정 기반) — 어떤 브랜드/카테고리든 동일 포맷 생성

build_fact_dashboard.py(일본 Qoo10 전용)를 일반화한 버전.
config(JSON)에서 products / concepts(니즈 사전) / satisfaction(평가축) 을 읽고,
제품별 전략 브리프를 데이터에서 자동 생성한다. (수동 작성 불필요)

사용:
  python scripts/build_voc_dashboard.py \
      --in data/oliveyoung_reviews.csv --config config/oliveyoung_products.json \
      --out oliveyoung_allthebetter_dashboard.html

입력 CSV 표준 컬럼: product_id, rating, review_date, option(opt), review_content_kr
(번역이 없으면 review_content 사용). brand/product/category는 config의 products 매핑 우선.
"""
import argparse
import json
import re
from collections import Counter

import pandas as pd

STOP = set("그리고 하지만 그래서 너무 정말 진짜 조금 약간 이거 저는 제가 근데 해서 했어요 있어요 없어요 "
           "같아요 거예요 예요 이에요 합니다 입니다 에서 으로 하고 면서 니까 만큼 처럼 보다 부터 까지 "
           "이라 그게 것 수 때 좀 더 꽤 잘 안 못 왜 또 은 는 이 가 을 를 에 의 도 과 와 로 만 요 듯 중 후 전 및 거 게 데 써 봄 "
           "사용 제품 구매 생각 느낌 정도 그냥 같은 많이 계속 다시 우선 일단 역시 완전 "
           "사용하기 사용했 구매했 구입 구입했 있어서 생각보다 기대돼 기대 처음 도착 도착했 배송 다음 이번 하나 "
           "좋았 좋아 같습니다 했습니다 받았 였어요 였습니다 거예 네요 어요 아요 해서요 아직 사용하".split())


def clean_opt(s):
    if not isinstance(s, str):
        return None
    s = re.sub(r'^(선택\d*|색상\d*|옵션\d*)\s*[:：]\s*', '', s).strip()
    s = re.sub(r'\b(선택\d*|색상\d*|옵션\d*)\s*[:：]\s*', ' ', s)
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
    if s in ("", "해당 없음", "없음", "정보 없음", "nan", "None"):
        return None
    if "만족하지" in s or "별로" in s or "건조" in s or "불만" in s:
        return "bad"
    if "보통" in s:
        return "mid"
    if "만족" in s or "좋" in s:
        return "good"
    return None


def text_col(g):
    """한국어 본문 컬럼 선택 (review_content_kr 우선, 없으면 review_content)."""
    if "review_content_kr" in g and g["review_content_kr"].notna().any():
        return g["review_content_kr"].fillna(g.get("review_content", "")).astype(str)
    return g.get("review_content", pd.Series([""] * len(g))).fillna("").astype(str)


def agg_group(g, concepts, sat_axes):
    n = len(g)
    out = {"count": n, "avg": round(g["rating"].mean(), 2),
           "pos": int((g["rating"] >= 4).sum()), "neu": int((g["rating"] == 3).sum()),
           "neg": int((g["rating"] <= 2).sum())}
    out["pos_pct"] = round(out["pos"] / n * 100, 1)
    rc = g["rating"].value_counts().to_dict()
    out["rating_dist"] = [{"score": s, "count": int(rc.get(s, 0))} for s in [5, 4, 3, 2, 1]]
    d = pd.to_datetime(g["review_date"], format="%Y.%m.%d", errors="coerce")
    ym = d.dt.strftime("%Y-%m")
    tc = ym.value_counts().sort_index()
    ta = g.assign(ym=ym).groupby("ym")["rating"].mean().round(2)
    out["trend"] = [{"month": k, "count": int(v), "avg": float(ta.get(k, 0))} for k, v in tc.items()]
    out["date_min"] = str(d.min().date()) if d.notna().any() else "-"
    out["date_max"] = str(d.max().date()) if d.notna().any() else "-"

    opt_series = g["option_kr"] if "option_kr" in g else g.get("option", pd.Series([None] * len(g)))
    opt = opt_series.dropna().map(clean_opt).dropna()
    out["opt_top"] = [{"label": k, "count": int(v)} for k, v in Counter(opt).most_common(15)]
    out["opt_n"] = int(opt_series.nunique())

    # 만족도 (설정된 축이 CSV에 있을 때만)
    sat = {}
    for ax in sat_axes:
        col = ax.get("col")
        if col and col in g:
            b = Counter()
            for v in g[col].dropna():
                k = sat_bucket(v)
                if k:
                    b[k] += 1
            if sum(b.values()):
                sat[ax["label"]] = {"good": b["good"], "mid": b["mid"], "bad": b["bad"], "n": sum(b.values())}
    out["sat"] = sat

    txt = text_col(g)
    trans_mask = txt.str.strip() != ""
    trans = g[trans_mask.values]
    ttxt = txt[trans_mask.values]

    def kw(mask):
        c = Counter()
        for t in ttxt[mask.values]:
            for w in set(tokenize_ko(t)):
                c[w] += 1
        return [{"kw": k, "count": v} for k, v in c.most_common(18)]
    hi = trans["rating"] >= 4
    lo = trans["rating"] <= 3
    out["kw_high"] = kw(hi)
    out["kw_low"] = kw(lo)
    out["trans_n"] = int(len(trans))
    out["high_n"] = int((trans["rating"] >= 4).sum())
    out["low_n"] = int((trans["rating"] <= 3).sum())

    # 니즈(개념) — 본문 전체 기준
    gn = max(1, len(g))
    needs = []
    for c, kws in concepts.items():
        hit = int(txt.apply(lambda t: any(k in t for k in kws)).sum())
        if hit:
            needs.append({"need": c, "count": hit, "pct": round(hit / gn * 100, 1)})
    needs.sort(key=lambda x: -x["count"])
    out["needs"] = needs[:12]
    out["needs_basis"] = len(g)

    # 변별 키워드 + 대표 인용
    def docs(sub_txt):
        return [set(tokenize_ko(t)) for t in sub_txt]
    hi_docs = docs(ttxt[hi.values])
    lo_src = ttxt[(trans["rating"] <= 2).values]
    if len(lo_src) < 8:
        lo_src = ttxt[lo.values]
    lo_docs = docs(lo_src)

    def dfreq(ds):
        c = Counter()
        for s in ds:
            c.update(s)
        return c
    hf, lf = dfreq(hi_docs), dfreq(lo_docs)
    H, L = max(1, len(hi_docs)), max(1, len(lo_docs))
    pros = sorted([w for w in hf if hf[w] >= max(5, H * 0.02)],
                  key=lambda w: hf[w] / H - lf.get(w, 0) / L, reverse=True)[:6]
    cons_noise = {"상품", "자체", "생각했던", "저한테", "그것", "부분", "느낌"}
    cons = [w for w in sorted([w for w in lf if lf[w] >= max(2, L * 0.03)],
                              key=lambda w: lf[w] / L - hf.get(w, 0) / H, reverse=True)
            if w not in set(pros) and w not in cons_noise][:6]

    def pick(sub_txt, lo_len, hi_len):
        cand = [t.strip() for t in sub_txt if lo_len <= len(t.strip()) <= hi_len]
        cand.sort(key=len, reverse=True)
        return cand[0] if cand else None
    pos_quote = pick(ttxt[(trans["rating"] >= 5).values], 18, 100)
    neg_quote = pick(ttxt[(trans["rating"] <= 2).values], 12, 120)

    PH_NOISE = ("기대돼", "아직", "전이지만", "사용하는", "생각에", "샀어요", "구매했어요", "왔어요", "와서")
    bi = Counter()
    for t in ttxt:
        ws = [w for w in re.sub(r'[^가-힣\s]', ' ', t).split() if len(w) >= 2 and w not in STOP]
        for a, b in zip(ws, ws[1:]):
            ph = a + " " + b
            if not any(x in ph for x in PH_NOISE):
                bi[ph] += 1
    phrases = [p for p, c in bi.most_common(10)]

    peak = max(out["trend"], key=lambda t: t["count"]) if out["trend"] else None
    out["voc"] = {"pros": pros or [k["kw"] for k in out["kw_high"][:5]], "cons": cons,
                  "top_option": out["opt_top"][0]["label"] if out["opt_top"] else "-",
                  "peak": f"{peak['month']} ({peak['count']:,}건)" if peak else "-",
                  "pos_quote": pos_quote, "neg_quote": neg_quote,
                  "neg_sample_n": int((trans["rating"] <= 2).sum())}

    # ---- 전략 브리프 자동 생성 (데이터 기반) ----
    needs_top = [x["need"] for x in out["needs"][:3]]
    ops = [w for w in cons if any(k in w for k in ("배송", "느", "늦", "품절", "재고", "포장", "가격", "비싸"))]
    out["strategy"] = {
        "conclusion": [
            f"핵심 관심사는 <b>{' · '.join(needs_top) if needs_top else '-'}</b>. "
            f"강점 키워드는 <b>{', '.join(pros[:3]) or '-'}</b>로, 만족의 실제 동인이 여기에 집중됨.",
            (f"개선/우려 포인트는 <b>{', '.join(cons[:3]) or '표본 적음'}</b>."
             + (" 일부는 제품이 아니라 <b>배송·가격 등 운영 이슈</b>로, CX로 방어 가능." if ops else "")),
        ],
        "ugc": [
            f"<b>'{needs_top[0] if needs_top else '핵심 효용'}'</b> 중심의 실사용 데모/비포애프터 콘텐츠 — 가장 많이 언급되는 니즈를 정면으로.",
            f"강점 키워드 <b>'{(pros[:1] or ['장점'])[0]}'</b>를 후킹 카피로 한 마이크로 인플루언서 솔직 리뷰.",
            f"자주 나오는 우려 <b>'{(cons[:1] or ['-'])[0]}'</b>를 Q&A/사용팁으로 선제 해소하는 신뢰형 콘텐츠.",
        ],
        "language": (phrases[:4] + pros[:2])[:6],
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--template", default="templates/fact_dashboard_template.html")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    pmap = cfg["products"]
    concepts = cfg.get("concepts", {})
    sat_axes = cfg.get("satisfaction", [])
    bc = cfg.get("brand_colors", {})

    df = pd.read_csv(args.inp, encoding="utf-8-sig", low_memory=False)
    df = df[df["rating"].apply(lambda x: str(x).strip().replace('.0', '').isdigit())].copy()
    df["rating"] = df["rating"].astype(float).astype(int)
    df["pid"] = df["product_id"].astype(str)
    df["brand"] = df["pid"].map(lambda p: pmap.get(p, {}).get("brand", "미상"))

    n = len(df)
    products = []
    for pid, meta in pmap.items():
        g = df[df["pid"] == pid]
        if not len(g):
            continue
        products.append({"id": pid, **{k: meta.get(k, "") for k in ("brand", "product", "category", "color", "url")},
                         **agg_group(g, concepts, sat_axes)})
    products.sort(key=lambda x: -x["count"])

    brands = []
    for bname, g in df[df["brand"] != "미상"].groupby("brand"):
        a = agg_group(g, concepts, sat_axes)
        brands.append({"name": bname, "color": bc.get(bname, "#64748b"),
                       "count": a["count"], "avg": a["avg"], "pos_pct": a["pos_pct"],
                       "products": sorted(set(g["pid"].map(lambda p: pmap.get(p, {}).get("product", p))))})
    brands.sort(key=lambda x: -x["count"])

    cats = " / ".join(dict.fromkeys(p["category"] for p in products))
    bsum = ", ".join(f"{b['name']} {len(b['products'])}종" for b in brands)
    insights = [f"이 데이터는 <b>{len(brands)}개 브랜드 · {len(products)}개 제품</b>, 총 <b>{n:,}건</b> 리뷰입니다 ({bsum} · 카테고리: {cats})."]
    for p in products:
        insights.append(f"<b>{p['brand']} {p['product']}</b> ({p['category']}): {p['count']:,}건 · 평균 <b>{p['avg']}점</b> · 긍정 {p['pos_pct']}% · "
                        f"강점 {', '.join(p['voc']['pros'][:3]) or '-'}")
    overall = [
        "<b>공통 결론</b>: 각 제품의 만족 동인은 상단 '핵심 니즈'와 '강점 키워드'에 집중됨 — 콘텐츠 메시지를 이 순서로 우선 배치.",
        "<b>저평점의 성격</b>: 제품 본질 vs 운영(배송·가격) 이슈를 구분해, 운영 이슈는 CX/물류 커뮤니케이션으로 분리 대응.",
        "<b>UGC 원칙</b>: 아래 '고객 실제 언어'를 카피로 그대로 차용하고, 자주 나오는 우려를 솔직하게 다루는 마이크로 인플루언서가 신뢰·전환에 유리.",
    ]

    dash = {
        "meta": {"total": n, "mapped": n, "brand_count": len(brands), "product_count": len(products),
                 "avg": round(df["rating"].mean(), 2), "categories": cats,
                 "date_min": str(pd.to_datetime(df['review_date'], format='%Y.%m.%d', errors='coerce').min().date()),
                 "date_max": str(pd.to_datetime(df['review_date'], format='%Y.%m.%d', errors='coerce').max().date()),
                 "unmapped": sorted(set(df["pid"]) - set(pmap))},
        "insights": insights, "overall": overall, "brands": brands, "products": products,
    }
    tpl = open(args.template, encoding="utf-8").read()
    payload = json.dumps(dash, ensure_ascii=False)
    payload = (payload.replace("</", "<\\/").replace("<!--", "<\\!--")
                      .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    open(args.out, "w", encoding="utf-8").write(tpl.replace("/*__DASH_DATA__*/null", payload))
    print(f"완료 → {args.out}")
    print(f"  총 {n:,}건 · 브랜드 {len(brands)} · 제품 {len(products)}")
    for p in products:
        print(f"   - {p['brand']} {p['product']}: {p['count']:,}건 평균 {p['avg']}")


if __name__ == "__main__":
    main()
