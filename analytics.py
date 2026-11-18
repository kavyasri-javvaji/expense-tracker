# analytics.py - simple calculations and the PDF report (no AI in this file)
from datetime import date
import numpy as np
import pandas as pd
from fpdf import FPDF

CATEGORIES = ["Food", "Transport", "Shopping", "Bills", "Health", "Entertainment", "Other"]
NEEDS = ["Food", "Transport", "Bills", "Health"]  # the rest are "wants"
PAYMENT_METHODS = ["UPI", "Cash", "Card", "Net Banking"]

# One colour per category, used in every chart, table and card
CATEGORY_COLORS = {
    "Food": "#F97316",          # orange
    "Transport": "#0EA5E9",     # sky blue
    "Shopping": "#EC4899",      # pink
    "Bills": "#6366F1",         # indigo
    "Health": "#14B8A6",        # teal
    "Entertainment": "#8B5CF6", # violet
    "Other": "#94A3B8",         # slate
}
CATEGORY_ICONS = {"Food": "🍔", "Transport": "🚌", "Shopping": "🛍️", "Bills": "💡",
                  "Health": "💊", "Entertainment": "🎬", "Other": "📦"}
PAYMENT_COLORS = {"UPI": "#6366F1", "Cash": "#14B8A6", "Card": "#EC4899", "Net Banking": "#F97316"}


def inr(x):
    """Indian digit grouping: 1234567 -> 12,34,567"""
    n = int(round(abs(x)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if x < 0 and n else "") + s


def rs(x):
    """Format a number as rupees: Rs.1,25,000"""
    return ("-" if x < 0 and round(abs(x)) else "") + "Rs." + inr(abs(x))


def health_score(income, expense, budgets, by_cat, n_months=1):
    """Score 0-100 = savings points (max 50) + budget points (max 50)."""
    savings_rate = (income - expense) / income if income > 0 else 0
    savings_pts = max(0, min(savings_rate / 0.20, 1)) * 50   # saving 20% or more = full 50 points
    budgeted = [c for c in budgets if budgets[c] > 0]
    within = [c for c in budgeted if by_cat.get(c, 0) <= budgets[c] * n_months]
    budget_pts = (len(within) / len(budgeted) if budgeted else 1) * 50
    return {"score": int(round(savings_pts + budget_pts)), "savings_rate": savings_rate,
            "savings_pts": savings_pts, "budget_pts": budget_pts,
            "within": len(within), "budgeted": len(budgeted)}


def find_anomalies(df):
    """Flag expenses above (mean + 2 x standard deviation) of their own category."""
    exp = df[df["type"] == "expense"].copy()
    stats = exp.groupby("category")["amount"].agg(usual_avg="mean", std="std", count="count")
    exp = exp.join(stats, on="category")
    exp["limit"] = exp["usual_avg"] + 2 * exp["std"]
    flagged = exp[(exp["count"] >= 5) & (exp["amount"] > exp["limit"])]  # need 5+ entries to be meaningful
    return flagged[["id", "date", "note", "category", "amount", "usual_avg", "limit"]].sort_values(
        "amount", ascending=False)


def complete_months(exp):
    """Months used for forecast/averages: skip the current month because it is not finished yet."""
    this_month = date.today().strftime("%Y-%m")
    months = sorted(exp["month"].unique())
    done = [m for m in months if m < this_month]
    return done if len(done) >= 2 else months


def linear_forecast(values):
    """Fit a straight line through the monthly totals and predict the next point."""
    x = np.arange(len(values))
    slope, intercept = np.polyfit(x, values, 1)
    return max(0.0, float(slope * len(values) + intercept))


def suggest_budgets(df):
    """Suggest monthly budgets from past spending:
    needs = typical spend + 5%, wants = typical spend - 15%, total capped at 80% of average income."""
    exp = df[df["type"] == "expense"]
    months = complete_months(exp)
    if not months:
        return None
    # Median monthly spend per category, so one unusual month does not distort the suggestion
    monthly = exp[exp["month"].isin(months)].pivot_table(index="month", columns="category", values="amount",
                                                         aggfunc="sum").reindex(months).fillna(0)
    avg = monthly.median()
    income = float(df[(df["type"] == "income") & (df["month"].isin(months))]["amount"].sum() / len(months))

    budgets = {c: float(avg.get(c, 0)) * (1.05 if c in NEEDS else 0.85) for c in CATEGORIES}
    wants = [c for c in CATEGORIES if c not in NEEDS]
    cap = income * 0.80
    if income > 0 and sum(budgets.values()) > cap:  # too high: shrink the wants to fit
        need_total = sum(budgets[c] for c in NEEDS)
        want_total = sum(budgets[c] for c in wants)
        factor = max(0.0, (cap - need_total) / want_total) if want_total > 0 else 1
        for c in wants:
            budgets[c] *= min(factor, 1)
    return {"budgets": {c: float(int(v / 100 + 0.5) * 100) for c, v in budgets.items()},
            "months": months, "avg_income": income}


def statement_to_rows(raw, date_col, desc_col, layout, amount_col=None, debit_col=None, credit_col=None):
    """Turn a bank statement table into transactions. Returns (rows, skipped_count).
    layout 'single': one amount column (negative = expense). layout 'split': separate debit / credit columns."""
    def to_num(series):
        return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")

    dates = pd.to_datetime(raw[date_col], dayfirst=True, errors="coerce")  # Indian statements are dd/mm/yyyy
    if layout == "single":
        signed = to_num(raw[amount_col])
    else:
        signed = to_num(raw[credit_col]).fillna(0) - to_num(raw[debit_col]).fillna(0)

    rows, skipped = [], 0
    for d, text, amt in zip(dates, raw[desc_col], signed):
        if pd.isna(d) or pd.isna(amt) or amt == 0:
            skipped += 1
            continue
        rows.append({"date": d.date().isoformat(), "note": str(text).strip(), "amount": abs(float(amt)),
                     "type": "income" if amt > 0 else "expense", "payment_method": "Net Banking"})
    return rows, skipped


def _clean(text):
    # The built-in PDF font only supports basic characters
    text = str(text).replace("₹", "Rs.").replace("**", "").replace("•", "-")
    return text.encode("latin-1", "replace").decode("latin-1")


def make_pdf(period, income, expense, score, by_cat, budgets, summary="", tips=None):
    """Build the monthly report and return it as bytes."""
    pdf = FPDF()
    pdf.add_page()
    line = {"new_x": "LMARGIN", "new_y": "NEXT"}

    # Title bar
    pdf.set_fill_color(79, 70, 229)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 14, "  SmartSpend AI - Monthly Report", fill=True, **line)

    # Summary numbers
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 12)
    pdf.ln(4)
    pdf.cell(0, 8, _clean(f"Period: {period}"), **line)
    pdf.cell(0, 8, f"Income: {rs(income)}    Expenses: {rs(expense)}    Savings: {rs(income - expense)}", **line)
    pdf.cell(0, 8, f"Financial health score: {score}/100", **line)

    # Category table
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(224, 231, 255)
    for text, width in (("Category", 60), ("Spent", 40), ("Budget", 40), ("Status", 40)):
        pdf.cell(width, 8, text, border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 11)
    for cat in CATEGORIES:
        spent, budget = by_cat.get(cat, 0), budgets.get(cat, 0)
        status = "No budget" if budget <= 0 else ("OVER" if spent > budget else "OK")
        for text, width in ((cat, 60), (rs(spent), 40), (rs(budget), 40), (status, 40)):
            pdf.cell(width, 8, text, border=1)
        pdf.ln()

    # AI insights
    if summary:
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "AI Summary", **line)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _clean(summary), **line)
    if tips:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, "Saving Tips", **line)
        pdf.set_font("Helvetica", "", 11)
        for tip in tips:
            pdf.multi_cell(0, 6, _clean("- " + tip), **line)

    return bytes(pdf.output())