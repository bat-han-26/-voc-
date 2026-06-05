"""Qoo10 일본 리뷰 크롤러

이 스크립트는 Qoo10 일본 상품의 리뷰를 수집하고, Gemini로 한국어 번역한 뒤
``review_result.csv`` 를 생성합니다. 생성된 CSV를 프로젝트 루트의 ``index.html``
대시보드에 드래그&드롭하면 실데이터 분석이 즉시 표시됩니다.

주의: 이 클라우드 실행 환경에서는 ``qoo10.jp`` 가 네트워크 허용목록에 없어
직접 실행이 차단됩니다. 로컬(또는 qoo10.jp 가 허용된 환경)에서 실행하세요.

의존 모듈(별도 제공 필요):
  - crawler.base.BaseCrawler   : output_path 저장 로직을 가진 베이스 크롤러
  - core.openai_client.ReviewAIClient : Gemini JSON 호출 클라이언트
  - config.settings.GEMINI_API_KEY
"""
import json
import os
import time
import queue
import threading
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from crawler.base import BaseCrawler
from core.openai_client import ReviewAIClient


class Qoo10Crawler(BaseCrawler):
    """Qoo10 일본 리뷰 크롤러"""

    COLUMN_MAP = {
        "review_content_jp": "review_content",
    }
    # review_id는 번역 후 translate_qoo10_reviews에서 생성

    def crawl(self, product_list: list) -> pd.DataFrame:
        """
        product_list: Qoo10 상품 URL 문자열 리스트
        예: ["https://www.qoo10.jp/g/998553409"]
        """
        all_reviews_data = []

        for p in product_list:
            r = requests.get(p)
            total_count = r.headers.get('Total_Count')
            pages = int(total_count) // 100 + 1 if total_count else 1

            path = urlparse(p).path
            item_id = path.split('/')[-1]

            for page_num in range(1, pages + 1):
                url = (
                    f'https://www.qoo10.jp/gmkt.inc/Goods/GoodsReviewAjaxAppend.aspx'
                    f'?gd_no={item_id}&group_code=2&page_no={page_num}&page_size=2000&sort_type=N'
                )
                print(f"Scraping page {page_num} of {pages}")

                try:
                    response = requests.post(url)
                    response.raise_for_status()

                    soup = BeautifulSoup(response.content, 'html.parser')
                    reviews = soup.find_all('li', recursive=False)

                    for review in reviews:
                        try:
                            score = review.select_one('.review_score .score').get_text(strip=True)
                        except AttributeError:
                            score = None

                        try:
                            user_id = review.select_one('.review_user_info span:nth-of-type(1)').get_text(strip=True)
                        except AttributeError:
                            user_id = None

                        try:
                            date = review.select_one('.review_user_info span:nth-of-type(2)').get_text(strip=True)
                        except AttributeError:
                            date = None

                        option_info = {}
                        try:
                            option_spans = review.select('.review_user_type span')
                            for span in option_spans:
                                text = span.get_text(strip=True)
                                if ':' in text:
                                    key, value = [x.strip() for x in text.split(':', 1)]
                                    option_info[key] = value
                        except AttributeError:
                            pass

                        eval_list = {}
                        try:
                            eval_items = review.select('.review_eval_list li')
                            for item in eval_items:
                                key = item.find('span').get_text(strip=True)
                                value = item.find('span').next_sibling.strip() if item.find('span').next_sibling else None
                                eval_list[key] = value
                        except AttributeError:
                            pass

                        try:
                            review_text = review.select_one('.review_txt').get_text(strip=True)
                        except AttributeError:
                            review_text = None

                        data = {
                            'product_id': item_id,
                            'rating': score,
                            'user_id': user_id,
                            'review_date': date,
                            'option': option_info.get('オプション', None),
                            'moisturizing': eval_list.get('保湿力', None),
                            'texture': eval_list.get('テクスチャー', None),
                            'scent': eval_list.get('香り', None),
                            'review_content_jp': review_text,
                        }
                        all_reviews_data.append(data)

                except requests.exceptions.RequestException as e:
                    print(f"Page {page_num} could not be retrieved. Error: {e}")

                time.sleep(0.5)

        df = pd.DataFrame(all_reviews_data)
        self.save(df)
        return df


def translate_qoo10_reviews(
    reviews_data: list,
    api_key: str,
    output_csv: str,
    thread_count: int = 80,
):
    """Qoo10 리뷰를 일본어에서 한국어로 번역합니다."""
    ai_client = ReviewAIClient(api_key=api_key, model="gemini-2.5-flash", max_retries=4)

    system_prompt = (
        "다음은 일본어 화장품 리뷰 데이터입니다. 이 데이터를 한국어로 자연스럽고 감성까지 반영되도록 번역해 주세요.\n"
        "각 항목의 의미를 정확하게 전달하면서도, 원문 리뷰의 어조, 감성, 뉘앙스를 최대한 그대로 유지하는 것이 매우 중요합니다.\n\n"
        "특히, 사용자가 직접 작성한 'review_content_jp'은 일본어 특유의 표현과 솔직한 감정이 한국어 사용자에게 자연스럽게 전달되도록 번역해 주세요.\n\n"
        "결과는 json 형태로 반환해주세요.\n"
        "아래는 예시입니다:\n\n"
        "{\n"
        '    "option": "타입: [진정 케어 & 모공 케어] 마그트리 비건 팩 클렌저 200ml",\n'
        '    "moisturizing": "만족합니다",\n'
        '    "texture": "만족합니다",\n'
        '    "scent": "만족합니다",\n'
        '    "review_content_jp": "J씨가 사용하는 걸 보고 궁금해서 구매했어요. 쓰는 게 기대돼요."\n'
        "}"
    )

    todo_q = queue.Queue()
    res_lock = threading.Lock()
    all_result = []

    for i, review in enumerate(reviews_data):
        todo_q.put((i, review))

    def worker():
        while True:
            try:
                seq, review = todo_q.get_nowait()
            except queue.Empty:
                return

            try:
                filtered = {k: v for k, v in review.items() if k not in ["product_id", "rating", "user_id", "review_date"]}
                lines = [f"{k}: {v}" for k, v in filtered.items()]
                pretty_review = "\n".join(lines)

                parsed = ai_client.call_json(
                    system_content=system_prompt,
                    user_content=f"리뷰 컨텐츠: {pretty_review}",
                    temperature=0.1,
                    max_tokens=2048,
                )

                combined = {}
                for k, v in review.items():
                    combined[k] = v
                    if k in parsed:
                        combined[f"{k}_kr"] = parsed[k]

                with res_lock:
                    all_result.append(combined)
                    print(f"[{threading.get_ident()}] {seq} 완료")

            except Exception as e:
                print(f"[{threading.get_ident()}] {seq} 에러: {e}")

            finally:
                todo_q.task_done()

    threads = []
    for _ in range(thread_count):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("전체 리뷰 번역 완료")

    df = pd.DataFrame(all_result)
    df['review_id'] = 'R' + (df.index + 1).astype(str).str.zfill(5)
    # 번역된 리뷰를 review_content로 매핑
    if 'review_content_jp_kr' in df.columns:
        df['review_content'] = df['review_content_jp_kr']
    elif 'review_content_jp' in df.columns:
        df['review_content'] = df['review_content_jp']
    df.to_csv(output_csv, index=False, encoding='utf-8-sig')
    return df


if __name__ == '__main__':
    from config.settings import GEMINI_API_KEY

    # ============================
    # 여기만 수정하세요
    # ============================
    OUTPUT_PATH = "./리뷰분석/콜로그램"
    PRODUCT_LIST = [
        # colorgram ティント 검색 결과의 상품 URL 들을 넣으세요.
        # 예) "https://www.qoo10.jp/g/1057459512",   # 탕후루 글라스 틴트
        #     "https://www.qoo10.jp/g/1135003693",   # 탕후루 글라스 틴트 (콜라보)
        #     "https://www.qoo10.jp/g/1036494829",   # 쥬시 드롭 틴트
        "https://www.qoo10.jp/g/998553409",
    ]
    TRANSLATE_THREAD_COUNT = 80
    # ============================

    crawler = Qoo10Crawler(output_path=OUTPUT_PATH)
    df = crawler.crawl(product_list=PRODUCT_LIST)

    translate_qoo10_reviews(
        reviews_data=df.to_dict('records'),
        api_key=GEMINI_API_KEY,
        output_csv=os.path.join(OUTPUT_PATH, "review_result.csv"),
        thread_count=TRANSLATE_THREAD_COUNT,
    )
