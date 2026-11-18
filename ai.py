# ai.py - all AI helper functions. Every AI call has a fallback, so the app never crashes.
import os
import re
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()  # reads API_KEY from the .env file (never hardcoded)

CATEGORIES = ["Food", "Transport", "Shopping", "Bills", "Health", "Entertainment", "Other"]

# Simple keyword rules, used only when the AI is unavailable
KEYWORDS = {
    "Food": ["pizza", "tea", "coffee", "lunch", "dinner", "breakfast", "snack", "biryani", "food",
             "swiggy", "zomato", "restaurant", "grocer", "milk", "burger", "canteen"],
    "Transport": ["auto", "bus", "metro", "uber", "ola", "cab", "petrol", "fuel", "train", "bike", "ticket"],
    "Shopping": ["shirt", "shoes", "amazon", "flipkart", "clothes", "dress", "mall", "shopping"],
    "Bills": ["rent", "electricity", "recharge", "wifi", "internet", "bill", "emi", "water", "gas"],
    "Health": ["doctor", "medicine", "hospital", "pharmacy", "gym", "medical", "clinic"],
    "Entertainment": ["movie", "netflix", "game", "concert", "spotify", "party", "outing"],
}


# ---------- The one function that calls the LLM ----------
def ask_ai(system, user):
    """Send a system + user prompt to the LLM. Returns the text, or None if anything fails."""
    try:
        key = os.getenv("API_KEY")
        if not key or key == "your_key_here":
            return None  # no key set -> caller uses its fallback

        provider = os.getenv("AI_PROVIDER", "gemini").lower()

        if provider == "gemini":
            model = os.getenv("MODEL_NAME", "gemini-2.5-flash")
            for attempt in range(2):  # Google is sometimes busy (503), so try twice
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={"system_instruction": {"parts": [{"text": system}]},
                          "contents": [{"parts": [{"text": user}]}]},
                    timeout=20)
                if r.status_code not in (429, 503):
                    break
                time.sleep(2)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]

        if provider == "openai":
            model = os.getenv("MODEL_NAME", "gpt-4o-mini")
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=30)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

        if provider == "anthropic":
            model = os.getenv("MODEL_NAME", "claude-haiku-4-5")
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": 1000, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=30)
            r.raise_for_status()
            return r.json()["content"][0]["text"]

        return None
    except Exception as e:
        print("AI error:", e)  # visible in the api.py terminal, useful for debugging
        return None


def parse_json(text):
    """Safely turn the AI reply into Python data. Returns None if it is not valid JSON."""
    if not text:
        return None
    try:
        text = re.sub(r"```(?:json)?", "", text).strip()  # remove markdown fences if present
        start = min([i for i in (text.find("["), text.find("{")) if i != -1], default=-1)
        if start == -1:
            return None
        return json.JSONDecoder().raw_decode(text[start:])[0]  # ignores any text after the JSON
    except Exception:
        return None


# ---------- Feature 2: categorize ----------
def keyword_category(note):
    note = (note or "").lower()
    for cat, words in KEYWORDS.items():
        if any(w in note for w in words):
            return cat
    return "Other"


def categorize_note(note):
    """Returns (category, source). source is 'ai' or 'fallback'."""
    reply = ask_ai(
        "You classify expenses. Reply with exactly one word from this list: "
        + ", ".join(CATEGORIES) + ". No other text.",
        f"Note: {note}")
    if reply:
        word = reply.strip().strip(".").title()
        if word in CATEGORIES:
            return word, "ai"
    return keyword_category(note), "fallback"


def simple_parse(text):
    """Fallback parser: splits 'paid 250 for auto and 80 for tea' into entries without AI."""
    items = []
    for part in re.split(r",| and |;", text.lower()):
        m = re.search(r"(\d+(?:\.\d+)?)", part)
        if m:
            note = re.sub(r"\b(paid|spent|for|on|rs|rupees|got|received|earned)\b", "", part)
            note = re.sub(r"[\d.]+", "", note).strip()
            t_type = "income" if re.search(r"received|got|earned|salary", part) else "expense"
            items.append({"type": t_type, "amount": float(m.group(1)), "note": note,
                          "category": keyword_category(note)})
    return items


def parse_text(text):
    """Turn natural text into a list of transactions. Returns (items, source)."""
    reply = ask_ai(
        "Extract every transaction from the user's text. Reply ONLY with a JSON array, no markdown. "
        'Each item: {"type": "income" or "expense", "amount": number, "note": short text, '
        '"category": one of ' + ", ".join(CATEGORIES) + "}.",
        text)
    data = parse_json(reply)
    if reply and not data:
        print("AI reply was not valid JSON:", reply[:300])
    if isinstance(data, list) and data:
        items = []
        for d in data:
            try:
                amount = float(d["amount"])
                if amount <= 0:
                    continue
                cat = d.get("category") if d.get("category") in CATEGORIES else keyword_category(d.get("note"))
                t_type = d.get("type") if d.get("type") in ("income", "expense") else "expense"
                items.append({"type": t_type, "amount": amount, "note": str(d.get("note", "")), "category": cat})
            except Exception:
                continue  # skip a bad item instead of crashing
        if items:
            return items, "ai"
    return simple_parse(text), "fallback"


# ---------- Feature 5: insights ----------
def monthly_summary(ctx):
    """ctx = this month's real totals. Returns {"summary": text, "tips": [3 tips], "source": ...}."""
    reply = ask_ai(
        "You are a friendly personal finance coach. Use ONLY the data given. Amounts are in Rs. "
        'Reply ONLY with JSON: {"summary": "one short paragraph on where the user overspends", '
        '"tips": ["tip 1", "tip 2", "tip 3"]}. Exactly 3 tips, each one short sentence '
        "that uses the user's own numbers.",
        json.dumps(ctx, default=float))
    data = parse_json(reply)
    if reply and not data:
        print("AI reply was not valid JSON:", reply[:300])  # helps to debug in the api.py terminal
    if isinstance(data, dict) and data.get("summary") and isinstance(data.get("tips"), list) and data["tips"]:
        return {"summary": str(data["summary"]), "tips": [str(t) for t in data["tips"][:3]], "source": "ai"}

    # Fallback: simple rule-based summary and tips
    cats = ctx.get("expense_by_category", {})
    if not cats:
        return {"summary": "No expenses recorded for this month.", "tips": [], "source": "fallback"}
    ranked = sorted(cats, key=cats.get, reverse=True)
    top = ranked[0]
    summary = f"You spent the most on {top} (Rs.{cats[top]:,.0f}) this month."
    if ctx.get("over_budget"):
        summary += " You went over budget in: " + ", ".join(ctx["over_budget"]) + "."
    tips = [f"Cut {top} by 10% to save about Rs.{cats[top] * 0.1:,.0f} a month."]
    if len(ranked) > 1:
        tips.append(f"Set a weekly limit for {ranked[1]} (Rs.{cats[ranked[1]]:,.0f} this month).")
    tips.append("Move some money into savings on the day your income arrives.")
    return {"summary": summary + " (AI is unavailable, so this is a basic summary.)",
            "tips": tips, "source": "fallback"}


def explain_anomalies(items):
    """items = list of unusual transactions. Returns a short explanation for each."""
    reply = ask_ai(
        "You are a finance assistant. For each unusual transaction write ONE short bullet "
        "(max 20 words) explaining why it stands out, using only the numbers given. Amounts are in Rs.",
        json.dumps(items, default=str))
    if reply:
        return reply
    # Fallback: simple rule-based sentences
    return "\n".join(
        f"- {i['note']} (Rs.{i['amount']:,.0f}) is {i['amount'] / i['usual_avg']:.1f}x the usual "
        f"Rs.{i['usual_avg']:,.0f} for {i['category']}." for i in items)


def answer_question(question, ctx):
    """Answer only from the user's real data. ctx = totals per month and category."""
    reply = ask_ai(
        "You are a finance assistant. Answer ONLY using the JSON data provided. Amounts are in Rs. "
        "If the data does not contain the answer, say exactly: "
        "\"I don't have that information in your data.\" Do not guess. Keep answers short.",
        f"My data:\n{json.dumps(ctx, default=float)}\n\nQuestion: {question}")
    if reply:
        return reply
    return "AI is not available right now. Please check your API key and internet connection."