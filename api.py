# api.py - REST API between Streamlit and the database / AI
from flask import Flask, request, jsonify
import pandas as pd
import database as db
import ai

app = Flask(__name__)
db.init_db()  # make sure the tables exist when the API starts


def valid(data):
    # Basic validation so bad data never reaches the database
    try:
        return (data.get("type") in ("income", "expense")
                and float(data.get("amount", 0)) > 0
                and bool(data.get("date")))
    except (TypeError, ValueError):
        return False


def get_df():
    df = pd.DataFrame(db.get_transactions(),
                      columns=["id", "type", "amount", "date", "note", "category", "payment_method"])
    df["month"] = df["date"].astype(str).str[:7]  # '2026-11'
    return df


# ---------- Transactions ----------
@app.route("/transactions", methods=["GET"])
def list_transactions():
    return jsonify(db.get_transactions())


@app.route("/transactions", methods=["POST"])
def create_transaction():
    data = request.get_json()
    if not valid(data):
        return jsonify({"error": "Invalid data"}), 400
    # Duplicate guard: same type, amount, date and note already saved (the user can override with force)
    if not data.get("force") and db.find_duplicate(data["type"], float(data["amount"]), data["date"],
                                                   data.get("note", "")):
        return jsonify({"duplicate": True, "error": "Possible duplicate"}), 409
    db.add_transaction(data["type"], float(data["amount"]), data["date"],
                       data.get("note", ""), data.get("category", "Other"), data.get("payment_method", "UPI"))
    return jsonify({"message": "Added"}), 201


@app.route("/transactions/bulk", methods=["POST"])
def create_many():
    # Used by the "type naturally" feature: save several entries at once
    saved = 0
    for data in request.get_json():
        if valid(data):
            db.add_transaction(data["type"], float(data["amount"]), data["date"],
                               data.get("note", ""), data.get("category", "Other"), data.get("payment_method", "UPI"))
            saved += 1
    return jsonify({"saved": saved}), 201


@app.route("/transactions/import", methods=["POST"])
def import_transactions():
    # Bank statement import: skip rows that already exist, categorise the rest without AI
    existing = {(t["type"], float(t["amount"]), t["date"], db.normalize(t["note"]))
                for t in db.get_transactions()}  # taken BEFORE importing, so repeats inside the file are kept
    added = skipped = 0
    for d in request.get_json():
        if not valid(d) or (d["type"], float(d["amount"]), d["date"], db.normalize(d.get("note", ""))) in existing:
            skipped += 1
            continue
        note = d.get("note", "")
        category = db.find_rule(note) or ai.keyword_category(note)  # learned rule first, then keywords
        db.add_transaction(d["type"], float(d["amount"]), d["date"], note, category,
                           d.get("payment_method", "Net Banking"))
        added += 1
    return jsonify({"added": added, "skipped": skipped})


@app.route("/transactions/<int:t_id>", methods=["PUT"])
def edit_transaction(t_id):
    data = request.get_json()
    if not valid(data):
        return jsonify({"error": "Invalid data"}), 400
    old = db.get_transaction(t_id)
    db.update_transaction(t_id, data["type"], float(data["amount"]), data["date"], data.get("note", ""),
                          data.get("category", "Other"), data.get("payment_method", "UPI"))
    # If the user changed the category, remember it for next time
    if old and old["category"] != data.get("category") and data.get("note"):
        db.learn_rule(data["note"], data["category"])
    return jsonify({"message": "Updated"})


@app.route("/transactions/<int:t_id>", methods=["DELETE"])
def remove_transaction(t_id):
    db.delete_transaction(t_id)
    return jsonify({"message": "Deleted"})


@app.route("/rules", methods=["POST"])
def save_rule():
    d = request.get_json()
    db.learn_rule(d.get("note", ""), d.get("category", "Other"))
    return jsonify({"message": "Learned"})


# ---------- Budgets ----------
@app.route("/budgets", methods=["GET"])
def list_budgets():
    return jsonify(db.get_budgets())


@app.route("/budgets", methods=["POST"])
def save_budgets():
    for category, amount in request.get_json().items():  # {"Food": 4000, ...}
        db.set_budget(category, float(amount))
    return jsonify({"message": "Budgets saved"})


# ---------- Recurring transactions ----------
@app.route("/recurring", methods=["GET"])
def list_recurring():
    return jsonify(db.get_recurring())


@app.route("/recurring", methods=["POST"])
def create_recurring():
    d = request.get_json()
    if not d.get("name") or d.get("type") not in ("income", "expense") or float(d.get("amount", 0)) <= 0:
        return jsonify({"error": "Invalid data"}), 400
    due_day = min(max(int(d.get("due_day", 1)), 1), 28)
    db.add_recurring(d["name"], d["type"], float(d["amount"]), d.get("category", "Other"), due_day)
    return jsonify({"message": "Saved"}), 201


@app.route("/recurring/<int:r_id>", methods=["DELETE"])
def remove_recurring(r_id):
    db.delete_recurring(r_id)
    return jsonify({"message": "Deleted"})


@app.route("/recurring/apply", methods=["POST"])
def apply_recurring():
    # One click: turn templates into real transactions on the chosen date.
    # body: {"date": "2026-11-06", "id": 3}  (leave out "id" to add all templates)
    d = request.get_json()
    added = 0
    for r in db.get_recurring():
        if d.get("id") in (None, r["id"]):
            db.add_transaction(r["type"], r["amount"], d["date"], r["name"], r["category"])
            added += 1
    return jsonify({"added": added})


# ---------- Savings goals ----------
@app.route("/goals", methods=["GET"])
def list_goals():
    return jsonify(db.get_goals())


@app.route("/goals", methods=["POST"])
def create_goal():
    d = request.get_json()
    if not d.get("name") or float(d.get("target", 0)) <= 0 or not d.get("deadline"):
        return jsonify({"error": "Invalid data"}), 400
    db.add_goal(d["name"], float(d["target"]), d["deadline"], float(d.get("saved", 0)))
    return jsonify({"message": "Saved"}), 201


@app.route("/goals/<int:g_id>/add", methods=["POST"])
def add_savings(g_id):
    db.add_to_goal(g_id, float(request.get_json().get("amount", 0)))
    return jsonify({"message": "Updated"})


@app.route("/goals/<int:g_id>", methods=["DELETE"])
def remove_goal(g_id):
    db.delete_goal(g_id)
    return jsonify({"message": "Deleted"})


# ---------- AI features ----------
@app.route("/categorize", methods=["POST"])
def categorize():
    note = request.get_json().get("note", "")
    learned = db.find_rule(note)  # a category the user taught the app earlier
    if learned:
        return jsonify({"category": learned, "source": "learned"})
    category, source = ai.categorize_note(note)
    return jsonify({"category": category, "source": source})


@app.route("/parse", methods=["POST"])
def parse():
    items, source = ai.parse_text(request.get_json().get("text", ""))
    for item in items:  # learned rules beat the AI
        learned = db.find_rule(item["note"])
        if learned:
            item["category"] = learned
    return jsonify({"items": items, "source": source})


@app.route("/insights/summary", methods=["GET"])
def insights_summary():
    month = request.args.get("month")
    df = get_df()
    df = df[df["month"] == month]
    if df.empty:
        return jsonify({"summary": "No data for this month.", "tips": [], "source": "fallback"})

    income = float(df[df["type"] == "income"]["amount"].sum())
    by_cat = df[df["type"] == "expense"].groupby("category")["amount"].sum().round(2).to_dict()
    budgets = db.get_budgets()
    ctx = {
        "month": month,
        "total_income": income,
        "total_expenses": float(sum(by_cat.values())),
        "expense_by_category": by_cat,
        "budgets": budgets,
        "over_budget": [c for c, spent in by_cat.items() if c in budgets and spent > budgets[c]],
    }
    return jsonify(ai.monthly_summary(ctx))


@app.route("/anomalies/explain", methods=["POST"])
def explain():
    return jsonify({"explanation": ai.explain_anomalies(request.get_json().get("items", []))})


@app.route("/ask", methods=["POST"])
def ask():
    # Send real totals per month, so the AI answers only from our data
    df = get_df()
    if df.empty:
        return jsonify({"answer": "I don't have that information in your data."})
    expenses = df[df["type"] == "expense"]
    income = df[df["type"] == "income"].groupby("month")["amount"].sum()
    spent = expenses.groupby("month")["amount"].sum()
    ctx = {
        "expense_by_month_and_category": {m: g.groupby("category")["amount"].sum().round(2).to_dict()
                                          for m, g in expenses.groupby("month")},
        "income_by_month": income.round(2).to_dict(),
        "total_expenses_by_month": spent.round(2).to_dict(),
        "savings_by_month": (income.sub(spent, fill_value=0)).round(2).to_dict(),
        "monthly_budgets": db.get_budgets(),
    }
    return jsonify({"answer": ai.answer_question(request.get_json().get("question", ""), ctx)})


# ---------- Demo tools ----------
@app.route("/sample", methods=["POST"])
def sample():
    db.load_sample_data()
    return jsonify({"message": "Sample data loaded"})


@app.route("/clear", methods=["DELETE"])
def clear():
    db.clear_all()
    return jsonify({"message": "All data cleared"})


if __name__ == "__main__":
    app.run(port=5000, debug=True)