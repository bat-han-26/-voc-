"""[3단계] 집계 + 대시보드 생성

data/reviews_enriched.jsonl 을 읽어 차트용 데이터로 집계하고,
templates/dashboard_template.html 에 주입해 output/dashboard.html 을 만듭니다.

사용:
  python scripts/pipeline_3_build.py \
      --in data/reviews_enriched.jsonl --config config/products.json \
      --template templates/dashboard_template.html --out output/dashboard.html
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def as_list(v):
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if v in (None, "", "none", "null", "모름"):
        return []
    return [str(v)]


def top_keywords(items, n=10, with_samples=True):
    cnt = Counter()
    samp = defaultdict(list)
    for kws, rating, text in items:
        for kw in kws:
            cnt[kw] += 1
            if with_samples and len(samp[kw]) < 4 and text:
                samp[kw].append({"rating": rating, "text": text[:140]})
    out = []
    for kw, c in cnt.most_common(n):
        out.append({"kw": kw, "count": c, "samples": samp.get(kw, [])})
    return out


def pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


def build(rows, cfg):
    brand_meta = {b["name"]: b for b in cfg["brands"]}
    our = next((b["name"] for b in cfg["brands"] if b.get("is_ours")), None)
    by_brand = defaultdict(list)
    for r in rows:
        by_brand[r.get("brand")].append(r)

    # ---- 브랜드별 핵심 지표 ----
    brands = []
    for b in cfg["brands"]:
        rs = by_brand.get(b["name"], [])
        if not rs:
            continue
        rated = [x for x in rs if x.get("rating")]
        avg = round(sum(x["rating"] for x in rated) / len(rated), 2) if rated else 0
        pos = sum(1 for x in rs if x.get("sentiment") == "positive")
        rep = sum(1 for x in rs if x.get("repurchase_intent") == "repurchase")
        chu = sum(1 for x in rs if x.get("repurchase_intent") == "churn")
        brands.append({
            "name": b["name"], "color": b["color"], "is_ours": b.get("is_ours", False),
            "review_count": len(rs), "avg_rating": avg,
            "pos_pct": pct(pos, len(rs)),
            "repurchase_pct": pct(rep, len(rs)),
            "churn_pct": pct(chu, len(rs)),
        })

    # ---- 포지셔닝: 지속력(장점에 지속/지속력 언급률) × 자극 순점수(자극 단점 - 무자극 장점) ----
    positioning = []
    for b in brands:
        rs = by_brand[b["name"]]
        persist = pct(sum(1 for x in rs if any("지속" in k or "오래" in k for k in as_list(x.get("pros_keywords")))), len(rs))
        irr_neg = sum(1 for x in rs if any(("자극" in k or "건조" in k) for k in as_list(x.get("cons_keywords"))))
        irr_pos = sum(1 for x in rs if any(("순한" in k or "촉촉" in k or "보습" in k) for k in as_list(x.get("pros_keywords"))))
        positioning.append({
            "brand": b["name"], "color": b["color"],
            "x": persist, "y": round(pct(irr_pos, len(rs)) - pct(irr_neg, len(rs)), 1),
            "size": b["review_count"],
        })

    # ---- 장단점 키워드 (브랜드별) ----
    pros_by_brand, cons_by_brand = {}, {}
    for b in brands:
        rs = by_brand[b["name"]]
        pros_by_brand[b["name"]] = top_keywords(
            [(as_list(x.get("pros_keywords")), x.get("rating"), x.get("review_kr") or x.get("review_content_jp")) for x in rs])
        cons_by_brand[b["name"]] = top_keywords(
            [(as_list(x.get("cons_keywords")), x.get("rating"), x.get("review_kr") or x.get("review_content_jp")) for x in rs])

    # ---- 분포형 차원 헬퍼 ----
    def dist(field, listlike=False):
        cnt = Counter()
        samp = defaultdict(list)
        total = 0
        for x in rows:
            vals = as_list(x.get(field)) if listlike else ([x.get(field)] if x.get(field) and x.get(field) not in ("none", "모름", "neutral") else [])
            for v in vals:
                cnt[v] += 1
                total += 1
                if len(samp[v]) < 3:
                    t = x.get("review_kr") or x.get("review_content_jp")
                    if t:
                        samp[v].append({"brand": x.get("brand"), "rating": x.get("rating"), "text": t[:120]})
        return [{"label": k, "count": c, "pct": pct(c, total), "samples": samp[k]} for k, c in cnt.most_common(12)]

    usage = dist("usage_situation", listlike=True)
    emotion = dist("emotion", listlike=True)
    decision = dist("purchase_decision", listlike=True)
    motive = dist("purchase_motive")

    # ---- 입술타입 분포 (브랜드별 stacked) ----
    lt_labels = cfg["dimensions"]["lip_type"]
    lt_by_brand = {}
    for b in brands:
        c = Counter(x.get("lip_type") for x in by_brand[b["name"]] if x.get("lip_type") in lt_labels)
        lt_by_brand[b["name"]] = [c.get(l, 0) for l in lt_labels]

    # ---- 재구매 vs 이탈 ----
    retention = []
    for b in brands:
        rs = by_brand[b["name"]]
        rep = sum(1 for x in rs if x.get("repurchase_intent") == "repurchase")
        chu = sum(1 for x in rs if x.get("repurchase_intent") == "churn")
        neu = len(rs) - rep - chu
        retention.append({"brand": b["name"], "color": b["color"], "repurchase": rep,
                          "churn": chu, "neutral": neu,
                          "ratio": round(rep / chu, 1) if chu else rep})

    # ---- 이탈 사유 히트맵 (브랜드 x 사유) ----
    reasons = cfg["dimensions"]["churn_reason"]
    churn_matrix = []
    for b in brands:
        rs = by_brand[b["name"]]
        c = Counter(x.get("churn_reason") for x in rs if x.get("churn_reason") in reasons)
        churn_matrix.append({"brand": b["name"], "counts": [c.get(r, 0) for r in reasons]})

    # ---- 브랜드 이동(전환) ----
    mig = Counter()
    mig_ex = defaultdict(list)
    for x in rows:
        frm, to = x.get("switch_from"), x.get("switch_to")
        if frm or to:
            pat = f"{frm or '?'} → {to or x.get('brand') or '?'}"
            mig[pat] += 1
            if len(mig_ex[pat]) < 3:
                t = x.get("review_kr") or x.get("review_content_jp")
                if t:
                    mig_ex[pat].append({"brand": x.get("brand"), "text": t[:140]})
    migration = [{"pattern": p, "count": c, "examples": mig_ex[p]} for p, c in mig.most_common(10)]

    # ---- Verbatim (감성 테마별 카드) ----
    themes = [
        ("positive", "😍", "강한 만족", "var(--pos)"),
        ("negative", "😞", "불만/이탈 위험", "var(--neg)"),
        ("neutral", "🤔", "중립/관망", "var(--accent)"),
    ]
    verbatim = []
    for sent, emoji, title, color in themes:
        qs = [x for x in rows if x.get("sentiment") == sent and x.get("verbatim_worthy")]
        if not qs:
            qs = [x for x in rows if x.get("sentiment") == sent]
        quotes = []
        for x in qs[:8]:
            t = x.get("review_kr") or x.get("review_content_jp")
            if t:
                quotes.append({"brand": x.get("brand"), "rating": x.get("rating"), "text": t[:180]})
        verbatim.append({"emoji": emoji, "title": title, "color": color,
                        "count": len(qs), "quotes": quotes})

    # ---- 상단 인사이트 결론 (자동 생성) ----
    insights = make_insights(brands, our, pros_by_brand, cons_by_brand, retention, churn_matrix, reasons)

    total = len(rows)
    rated_all = [x for x in rows if x.get("rating")]
    return {
        "meta": {
            "keyword": cfg.get("keyword", ""),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_reviews": total,
            "brand_count": len(brands),
            "avg_rating": round(sum(x["rating"] for x in rated_all) / len(rated_all), 2) if rated_all else 0,
            "our_brand": our,
        },
        "brands": brands,
        "insights": insights,
        "positioning": positioning,
        "pros_by_brand": pros_by_brand,
        "cons_by_brand": cons_by_brand,
        "lip_type": {"labels": lt_labels, "by_brand": lt_by_brand},
        "usage": usage, "emotion": emotion, "decision": decision, "motive": motive,
        "retention": retention,
        "churn": {"reasons": reasons, "matrix": churn_matrix},
        "migration": migration,
        "verbatim": verbatim,
    }


def make_insights(brands, our, pros, cons, retention, churn_matrix, reasons):
    ours = next((b for b in brands if b["name"] == our), None)
    comp = [b for b in brands if b["name"] != our]
    strengths, weaknesses, opps = [], [], []
    verdict = "데이터가 부족합니다. 크롤링/강화 단계를 먼저 실행하세요."
    if ours and comp:
        avg_comp = sum(b["avg_rating"] for b in comp) / len(comp)
        avg_comp_rep = sum(b["repurchase_pct"] for b in comp) / len(comp)
        rank = sorted(brands, key=lambda b: -b["avg_rating"]).index(ours) + 1
        verdict = (f"<b>{our}</b>는 평균 평점 <b>{ours['avg_rating']}점</b>으로 "
                   f"분석 {len(brands)}개 브랜드 중 <b>{rank}위</b>입니다 "
                   f"(경쟁 평균 {round(avg_comp,2)}점). 재구매 의향 <b>{ours['repurchase_pct']}%</b>, "
                   f"이탈 시그널 <b>{ours['churn_pct']}%</b>. "
                   f"아래 포지셔닝·이탈사유·키워드 분석으로 다음 소재 방향을 도출합니다.")
        top_pro = (pros.get(our) or [{}])[0].get("kw")
        if top_pro:
            strengths.append(f"최대 강점 키워드: <b>{top_pro}</b> — 마케팅 메인 카피로 활용")
        if ours["repurchase_pct"] >= avg_comp_rep:
            strengths.append(f"재구매 의향 {ours['repurchase_pct']}% — 경쟁 평균({round(avg_comp_rep,1)}%) 이상, 충성 기반 확보")
        else:
            weaknesses.append(f"재구매 의향 {ours['repurchase_pct']}% — 경쟁 평균({round(avg_comp_rep,1)}%) 대비 낮음, 충성도 강화 필요")
        top_con = (cons.get(our) or [{}])[0].get("kw")
        if top_con:
            weaknesses.append(f"최다 단점 키워드: <b>{top_con}</b> — 제품/소구 개선 1순위")
        # 이탈 사유 1위
        for cm in churn_matrix:
            if cm["brand"] == our and any(cm["counts"]):
                idx = cm["counts"].index(max(cm["counts"]))
                weaknesses.append(f"이탈 사유 1위: <b>{reasons[idx]}</b> — 이탈 방어 메시지 필요")
                break
        # 경쟁사 약점 = 우리 기회
        for b in comp:
            tc = (cons.get(b["name"]) or [{}])[0].get("kw")
            if tc:
                opps.append(f"{b['name']}의 약점 <b>{tc}</b> 공략 — 비교 소구 기회")
    return {"verdict": verdict, "our_strengths": strengths or ["(데이터 강화 후 자동 생성)"],
            "our_weaknesses": weaknesses or ["(데이터 강화 후 자동 생성)"],
            "opportunities": opps or ["(데이터 강화 후 자동 생성)"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/reviews_enriched.jsonl")
    ap.add_argument("--config", default="config/products.json")
    ap.add_argument("--template", default="templates/dashboard_template.html")
    ap.add_argument("--out", default="output/dashboard.html")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    rows = load(args.inp)
    print(f"강화 리뷰 {len(rows)}건 로드")
    dash = build(rows, cfg)

    with open(args.template, encoding="utf-8") as f:
        tpl = f.read()
    payload = json.dumps(dash, ensure_ascii=False)
    # 인라인 <script> 안전: 리뷰 본문에 </script>, <!-- 등이 있어도 태그를 조기
    # 종료시키지 못하도록, JS 문자열을 깨는 U+2028/U+2029 도 함께 이스케이프.
    payload = (payload.replace("</", "<\\/")
                      .replace("<!--", "<\\!--")
                      .replace("\u2028", "\\u2028")
                      .replace("\u2029", "\\u2029"))
    html = tpl.replace("/*__DASH_DATA__*/null", payload)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[3단계 완료] → {args.out}  (총 {dash['meta']['total_reviews']}건, "
          f"{dash['meta']['brand_count']}개 브랜드)")


if __name__ == "__main__":
    main()
