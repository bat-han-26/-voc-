"""전체 파이프라인 오케스트레이터: 수집 → 강화 → 대시보드 생성

  python scripts/run_pipeline.py            # 1→2→3 전부
  python scripts/run_pipeline.py --skip-crawl   # 이미 수집된 raw 재사용
  python scripts/run_pipeline.py --no-enrich     # 번역/강화 없이(원문 기반 제한 분석)

환경변수: GEMINI_API_KEY (강화 단계 필요)
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(mod, *args):
    cmd = [sys.executable, os.path.join(HERE, mod), *args]
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"[중단] {mod} 실패 (exit {r.returncode})", file=sys.stderr)
        sys.exit(r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-crawl", action="store_true")
    ap.add_argument("--no-enrich", action="store_true")
    ap.add_argument("--config", default="config/products.json")
    args = ap.parse_args()

    if not args.skip_crawl:
        run("pipeline_1_crawl.py", "--config", args.config)
    if not args.no_enrich:
        if not os.environ.get("GEMINI_API_KEY"):
            print("경고: GEMINI_API_KEY 미설정 → 강화 단계 건너뜀(--no-enrich 와 동일)", file=sys.stderr)
        else:
            run("pipeline_2_enrich.py", "--config", args.config)
    run("pipeline_3_build.py", "--config", args.config)
    print("\n✅ 완료 → output/dashboard.html")


if __name__ == "__main__":
    main()
