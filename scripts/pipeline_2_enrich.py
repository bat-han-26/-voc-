"""[2단계] 리뷰 LLM 강화(분류·태깅)  — 레퍼런스급 대시보드의 핵심

각 리뷰를 Gemini로 다차원 분류해 data/reviews_enriched.jsonl 로 저장합니다.
일본어 원문을 한국어로 번역하면서, 동시에 구조화된 차원으로 태깅합니다.

특징:
  - 멀티스레드 + 재시도
  - 재개 가능(resumable): 이미 처리된 (brand, product_id, idx) 는 건너뜀
  - 외부 의존성 없음(requests 만 사용, Gemini REST 호출)

환경변수: GEMINI_API_KEY (필수)
네트워크 허용목록: generativelanguage.googleapis.com

사용:
  export GEMINI_API_KEY=...
  python scripts/pipeline_2_enrich.py --in data/reviews_raw.csv --out data/reviews_enriched.jsonl
"""
import argparse
import csv
import json
import os
import queue
import sys
import threading
import time

import requests

MODEL = "gemini-2.5-flash"
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


def build_prompt(dims):
    return (
        "당신은 화장품(립 틴트) VOC 분석 전문가입니다. 아래 일본어 리뷰 1건을 분석해 "
        "한국어로 번역하고, 정해진 차원으로 분류하세요. 반드시 JSON 하나만 반환합니다.\n\n"
        "출력 JSON 스키마:\n"
        "{\n"
        '  "review_kr": "리뷰 본문 한국어 번역",\n'
        '  "summary": "한 줄 핵심 요약(한국어)",\n'
        '  "sentiment": "positive | neutral | negative",\n'
        f'  "lip_type": "{ " | ".join(dims["lip_type"]) } | 모름",\n'
        f'  "usage_situation": ["{dims["usage_situation"][0]}" 등 해당 태그 배열(없으면 [])],\n'
        '  "emotion": ["만족","설렘","실망","놀람","후회","신뢰" 중 해당 배열],\n'
        f'  "purchase_decision": ["{dims["purchase_decision"][0]}" 등 해당 태그 배열],\n'
        f'  "purchase_motive": "{ " | ".join(dims["purchase_motive"]) } | 모름",\n'
        '  "repurchase_intent": "repurchase | churn | neutral",\n'
        f'  "churn_reason": "{ " | ".join(dims["churn_reason"]) } | none",\n'
        '  "switch_from": "이전에 쓰던 브랜드/제품명(언급 시) | null",\n'
        '  "switch_to": "갈아타려는 브랜드/제품명(언급 시) | null",\n'
        '  "pros_keywords": ["장점 키워드 1~3개(한국어, 예: 윤기/지속력/촉촉함/발색/패키지/가성비)"],\n'
        '  "cons_keywords": ["단점 키워드 0~3개(한국어, 예: 건조/자극/색빠짐/끈적임/향)"],\n'
        '  "verbatim_worthy": true/false\n'
        "}\n\n"
        "규칙: 추측하지 말고 리뷰에 드러난 내용만 태깅. 해당 없으면 빈 배열/none/null/모름 사용. "
        "키워드는 짧은 명사형 한국어로 정규화."
    )


def call_gemini(prompt, review_text, api_key, max_retries=4):
    payload = {
        "system_instruction": {"parts": [{"text": prompt}]},
        "contents": [{"parts": [{"text": f"리뷰(일본어): {review_text}"}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json",
                             "maxOutputTokens": 1024},
    }
    url = API.format(model=MODEL, key=api_key)
    last = None
    for attempt in range(max_retries):
        try:
            r = requests.post(url, json=payload, timeout=60)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(txt)
        except Exception as e:  # noqa
            last = e
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Gemini 실패: {last}")


def key_of(row):
    return f"{row.get('brand')}|{row.get('product_id')}|{row.get('_idx')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/reviews_raw.csv")
    ap.add_argument("--out", default="data/reviews_enriched.jsonl")
    ap.add_argument("--config", default="config/products.json")
    ap.add_argument("--threads", type=int, default=16)
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    with open(args.config, encoding="utf-8") as f:
        dims = json.load(f)["dimensions"]
    prompt = build_prompt(dims)

    with open(args.inp, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for i, row in enumerate(rows):
        row["_idx"] = i

    # resume: 이미 처리된 키 로드
    done = set()
    if os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["_key"])
                except Exception:  # noqa
                    pass
    todo = [r for r in rows if key_of(r) not in done]
    print(f"총 {len(rows)}건 중 처리 대상 {len(todo)}건 (완료 {len(done)}건 건너뜀)")

    q = queue.Queue()
    for r in todo:
        q.put(r)
    lock = threading.Lock()
    out_f = open(args.out, "a", encoding="utf-8")
    counter = {"n": 0}

    def worker():
        while True:
            try:
                row = q.get_nowait()
            except queue.Empty:
                return
            text = (row.get("review_content_jp") or "").strip()
            try:
                enr = call_gemini(prompt, text, api_key) if text else {}
            except Exception as e:  # noqa
                enr = {"_error": str(e)}
            rec = {
                "_key": key_of(row),
                "brand": row.get("brand"),
                "product_id": row.get("product_id"),
                "rating": int(row.get("rating") or 0) if (row.get("rating") or "").strip().isdigit() else None,
                "review_date": row.get("review_date"),
                "option": row.get("option"),
                "moisturizing": row.get("moisturizing"),
                "texture": row.get("texture"),
                "scent": row.get("scent"),
                "review_content_jp": text,
                **enr,
            }
            with lock:
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out_f.flush()
                counter["n"] += 1
                if counter["n"] % 20 == 0:
                    print(f"  강화 {counter['n']}/{len(todo)}")
            q.task_done()

    threads = [threading.Thread(target=worker) for _ in range(args.threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out_f.close()
    print(f"\n[2단계 완료] 강화 {counter['n']}건 → {args.out}")


if __name__ == "__main__":
    main()
