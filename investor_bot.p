import os
import warnings
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma

# 不要な警告を非表示にする
warnings.filterwarnings('ignore')

# 環境変数（APIキー）を読み込む
load_dotenv()

# 1. ターゲットファイルのパス
file_path = "unzipped_docs/2026年3月期第３四半期_決算一式/2026年3月期第３四半期 決算説明_質疑応答（書き起こし）.pdf"

print("① PDFを読み込んでいます...")
loader = PyPDFLoader(file_path)
documents = loader.load()

print("② 文章をAIが理解しやすいサイズに分割しています...")
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
texts = text_splitter.split_documents(documents)

print("③ ベクトルデータベース（Chroma）を構築中...")
embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
db = Chroma.from_documents(texts, embeddings)

print("④ 情報を検索してAIに分析させています...\n")
llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0)
query = "この質疑応答の中で、機関投資家が一番気にしている（突っ込んでいる）リスクや懸念点は何ですか？また、それに対して経営陣はどう回答していますか？箇条書きで分かりやすく抽出して。"

# ==========================================
# ここからがブラックボックスに頼らない「RAG」の自作ロジックです！
# ==========================================

# Step A: データベースから質問に関連する箇所を検索 (Retrieve)
docs = db.similarity_search(query, k=5)
context = "\n\n".join([doc.page_content for doc in docs])

# Step B: AIへの指示文（プロンプト）に、検索した情報を埋め込む (Augment)
prompt = f"""
あなたはプロの機関投資家・金融アナリストです。
以下の【決算書の書き起こしデータ】を元に、【質問】に答えてください。

【決算書の書き起こしデータ】
{context}

【質問】
{query}
"""

# Step C: AIに回答させる (Generate)
print(f"質問: {query}\n" + "="*50)
response = llm.invoke(prompt)
print(response.content)
