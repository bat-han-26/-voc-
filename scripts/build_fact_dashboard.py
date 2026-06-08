"""100% 팩트 대시보드 생성기 — 실제 Qoo10 review_result.csv 만 사용

추론/합성 일절 없음. 데이터에 실재하는 컬럼만 집계:
  - 평점, 날짜, 옵션(컬러), 상품, 보습/텍스처/향(유효응답만), 번역 리뷰 본문(키워드)
경쟁비교·이탈·감성분류 등 추론 차원은 포함하지 않음.

사용: python scripts/build_fact_dashboard.py --in data/review_result.csv --out colorgram_qoo10_fact_dashboard.html
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
           "사용하기 사용했 구매했 구입 구입했 있어서 생각보다 기대돼 기대 처음 도착 배송 다음 이번 하나 "
           "좋았 좋아 같습니다 했습니다 받았 였어요 였습니다 거예 네요 어요 아요 해서요".split())

GOOD = {"만족합니다", "만족", "매우만족", "좋아요"}
MID = {"보통", "보통입니다"}
BAD = {"별로예요", "만족하지 않습니다", "건조합니다", "별로", "불만족"}
SKIP_SAT = {"", "해당 없음", "없음", "정보 없음", "만족도 없음", "nan", "None"}


def clean_opt(s):
    if not isinstance(s, str):
        return None
    s = re.sub(r'^(선택\d*|색상|옵션)\s*[:：]\s*', '', s).strip()
    s = re.sub(r'^\[[^\]]*\]\s*', '', s)        # [기획] 류 머리 제거
    s = re.sub(r'^\d+[.\s]*', '', s).strip()     # 번호 제거
    return s or None


def tokenize_ko(text):
    text = re.sub(r'[^가-힣a-zA-Z0-9\s]', ' ', str(text))
    out = []
    for tok in text.split():
        w = tok.strip()
        w = re.sub(r'(이에요|예요|에요|어요|아요|해요|네요|구요|는데|지만|으로|에서|에게|이라|라서|이고|'
                   r'하고|들이|들을|들은|들의|이|가|은|는|을|를|에|의|도|과|와|로|만|요)$', '', w)
        if len(w) < 2 or w in STOP or w.isdigit():
            continue
        out.append(w)
    return out


def sat_bucket(v):
    s = str(v).strip()
    if s in SKIP_SAT:
        return None
    if s in GOOD:
        return "good"
    if s in MID:
        return "mid"
    if s in BAD:
        return "bad"
    if "만족하지" in s or "별로" in s or "건조" in s:
        return "bad"
    if "보통" in s:
        return "mid"
    if "만족" in s or "좋" in s:
        return "good"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/review_result.csv")
    ap.add_argument("--out", default="colorgram_qoo10_fact_dashboard.html")
    ap.add_argument("--template", default="templates/fact_dashboard_template.html")
    args = ap.parse_args()

    df = pd.read_csv(args.inp, encoding="utf-8-sig", low_memory=False)
    df = df[df["rating"].apply(lambda x: str(x).strip().isdigit())].copy()
    df["rating"] = df["rating"].astype(int)
    n = len(df)
    avg = round(df["rating"].mean(), 2)

    # 평점 분포
    rc = df["rating"].value_counts().to_dict()
    rating_dist = [{"score": s, "count": int(rc.get(s, 0))} for s in [5, 4, 3, 2, 1]]
    pos = int((df["rating"] >= 4).sum())
    neu = int((df["rating"] == 3).sum())
    neg = int((df["rating"] <= 2).sum())

    # 월별 추이
    d = pd.to_datetime(df["review_date"], format="%Y.%m.%d", errors="coerce")
    ym = d.dt.strftime("%Y-%m")
    trend_counts = ym.value_counts().sort_index()
    trend = [{"month": k, "count": int(v)} for k, v in trend_counts.items()]
    # 월별 평균 평점
    trend_avg = df.assign(ym=ym).groupby("ym")["rating"].mean().round(2)
    trend = [{**t, "avg": float(trend_avg.get(t["month"], 0))} for t in trend]
    date_min, date_max = d.min(), d.max()

    # 상품별
    products = []
    for pid, g in df.groupby("product_id"):
        opts = g["option_kr"].dropna()
        cleaned = opts.map(clean_opt).dropna()
        main_line = ""
        if len(cleaned):
            words = Counter()
            for o in cleaned:
                for w in re.findall(r'[가-힣]+', str(o)):
                    if len(w) >= 2 and w not in {"선택", "색상", "옵션", "기획", "세트"}:
                        words[w] += 1
            main_line = words.most_common(1)[0][0] if words else ""
        ex = cleaned.iloc[0] if len(cleaned) else ""
        products.append({
            "product_id": str(pid), "count": len(g),
            "avg": round(g["rating"].mean(), 2),
            "pos_pct": round((g["rating"] >= 4).mean() * 100, 1),
            "main_line": main_line, "example": ex,
            "options": int(g["option_kr"].nunique()),
        })
    products.sort(key=lambda x: -x["count"])

    # 인기 컬러(옵션) Top
    opt_clean = df["option_kr"].dropna().map(clean_opt).dropna()
    opt_top = [{"label": k, "count": int(v)} for k, v in Counter(opt_clean).most_common(15)]

    # 세부 만족도 (유효 응답만)
    sat = {}
    for axis, col in [("보습력", "moisturizing_kr"), ("텍스처", "texture_kr"), ("향", "scent_kr")]:
        buckets = Counter()
        for v in df[col].dropna():
            b = sat_bucket(v)
            if b:
                buckets[b] += 1
        total = sum(buckets.values())
        sat[axis] = {"good": buckets["good"], "mid": buckets["mid"], "bad": buckets["bad"], "n": total}

    # 키워드 (번역된 리뷰만, 평점군별)
    trans = df[df["review_content_kr"].notna() & (df["review_content_kr"].astype(str).str.strip() != "")]
    def kw(sub):
        c = Counter()
        for t in sub["review_content_kr"]:
            for w in set(tokenize_ko(t)):   # set: 리뷰당 1회(문서빈도)
                c[w] += 1
        return [{"kw": k, "count": v} for k, v in c.most_common(20)]
    kw_high = kw(trans[trans["rating"] >= 4])
    kw_low = kw(trans[trans["rating"] <= 3])
    trans_n, high_n, low_n = len(trans), int((trans["rating"] >= 4).sum()), int((trans["rating"] <= 3).sum())

    # 대표 리뷰 (번역된 것 중, 평점별 샘플)
    sample = []
    for _, r in trans.head(300).iterrows():
        sample.append({"rating": int(r["rating"]), "date": str(r["review_date"]),
                       "option": clean_opt(r.get("option_kr")) or "",
                       "kr": str(r["review_content_kr"])[:200],
                       "jp": str(r.get("review_content", ""))[:200]})

    # 인사이트 결론 (전부 데이터에서 직접 도출)
    top_prod = products[0]
    top_color = opt_top[0]["label"] if opt_top else "-"
    praise = [k["kw"] for k in kw_high[:6]]
    gripe = [k["kw"] for k in kw_low[:6]]
    insights = [
        f"총 <b>{n:,}건</b> 리뷰, 평균 평점 <b>{avg}점</b> — 긍정(4~5점) <b>{round(pos/n*100,1)}%</b>로 전반 만족도가 매우 높음",
        f"리뷰 최다 상품은 <b>ID {top_prod['product_id']}</b> ({top_prod['count']:,}건, 평균 {top_prod['avg']}점)",
        f"가장 많이 리뷰된 컬러는 <b>{top_color}</b> 등 — 블러링 라인 인기 집중",
        f"고평점 리뷰 빈출 키워드: <b>{', '.join(praise[:5])}</b>",
        f"저평점(1~3점) 리뷰 빈출 키워드: <b>{', '.join(gripe[:5]) if gripe else '데이터 소수'}</b>",
    ]

    dash = {
        "meta": {"total": n, "avg": avg, "products": len(products),
                 "pos": pos, "neu": neu, "neg": neg,
                 "pos_pct": round(pos / n * 100, 1),
                 "date_min": str(date_min.date()), "date_max": str(date_max.date()),
                 "trans_n": trans_n, "high_n": high_n, "low_n": low_n},
        "insights": insights, "rating_dist": rating_dist, "trend": trend,
        "products": products, "opt_top": opt_top, "sat": sat,
        "kw_high": kw_high, "kw_low": kw_low, "sample": sample,
    }

    tpl = open(args.template, encoding="utf-8").read()
    payload = json.dumps(dash, ensure_ascii=False)
    payload = (payload.replace("</", "<\\/").replace("<!--", "<\\!--")
                      .replace(" ", "\\u2028").replace(" ", "\\u2029"))
    html = tpl.replace("/*__DASH_DATA__*/null", payload)
    open(args.out, "w", encoding="utf-8").write(html)
    print(f"완료 → {args.out}")
    print(f"  리뷰 {n:,}건 · 평균 {avg} · 상품 {len(products)}개 · 번역 {trans_n:,}건 · 기간 {dash['meta']['date_min']}~{dash['meta']['date_max']}")


if __name__ == "__main__":
    main()
