import os
import json
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
import requests
import numpy as np
from sentence_transformers import SentenceTransformer, util

# -------------------
# Configuration
# -------------------
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

FAQ_FILE = DATA_DIR / "faq.json"
FAQ_CUSTOM_FILE = DATA_DIR / "faq_custom.json"
PENDING_FILE = DATA_DIR / "pending.json"

SIMILARITY_THRESHOLD = 0.72  # adjust if needed

# Load environment variables
load_dotenv()
OR_KEY = os.getenv("OPENROUTER_KEY", "").strip()
MODEL = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct:free")

API_URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {OR_KEY}"}

# -------------------
# Utilities: load/save JSON
# -------------------
def load_json(path):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:  # empty file
                    return []
                return json.loads(content)
        except json.JSONDecodeError:
            # corrupted file → reset to empty
            return []
    return []


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Initialize storage files if missing
if not FAQ_FILE.exists():
    save_json(FAQ_FILE, [])
if not FAQ_CUSTOM_FILE.exists():
    save_json(FAQ_CUSTOM_FILE, [])
if not PENDING_FILE.exists():
    save_json(PENDING_FILE, [])

# -------------------
# Load FAQs and embeddings
# -------------------
def load_all_faqs():
    base = load_json(FAQ_FILE)
    custom = load_json(FAQ_CUSTOM_FILE)
    combined = base + custom
    return [{"question": q.get("question", "").strip(),
             "answer": q.get("answer", "").strip()}
            for q in combined if q.get("question")]

print("Loading embedding model (this may take a few moments)...")
EMBED_MODEL = SentenceTransformer("all-MiniLM-L6-v2")  # small & fast
faqs = load_all_faqs()


faq_embeddings = None
if faqs:
    questions = [q["question"] for q in faqs]
    faq_embeddings = EMBED_MODEL.encode(questions, convert_to_tensor=True)

def rebuild_embeddings():
    global faqs, faq_embeddings
    faqs = load_all_faqs()
    if faqs:
        questions = [q["question"] for q in faqs]
        faq_embeddings = EMBED_MODEL.encode(questions, convert_to_tensor=True)
    else:
        faq_embeddings = None

# -------------------
# OpenRouter completion
# -------------------
def openrouter_generate(prompt):
    """
    Uses OpenRouter chat completions API.
    Requires OPENROUTER_KEY in .env
    """
    if not OR_KEY:
        return None
    try:
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(API_URL, json=payload, headers=HEADERS, timeout=60)
        if r.status_code == 200:
            data = r.json()
            if "choices" in data and len(data["choices"]) > 0:
                msg = data["choices"][0].get("message", {}).get("content")
                if msg:
                    return msg.strip()
            return str(data)
        else:
            print("OpenRouter error:", r.status_code, r.text)
            return None
    except Exception as e:
        print("OpenRouter exception:", e)
        return None


# -------------------
# Flask app routes
# -------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
@app.route("/ping")
def ping():
    return "pong"
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "No message provided"}), 400

    # 1) semantic match against FAQ
    if faq_embeddings is not None and len(faqs) > 0:
        q_emb = EMBED_MODEL.encode(user_msg, convert_to_tensor=True)
        scores = util.pytorch_cos_sim(q_emb, faq_embeddings)[0].cpu().numpy()
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        if best_score >= SIMILARITY_THRESHOLD:
            answer = faqs[best_idx]["answer"]
            return jsonify({"answer": answer, "source": "cache", "score": best_score})

    # 2) Not found → call OpenRouter
    prompt = f"You are a helpful assistant for a product admin system. Provide a clear, actionable answer:\n\n{user_msg}"
    ai_answer = openrouter_generate(prompt)
    if ai_answer:
        # save as custom FAQ
        custom = load_json(FAQ_CUSTOM_FILE)
        custom.append({"question": user_msg, "answer": ai_answer})
        save_json(FAQ_CUSTOM_FILE, custom)
        rebuild_embeddings()
        return jsonify({"answer": ai_answer, "source": "api", "score": None})
    else:
        # fallback: save pending
        pending = load_json(PENDING_FILE)
        pending.append({"question": user_msg, "created_at": datetime.utcnow().isoformat()})
        save_json(PENDING_FILE, pending)
        return jsonify({"answer": "I don't know the exact answer yet — I've saved your question for admin review.", "source": "saved", "score": None})

# -------------------
# Admin endpoints
# -------------------
@app.route("/admin")
def admin_page():
    return render_template("admin.html")

@app.route("/api/pending", methods=["GET"])
def get_pending():
    return jsonify(load_json(PENDING_FILE))

@app.route("/api/answer", methods=["POST"])
def answer_pending():
    payload = request.get_json() or {}
    question = payload.get("question", "").strip()
    answer = payload.get("answer", "").strip()
    if not question or not answer:
        return jsonify({"error": "question and answer required"}), 400

    # add to custom FAQ
    custom = load_json(FAQ_CUSTOM_FILE)
    custom.append({"question": question, "answer": answer})
    save_json(FAQ_CUSTOM_FILE, custom)
    rebuild_embeddings()

    # remove from pending
    pending = load_json(PENDING_FILE)
    pending = [p for p in pending if p.get("question","").strip() != question]
    save_json(PENDING_FILE, pending)
    return jsonify({"ok": True})

@app.route("/api/generate", methods=["POST"])
def generate_for_pending():
    payload = request.get_json() or {}
    question = payload.get("question", "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    prompt = f"You are a helpful assistant for a product admin system. Provide a clear, actionable answer:\n\n{question}"
    ai_answer = openrouter_generate(prompt)
    if not ai_answer:
        return jsonify({"error": "generation failed"}), 500

    # save to custom FAQ
    custom = load_json(FAQ_CUSTOM_FILE)
    custom.append({"question": question, "answer": ai_answer})
    save_json(FAQ_CUSTOM_FILE, custom)
    rebuild_embeddings()

    # remove from pending
    pending = load_json(PENDING_FILE)
    pending = [p for p in pending if p.get("question","").strip() != question]
    save_json(PENDING_FILE, pending)
    return jsonify({"answer": ai_answer})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
