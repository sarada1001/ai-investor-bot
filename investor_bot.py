# 依頼事項
現在、ボットがS&P500の銘柄から取引対象（ターゲット銘柄）をどのように絞り込んでいるのか調査し、そのアーキテクチャを「開場中に毎時間、動的にスクリーニングし直す」仕様（Intraday Dynamic Screening）に改修してください。

## ステップ1：現状調査と報告
- 現在、パイプライン（`main.py` や `run_pipeline.py` など）に渡されるターゲット銘柄のリストがどこで生成されているか（例: 毎朝 `screener.py` が実行されている等）を特定し、ターミナルに簡潔に報告してください。

## ステップ2：動的スクリーニングへの改修
特定したロジックを改修し、以下の要件を満たす動的スクリーナーを実装してください。
- **実行タイミング:** パイプライン（`main.py`など、毎時間のcronジョブで起動するメイン処理）の「一番最初」にスクリーニング処理を組み込み、セッション開始のたびにターゲット銘柄を再選定するようにしてください。
- **Intraday指標の導入:** 前日の日足データだけでなく、`yfinance` で当日（Intraday）の `1h` または `15m` 足データを取得し、「当日の異常な出来高増加（Volume Spike）」や「日中のモメンタム（急騰）」が発生している銘柄をS&P500の中から動的に抽出するロジックを追加してください。
- **処理の軽量化:** 毎時間500銘柄すべてをフルスキャンすると重いため、事前フィルタリング（API呼び出し回数を減らす工夫、あるいは並列処理）を入れてください。

## ステップ3：テストとプッシュ
- 改修後、`main.py --dry-run` などで実行し、「動的に選定された数銘柄」に対して処理が走ることを確認してください。
- 完了後、`feat: implement dynamic intraday screening for S&P500` というコミットメッセージでGitにコミットし、GitHubへプッシュしてください。import os
import warnings
import requests
import json
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI

warnings.filterwarnings('ignore')
load_dotenv()

# ==========================================
# 📱 LINE Botの設定
# ==========================================
LINE_ACCESS_TOKEN = "rLmNKB5qoOYjlQ1W7G46SpD2dhH3uxCNxqHYnyqWKTmPRWGPP0ZpqrfWs8y3MRFXym3ctwIZXlC14eo2LxXjx++Hha4Fgy2RJX1Ii1LCuRuThgkshqMko1DHIgbDrm812uX+2ywiI6vA9GuJiBy3pAdB04t89/1O/w1cDnyilFU="
LINE_USER_ID = "U266575a29b79da182dfad34f6e879603"

def send_line_message(text):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    data = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": text}]}
    requests.post(url, headers=headers, data=json.dumps(data))

# ==========================================
# 📊 RAG（決算書分析）とデータベース保存
# ==========================================
folder_path = "unzipped_docs/2026年3月期第３四半期_決算一式"
persist_dir = "chroma_db_saved"

embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")

if not os.path.exists(persist_dir):
    print("① 初回起動：PDFを読み込んでデータベースを構築・保存します...")
    loader = PyPDFDirectoryLoader(folder_path)
    documents = loader.load()
    text_splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    texts = text_splitter.split_documents(documents)
    db = Chroma.from_documents(texts, embeddings, persist_directory=persist_dir)
    print("✅ データベースのセーブが完了しました！")
else:
    print("① 保存済みのデータベースを読み込みます（一瞬で終わります）...")
    db = Chroma(persist_directory=persist_dir, embedding_function=embeddings)

llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

query = "AI事業（exaBaseなど）の売上成長率や、今後の導入社数の目標について、具体的な数字があれば教えてください。"

# --------------------------------------------------
# 🌟 Multi-HyDE（仮説生成）プロセス
# --------------------------------------------------
print("\n>> 【裏側の処理】質問に対する「仮説」を生成中...")
hyde_prompt = f"""
あなたはプロの金融アナリストです。
以下の【ユーザーの質問】に対して、実際の決算説明会資料に「どのように書かれていると予想されるか」、具体的な数字のプレースホルダー（例: 〇〇億円、〇〇%など）を含めた【仮説的な回答】を3パターン作成してください。

【ユーザーの質問】
{query}
"""
# AIに仮の答え（仮説）を考えさせる
hypotheses = llm.invoke(hyde_prompt).content
print(">> 【生成された仮説】\n", hypotheses, "\n")

# --------------------------------------------------
# 🌟 検索の高度化（元の質問＋仮説で検索）
# --------------------------------------------------
print("② 情報を横断検索してAIに分析させています...\n")
enhanced_query = query + "\n" + hypotheses
# 仮説を混ぜて検索することで、専門用語のヒット率を上げる
docs = db.similarity_search(enhanced_query, k=6)
context = "\n\n".join([doc.page_content for doc in docs])

prompt = f"""
あなたはプロの機関投資家・金融アナリストです。
以下の【検索された決算資料の該当箇所】を元に、【質問】に答えてください。
※資料に書かれていないことは絶対に推測で語らないでください。

【検索された決算資料の該当箇所】
{context}

【質問】
{query}
"""

print("③ 分析完了！結果をターミナルに表示し、LINEへ送信します...\n" + "="*40)
response = llm.invoke(prompt)

print(response.content)

line_text = f"📊 【決算分析レポート】\n\n{response.content}"
send_line_message(line_text)
