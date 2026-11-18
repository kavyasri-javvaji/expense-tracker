# app.py - Streamlit frontend (talks to the Flask API)
import threading
import time
import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
import analytics as an
from analytics import CATEGORIES, CATEGORY_COLORS, CATEGORY_ICONS, PAYMENT_METHODS, PAYMENT_COLORS

API_URL = "http://127.0.0.1:5000"
TODAY = date.today()

st.set_page_config(page_title="SmartSpend AI", page_icon="💰", layout="wide")

# ---------- Custom CSS (colours work in light and dark mode) ----------
st.markdown("""
<style>
.block-container {padding-top: 1.5rem;}
.banner {background: linear-gradient(120deg,#4F46E5,#7C3AED 55%,#EC4899); padding: 22px 28px;
         border-radius: 18px; color: white; margin-bottom: 18px; box-shadow: 0 8px 24px rgba(79,70,229,.30);}
.banner h1 {margin: 0; font-size: 2rem; color: white; padding: 0;}
.banner p {margin: 4px 0 0; opacity: .92; font-size: 1rem;}
.card {border-radius: 16px; padding: 18px 20px; color: white; box-shadow: 0 6px 18px rgba(0,0,0,.18); margin-bottom: 8px;}
.card .label {font-size: .9rem; opacity: .92;}
.card .value {font-size: 1.8rem; font-weight: 700; line-height: 1.3;}
.card .sub {font-size: .8rem; opacity: .88;}
.c-income {background: linear-gradient(135deg,#0D9488,#2DD4BF);}
.c-expense {background: linear-gradient(135deg,#EA580C,#FB923C);}
.c-savings {background: linear-gradient(135deg,#4F46E5,#818CF8);}
.c-budget {background: linear-gradient(135deg,#DB2777,#F472B6);}
.soft {border-radius: 16px; padding: 16px 20px; margin-bottom: 12px; background: rgba(99,102,241,.10);
       border: 1px solid rgba(99,102,241,.25); box-shadow: 0 4px 14px rgba(0,0,0,.08);}
.tip {border-radius: 14px; padding: 14px 16px; background: rgba(20,184,166,.12);
      border-left: 5px solid #14B8A6; min-height: 90px;}
.track {background: rgba(128,128,128,.25); border-radius: 8px; height: 12px;}
[data-testid="stSidebar"] {background: linear-gradient(180deg, rgba(99,102,241,.15), rgba(236,72,153,.10));}
[data-testid="stSidebar"] .stRadio label {padding: 6px 10px; border-radius: 10px;}
</style>
""", unsafe_allow_html=True)


# ---------- Helpers ----------
def call(method, path, timeout=10, **kwargs):
    """One helper for every API call. Returns the JSON reply, or None if the backend is down."""
    try:
        return requests.request(method, API_URL + path, timeout=timeout, **kwargs).json()
    except (requests.exceptions.RequestException, ValueError):
        st.error("Cannot reach the backend. Start it with: python api.py")
        return None


COLUMNS = ["id", "type", "amount", "date", "note", "category", "payment_method"]


def load_data():
    data = call("GET", "/transactions") or []
    df = pd.DataFrame(data, columns=COLUMNS)
    df["month"] = df["date"].astype(str).str[:7]  # '2026-11'
    budgets = call("GET", "/budgets") or {}
    return data, df, budgets


money = an.rs  # Rs.1,25,000 (Indian digit grouping)


def card(label, value, icon, css, sub=""):
    """Gradient metric card."""
    st.markdown(f"<div class='card {css}'><div class='label'>{icon} {label}</div>"
                f"<div class='value'>{value}</div><div class='sub'>{sub}</div></div>", unsafe_allow_html=True)


def cat_label(cat):
    return f"{CATEGORY_ICONS.get(cat, '📦')} {cat}"


def style_table(df):
    """Format money columns and colour the category column."""
    money_cols = ["amount", "forecast", "budget", "usual_avg", "limit", "current", "suggested"]
    styler = df.style.format({c: money for c in money_cols if c in df.columns})
    if "category" in df.columns:
        colour = lambda v: f"color: {CATEGORY_COLORS.get(v, '#94A3B8')}; font-weight: 600"
        styler = styler.map(colour, subset=["category"]) if hasattr(styler, "map") else styler.applymap(colour, subset=["category"])
    return styler


def style_fig(fig, title, height=340):
    fig.update_layout(title=title, height=height, margin=dict(l=10, r=10, t=50, b=10),
                      font=dict(family="Segoe UI, Arial, sans-serif"))
    return fig


def month_picker(df, key):
    months = sorted(df["month"].unique(), reverse=True)  # newest first
    if not months:
        return None
    return st.selectbox("Month", months, key=key)


def period_filter(df, key):
    """Dashboard filter: one month, all time, or a date range.
    Returns (filtered data, label, number of months) or None."""
    mode = st.radio("View by", ["Month", "Date range"], horizontal=True, key=key + "_mode")
    dates = pd.to_datetime(df["date"])
    if mode == "Month":
        pick = st.selectbox("Month", ["All time"] + sorted(df["month"].unique(), reverse=True),
                            index=1, key=key + "_month")
        if pick == "All time":
            sel, start, end, label = df, dates.min(), dates.max(), "All time"
        else:
            sel, start, end, label = df[df["month"] == pick], pd.Timestamp(pick + "-01"), pd.Timestamp(pick + "-01"), pick
    else:
        picked = st.date_input("Date range", value=(dates.min().date(), dates.max().date()), key=key + "_range")
        if len(picked) != 2:
            st.info("Pick both a start date and an end date.")
            return None
        start, end = pd.Timestamp(picked[0]), pd.Timestamp(picked[1])
        sel, label = df[(dates >= start) & (dates <= end)], f"{picked[0]} to {picked[1]}"
    n_months = max(1, (end.year - start.year) * 12 + end.month - start.month + 1)  # budgets are monthly
    return sel, label, n_months


def progress_bar(cat, spent, limit):
    """Green = safe, amber = 80% or more used, red = over budget."""
    pct = spent / limit * 100 if limit else 0
    color = "#16A34A" if pct < 80 else ("#F59E0B" if pct <= 100 else "#DC2626")
    st.markdown(f"**{cat_label(cat)}**: {money(spent)} of {money(limit)} ({pct:.0f}%)")
    st.markdown(f"<div class='track'><div style='width:{min(pct, 100)}%;background:{color};"
                f"height:12px;border-radius:8px'></div></div>", unsafe_allow_html=True)
    if pct > 100:
        st.warning(f"⚠️ {cat} is over budget by {money(spent - limit)}")
    elif pct >= 80:
        st.caption(f"🟠 {cat} has used {pct:.0f}% of its budget. Only {money(limit - spent)} left.")


def rec_added(df, r, month):
    """Has this recurring item already been added in that month? (stops double entries)"""
    m = df[(df["month"] == month) & (df["type"] == r["type"]) & (df["note"] == r["name"])]
    return bool(((m["amount"] - r["amount"]).abs() < 0.01).any())


def guess_column(cols, words, default):
    """Pick the CSV column whose name contains one of the words."""
    for i, c in enumerate(cols):
        if any(w in str(c).lower() for w in words):
            return i
    return default


# ---------- Start the Flask backend inside the app (needed for cloud deployment) ----------
@st.cache_resource
def start_backend():
    """Starts the Flask API in a background thread, unless it is already running (local use)."""
    try:
        requests.get(API_URL + "/budgets", timeout=1)
        return "already running"
    except requests.exceptions.RequestException:
        pass
    import api  # creates the tables
    if not api.db.get_transactions():
        api.db.load_sample_data()  # a fresh cloud server starts empty, so load the demo data
    threading.Thread(target=lambda: api.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False),
                     daemon=True).start()
    for _ in range(40):  # wait until the API answers
        try:
            requests.get(API_URL + "/budgets", timeout=1)
            break
        except requests.exceptions.RequestException:
            time.sleep(0.25)
    return "started"


start_backend()

# ---------- Simple demo login (a demo gate, not production security) ----------
DEMO_USER, DEMO_PASSWORD = "demo", "Demo@123"
if not st.session_state.get("logged_in"):
    st.markdown("<div class='banner'><h1>💰 SmartSpend AI</h1>"
                "<p>Please log in to continue.</p></div>", unsafe_allow_html=True)
    with st.form("login_form"):
        user_in = st.text_input("Username")
        pass_in = st.text_input("Password", type="password")
        if st.form_submit_button("Log in"):
            if user_in == DEMO_USER and pass_in == DEMO_PASSWORD:
                st.session_state["logged_in"] = True
                st.rerun()
            else:
                st.error("Wrong username or password.")
    st.stop()  # nothing below runs until the user is logged in

# ---------- Header banner and sidebar ----------
st.markdown("<div class='banner'><h1>💰 SmartSpend AI</h1>"
            "<p>Track every rupee, understand your money and spend smarter with AI insights.</p></div>",
            unsafe_allow_html=True)

st.sidebar.title("💰 SmartSpend AI")
if st.sidebar.button("Log out"):
    st.session_state["logged_in"] = False
    st.rerun()
page = st.sidebar.radio("Menu", ["➕ Add Income/Expense", "💼 Budgets", "📊 Dashboard", "🤖 AI Insights"],
                        label_visibility="collapsed")

with st.sidebar.expander("🧪 Demo tools"):
    if st.button("Load sample data"):
        call("POST", "/sample")
        st.session_state["msg"] = "Sample data loaded"
        st.rerun()
    if st.button("Clear all data"):
        call("DELETE", "/clear")
        st.session_state["msg"] = "All data cleared"
        st.rerun()

if "msg" in st.session_state:  # message left over from the previous action
    st.toast(st.session_state.pop("msg"))

data, df, budgets = load_data()
recs_all = call("GET", "/recurring") or []

# Sidebar reminder: recurring bills that are due this month and not added yet
this_month = TODAY.strftime("%Y-%m")
due_now = [r for r in recs_all if TODAY.day >= r.get("due_day", 1) and not rec_added(df, r, this_month)]
if due_now:
    st.sidebar.warning(f"⏰ {len(due_now)} recurring bill(s) due: " + ", ".join(r["name"] for r in due_now))

# =====================================================
# PAGE 1: Add Income / Expense
# =====================================================
if page == "➕ Add Income/Expense":
    st.subheader("➕ Add Income / Expense")
    tab1, tab2, tab3, tab4 = st.tabs(["Add one entry", "✨ Type naturally (AI)", "🔁 Recurring", "📥 Import CSV"])

    # --- Tab 1: normal form ---
    with tab1:
        with st.form("add_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            t_type = c1.radio("Type", ["expense", "income"], horizontal=True)
            amount = c2.number_input("Amount (Rs.)", min_value=0.0, step=10.0)
            t_date = c1.date_input("Date", value=TODAY, max_value=TODAY)  # no future dates
            cat_choice = c2.selectbox("Category", ["Auto (AI)"] + CATEGORIES)
            pay_method = c1.selectbox("Paid via", PAYMENT_METHODS)
            note = st.text_input("Note (e.g. pizza with friends)")
            submitted = st.form_submit_button("Save")

        if submitted:
            if amount <= 0:
                st.warning("Amount must be greater than 0.")
            else:
                category = cat_choice
                if cat_choice == "Auto (AI)":
                    category = "Other"
                    if note.strip():
                        r = call("POST", "/categorize", json={"note": note}, timeout=40)
                        if r and "category" in r:
                            category = r["category"]
                            if r["source"] == "fallback":
                                st.info("AI is unavailable, so keyword matching was used. "
                                        "You can change the category in the Edit section below.")
                payload = {"type": t_type, "amount": amount, "date": t_date.isoformat(),
                           "note": note, "category": category, "payment_method": pay_method}
                res = call("POST", "/transactions", json=payload)
                if res and res.get("duplicate"):
                    st.session_state["dup_payload"] = payload  # ask the user before saving
                elif res and "message" in res:
                    st.session_state["msg"] = f"Saved under {category}"
                    st.rerun()
                else:
                    st.error("Could not save.")

        if "dup_payload" in st.session_state:
            st.warning("⚠️ A transaction with the same type, amount, date and note already exists. Save it anyway?")
            d1, d2, _ = st.columns([1, 1, 4])
            if d1.button("Save anyway"):
                call("POST", "/transactions", json={**st.session_state.pop("dup_payload"), "force": True})
                st.session_state["msg"] = "Saved (duplicate confirmed)"
                st.rerun()
            if d2.button("Cancel"):
                st.session_state.pop("dup_payload")
                st.rerun()

    # --- Tab 2: natural text ---
    with tab2:
        text = st.text_input("Type what you spent", placeholder="paid 250 for auto and 80 for tea", key="nl_text")
        n1, n2 = st.columns(2)
        q_date = n1.date_input("Date for these entries", value=TODAY, max_value=TODAY, key="nl_date")
        q_pay = n2.selectbox("Paid via", PAYMENT_METHODS, key="nl_pay")
        if st.button("Read my text"):
            r = call("POST", "/parse", json={"text": text}, timeout=40)
            items = r.get("items", []) if r else []
            if not items:
                st.warning("I could not find any amounts. Try: paid 250 for auto and 80 for tea")
            st.session_state["parsed"] = items

        if st.session_state.get("parsed"):
            st.caption("Check the entries (you can edit the category), then save.")
            edited = st.data_editor(
                pd.DataFrame(st.session_state["parsed"]), hide_index=True, key="parsed_editor",
                column_config={
                    "type": st.column_config.SelectboxColumn(options=["expense", "income"]),
                    "category": st.column_config.SelectboxColumn(options=CATEGORIES)})
            if st.button("Save all entries"):
                rows = [{**r, "date": q_date.isoformat(), "payment_method": q_pay}
                        for r in edited.to_dict("records")]
                for old, new in zip(st.session_state["parsed"], rows):  # learn from corrections
                    if old["category"] != new["category"] and new["note"]:
                        call("POST", "/rules", json={"note": new["note"], "category": new["category"]})
                res = call("POST", "/transactions/bulk", json=rows)
                st.session_state.pop("parsed")
                st.session_state["msg"] = f"Saved {res.get('saved', 0) if res else 0} entries"
                st.rerun()

    # --- Tab 3: recurring transactions ---
    with tab3:
        st.caption("Save things that repeat (rent, EMI, subscriptions). The app shows what is due "
                   "and stops you adding the same bill twice in a month.")
        with st.form("rec_form", clear_on_submit=True):
            c1, c2, c3, c4, c5 = st.columns(5)
            r_name = c1.text_input("Name (e.g. Room rent)")
            r_type = c2.selectbox("Type", ["expense", "income"])
            r_amount = c3.number_input("Amount (Rs.)", min_value=0.0, step=100.0)
            r_cat = c4.selectbox("Category", CATEGORIES)
            r_due = c5.number_input("Due day (1-28)", min_value=1, max_value=28, value=1, step=1)
            if st.form_submit_button("Save template"):
                res = call("POST", "/recurring", json={"name": r_name, "type": r_type, "amount": r_amount,
                                                       "category": r_cat, "due_day": int(r_due)})
                if res and "message" in res:
                    st.session_state["msg"] = "Template saved"
                    st.rerun()
                else:
                    st.warning("Enter a name and an amount greater than 0.")

        rec_date = st.date_input("Add them on this date", value=TODAY, max_value=TODAY, key="rec_date")
        rec_month = rec_date.strftime("%Y-%m")
        pending = []
        for r in recs_all:
            added = rec_added(df, r, rec_month)
            due_date = date(rec_date.year, rec_date.month, r["due_day"])
            status = "✅ Added" if added else ("⏰ Due" if rec_date >= due_date else f"📅 Due on {r['due_day']}")
            if not added:
                pending.append(r)
            c1, c2, c3, c4, c5, c6 = st.columns([3, 2, 3, 2, 1.3, 1])
            c1.write(f"**{r['name']}**")
            c2.write(money(r["amount"]))
            c3.write(cat_label(r["category"]))
            c4.write(status)
            if c5.button("➕ Add", key=f"rec_add_{r['id']}", disabled=added):
                call("POST", "/recurring/apply", json={"date": rec_date.isoformat(), "id": r["id"]})
                st.session_state["msg"] = f"Added {r['name']}"
                st.rerun()
            if c6.button("🗑️", key=f"rec_del_{r['id']}"):
                call("DELETE", f"/recurring/{r['id']}")
                st.rerun()
        if pending and st.button(f"➕ Add all pending ({len(pending)})"):
            for r in pending:
                call("POST", "/recurring/apply", json={"date": rec_date.isoformat(), "id": r["id"]})
            st.session_state["msg"] = f"Added {len(pending)} recurring entries"
            st.rerun()
        if not recs_all:
            st.info("No templates yet. Save one above.")

    # --- Tab 4: bank statement CSV import ---
    with tab4:
        st.caption("Import a bank statement. Debits become expenses and credits become income. "
                   "Rows that already exist are skipped, and categories come from your learned rules and keywords.")
        sample = pd.DataFrame({
            "Date": [(TODAY - timedelta(days=i)).strftime("%d/%m/%Y") for i in (6, 5, 4, 3, 2, 1)],
            "Description": ["UPI-SWIGGY FOOD ORDER", "Metro card recharge", "Amazon shopping", "Salary credit",
                            "Pharmacy medicines", "Netflix subscription"],
            "Amount": [-320, -500, -1299, 25000, -240, -199]})
        st.download_button("⬇️ Download a sample statement", sample.to_csv(index=False),
                           file_name="sample_statement.csv", mime="text/csv")
        up_key = f"csv_upload_{st.session_state.get('up_n', 0)}"
        uploaded = st.file_uploader("Upload your CSV", type="csv", key=up_key)
        if uploaded is not None:
            try:
                raw = pd.read_csv(uploaded)
            except Exception:
                raw = None
                st.error("Could not read this file as a CSV.")
            if raw is not None and not raw.empty:
                cols = list(raw.columns)
                layout_name = st.radio("Amount layout", ["One amount column (negative = expense)",
                                                         "Separate debit and credit columns"], horizontal=True)
                k1, k2, k3 = st.columns(3)
                date_col = k1.selectbox("Date column", cols, index=guess_column(cols, ["date"], 0))
                desc_col = k2.selectbox("Description column", cols,
                                        index=guess_column(cols, ["desc", "narr", "particular", "detail"], min(1, len(cols) - 1)))
                if layout_name.startswith("One"):
                    amt_col = k3.selectbox("Amount column", cols, index=guess_column(cols, ["amount"], len(cols) - 1))
                    rows, skipped = an.statement_to_rows(raw, date_col, desc_col, "single", amount_col=amt_col)
                else:
                    deb = k3.selectbox("Debit column", cols, index=guess_column(cols, ["debit", "withdraw"], 0))
                    cred = k3.selectbox("Credit column", cols, index=guess_column(cols, ["credit", "deposit"], 0))
                    rows, skipped = an.statement_to_rows(raw, date_col, desc_col, "split", debit_col=deb, credit_col=cred)
                st.write(f"**{len(rows)}** rows ready to import" + (f" ({skipped} unreadable rows skipped)" if skipped else ""))
                if rows:
                    st.dataframe(style_table(pd.DataFrame(rows).head(50)), hide_index=True, width="stretch")
                    if st.button("📥 Import now"):
                        res = call("POST", "/transactions/import", json=rows, timeout=60)
                        if res:
                            st.session_state["msg"] = f"Imported {res['added']} rows, skipped {res['skipped']} duplicates"
                        st.session_state["up_n"] = st.session_state.get("up_n", 0) + 1  # reset the uploader
                        st.rerun()

    # --- Transactions: search, filter, sort ---
    st.subheader("📋 Transactions")
    if data:
        f1, f2, f3, f4 = st.columns(4)
        search = f1.text_input("🔎 Search note", key="f_search")
        f_cats = f2.multiselect("Category", CATEGORIES, key="f_cat")
        f_type = f3.selectbox("Type", ["All", "expense", "income"], key="f_type")
        f_pay = f4.multiselect("Paid via", PAYMENT_METHODS, key="f_pay")
        g1, g2, g3 = st.columns(3)
        use_dates = g1.checkbox("Filter by date range", key="f_usedate")
        sort_by = g2.selectbox("Sort by", ["date", "amount", "category", "type"], key="f_sort")
        order = g3.radio("Order", ["Descending", "Ascending"], horizontal=True, key="f_order")

        view = df.copy()
        if search:
            view = view[view["note"].fillna("").str.contains(search, case=False, regex=False)]
        if f_cats:
            view = view[view["category"].isin(f_cats)]
        if f_type != "All":
            view = view[view["type"] == f_type]
        if f_pay:
            view = view[view["payment_method"].isin(f_pay)]
        if use_dates:
            rng = st.date_input("Date range", value=(date.fromisoformat(df["date"].min()),
                                                     date.fromisoformat(df["date"].max())), key="f_range")
            if len(rng) == 2:
                view = view[(view["date"] >= rng[0].isoformat()) & (view["date"] <= rng[1].isoformat())]
        view = view.sort_values(sort_by, ascending=(order == "Ascending"))

        st.caption(f"Showing {len(view)} of {len(df)} transactions")
        st.dataframe(style_table(view.drop(columns=["month"])), hide_index=True, width="stretch")
        st.download_button("⬇️ Download as CSV", view.drop(columns=["month"]).to_csv(index=False),
                           file_name="transactions.csv", mime="text/csv")

        # --- Edit / Delete ---
        st.subheader("✏️ Edit or Delete")
        st.caption("If you change a category, the app remembers it for similar notes next time.")
        rows = view.to_dict("records")
        if rows:
            ids = [t["id"] for t in rows]
            selected_id = st.selectbox(
                "Pick a transaction", ids,
                format_func=lambda i: next(f"#{t['id']} | {t['date']} | {t['type']} | Rs.{t['amount']} | {t['note']}"
                                           for t in rows if t["id"] == i))
            row = next(t for t in rows if t["id"] == selected_id)

            # key includes the id so the fields refresh when you pick another row
            c1, c2 = st.columns(2)
            e_type = c1.radio("Type", ["expense", "income"], horizontal=True,
                              index=0 if row["type"] == "expense" else 1, key=f"type_{selected_id}")
            e_amount = c2.number_input("Amount (Rs.)", min_value=0.0, step=10.0,
                                       value=float(row["amount"]), key=f"amt_{selected_id}")
            e_date = c1.date_input("Date", value=pd.to_datetime(row["date"]).date(), max_value=TODAY,
                                   key=f"date_{selected_id}")
            e_cat = c2.selectbox("Category", CATEGORIES,
                                 index=CATEGORIES.index(row["category"]) if row["category"] in CATEGORIES else 6,
                                 key=f"cat_{selected_id}")
            e_pay = c1.selectbox("Paid via", PAYMENT_METHODS,
                                 index=PAYMENT_METHODS.index(row["payment_method"]) if row["payment_method"] in PAYMENT_METHODS else 0,
                                 key=f"pay_{selected_id}")
            e_note = st.text_input("Note", value=row["note"] or "", key=f"note_{selected_id}")

            b1, b2, _ = st.columns([1, 1, 4])
            if b1.button("Update"):
                call("PUT", f"/transactions/{selected_id}",
                     json={"type": e_type, "amount": e_amount, "date": e_date.isoformat(), "note": e_note,
                           "category": e_cat, "payment_method": e_pay})
                learned = e_cat != row["category"] and e_note.strip()
                st.session_state["msg"] = ("Updated. Learned: similar notes will now be " + e_cat) if learned \
                    else "Transaction updated"
                st.rerun()
            if b2.button("Delete"):
                call("DELETE", f"/transactions/{selected_id}")
                st.session_state["msg"] = "Transaction deleted"
                st.rerun()
        else:
            st.info("No transactions match your filters.")
    else:
        st.info("No transactions yet. Add one above, or use 'Load sample data' in the sidebar.")

# =====================================================
# PAGE 2: Budgets (and savings goals)
# =====================================================
elif page == "💼 Budgets":
    st.subheader("💼 Budgets & Savings Goals")
    tab_b, tab_g = st.tabs(["Monthly budgets", "🎯 Savings goals"])

    with tab_b:
        st.markdown("**Set your monthly budget for each category**")
        with st.form("budget_form"):
            cols = st.columns(4)
            new_budgets = {}
            for i, cat in enumerate(CATEGORIES):
                new_budgets[cat] = cols[i % 4].number_input(f"{cat_label(cat)} (Rs.)", min_value=0.0, step=100.0,
                                                            value=float(budgets.get(cat, 0.0)))
            if st.form_submit_button("Save budgets"):
                call("POST", "/budgets", json=new_budgets)
                st.session_state["msg"] = "Budgets saved"
                st.rerun()

        # Smart suggestion from past spending
        st.markdown("**💡 Smart budget suggestion**")
        if st.button("Suggest budgets from my spending"):
            st.session_state["suggest"] = an.suggest_budgets(df)
        if "suggest" in st.session_state:
            sug = st.session_state["suggest"]
            if sug:
                st.caption(f"Based on {len(sug['months'])} past month(s): needs (Food, Transport, Bills, Health) = "
                           f"typical spend + 5%, wants = typical spend - 15%, total capped at 80% of your average income "
                           f"({money(sug['avg_income'])}).")
                table = pd.DataFrame({"category": CATEGORIES,
                                      "current": [budgets.get(c, 0) for c in CATEGORIES],
                                      "suggested": [sug["budgets"][c] for c in CATEGORIES]})
                st.dataframe(style_table(table), hide_index=True, width="stretch")
                if st.button("Apply suggested budgets"):
                    call("POST", "/budgets", json=sug["budgets"])
                    st.session_state.pop("suggest")
                    st.session_state["msg"] = "Suggested budgets applied"
                    st.rerun()
            else:
                st.info("Not enough expense data yet to suggest budgets.")

        st.markdown("**Budget progress**")
        month = month_picker(df, "budget_month")
        if month is None:
            st.info("No transactions yet.")
        else:
            spent = df[(df["month"] == month) & (df["type"] == "expense")].groupby("category")["amount"].sum()
            shown = False
            for cat in CATEGORIES:
                if budgets.get(cat, 0) > 0:
                    progress_bar(cat, float(spent.get(cat, 0)), budgets[cat])
                    shown = True
            if not shown:
                st.info("Set a budget above to see progress bars.")

    with tab_g:
        with st.form("goal_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            g_name = c1.text_input("Goal name (e.g. New laptop)")
            g_target = c2.number_input("Target amount (Rs.)", min_value=0.0, step=1000.0)
            g_deadline = c3.date_input("Target date", value=TODAY + timedelta(days=180), min_value=TODAY)
            if st.form_submit_button("Create goal"):
                res = call("POST", "/goals", json={"name": g_name, "target": g_target,
                                                   "deadline": g_deadline.isoformat()})
                if res and "message" in res:
                    st.session_state["msg"] = "Goal created"
                    st.rerun()
                else:
                    st.warning("Enter a goal name and a target amount greater than 0.")

        goals = call("GET", "/goals") or []
        if not goals:
            st.info("No goals yet. Create your first one above.")
        for g in goals:
            pct = min(g["saved"] / g["target"] * 100, 100)
            days_left = (date.fromisoformat(g["deadline"]) - TODAY).days
            months_left = max(days_left / 30, 1)
            need = max(g["target"] - g["saved"], 0) / months_left
            st.markdown(
                f"<div class='soft'><b>🎯 {g['name']}</b> &nbsp; {money(g['saved'])} of {money(g['target'])} ({pct:.0f}%)"
                f"<div class='track' style='margin:8px 0'><div style='width:{pct}%;height:12px;border-radius:8px;"
                f"background:linear-gradient(90deg,#14B8A6,#6366F1)'></div></div>"
                f"<small>{max(days_left, 0)} days left · save about {money(need)} per month to reach it</small></div>",
                unsafe_allow_html=True)
            c1, c2, c3 = st.columns([2, 1, 1])
            add_amt = c1.number_input("Add savings (Rs.)", min_value=0.0, step=500.0, key=f"goal_amt_{g['id']}")
            if c2.button("Add savings", key=f"goal_add_{g['id']}") and add_amt > 0:
                call("POST", f"/goals/{g['id']}/add", json={"amount": add_amt})
                st.session_state["msg"] = "Savings added"
                st.rerun()
            if c3.button("Delete goal", key=f"goal_del_{g['id']}"):
                call("DELETE", f"/goals/{g['id']}")
                st.rerun()

# =====================================================
# PAGE 3: Dashboard
# =====================================================
elif page == "📊 Dashboard":
    st.subheader("📊 Dashboard")
    res = period_filter(df, "dash") if not df.empty else None
    if df.empty:
        st.info("No data yet. Add transactions or load sample data from the sidebar.")
    elif res is not None:
        dff, label, n = res
        exp = dff[dff["type"] == "expense"]
        income = dff[dff["type"] == "income"]["amount"].sum()
        expense = exp["amount"].sum()
        by_cat = exp.groupby("category")["amount"].sum()
        budget_total = sum(budgets.values()) * n  # monthly budget x months in the period

        tab_over, tab_fc, tab_wi, tab_al = st.tabs(["Overview", "🔮 Forecast", "🧮 What-if", "🚨 Alerts"])

        # ---------- Overview ----------
        with tab_over:
            # Budget alerts: over budget (red) or 80% used (amber)
            alerts = []
            for c in CATEGORIES:
                limit, spent_c = budgets.get(c, 0) * n, float(by_cat.get(c, 0))
                if limit > 0 and spent_c > limit:
                    alerts.append(f"🔴 **{c}** is over budget by {money(spent_c - limit)}")
                elif limit > 0 and spent_c >= 0.8 * limit:
                    alerts.append(f"🟠 **{c}** has used {spent_c / limit * 100:.0f}% of its budget")
            if alerts:
                st.warning("\n\n".join(alerts))

            c1, c2, c3, c4 = st.columns(4)
            with c1:
                card("Total Income", money(income), "💵", "c-income", label)
            with c2:
                card("Total Expenses", money(expense), "💸", "c-expense", f"{len(exp)} transactions")
            with c3:
                card("Savings", money(income - expense), "🏦", "c-savings",
                     f"Savings rate {(income - expense) / income * 100:.0f}%" if income > 0 else "No income yet")
            with c4:
                card("Budget Left", money(budget_total - expense), "🎯", "c-budget", f"of {money(budget_total)} budget")

            # Health score gauge + formula
            h = an.health_score(income, expense, budgets, by_cat, n)
            g1, g2 = st.columns([1, 1])
            with g1:
                gauge = go.Figure(go.Indicator(
                    mode="gauge+number", value=h["score"],
                    gauge={"axis": {"range": [0, 100]}, "bar": {"color": "#6366F1"},
                           "steps": [{"range": [0, 40], "color": "#FCA5A5"},
                                     {"range": [40, 70], "color": "#FDE68A"},
                                     {"range": [70, 100], "color": "#86EFAC"}]}))
                st.plotly_chart(style_fig(gauge, "Financial health score", 280), width="stretch")
            with g2:
                st.info(
                    "**How the score is calculated**\n\n"
                    "Score = Savings points (max 50) + Budget points (max 50)\n\n"
                    "- Savings points = min(savings rate ÷ 20%, 1) × 50\n"
                    "- Budget points = (categories within budget ÷ categories with a budget) × 50\n\n"
                    f"**Your numbers:** savings rate {h['savings_rate'] * 100:.0f}% → {h['savings_pts']:.0f} pts, "
                    f"{h['within']} of {h['budgeted']} categories within budget → {h['budget_pts']:.0f} pts")

            # Donut chart with the total in the centre
            left, right = st.columns(2)
            with left:
                if by_cat.empty:
                    st.info("No expenses in this period.")
                else:
                    donut = go.Figure(go.Pie(
                        labels=list(by_cat.index), values=list(by_cat.values), hole=0.62,
                        marker=dict(colors=[CATEGORY_COLORS.get(c, "#94A3B8") for c in by_cat.index]),
                        textinfo="percent", hovertemplate="%{label}<br>Rs.%{value:,.0f} (%{percent})<extra></extra>"))
                    donut.update_layout(annotations=[dict(text=f"<b>{money(expense)}</b><br>spent", x=0.5, y=0.5,
                                                          font_size=16, showarrow=False)])
                    st.plotly_chart(style_fig(donut, "Spending by category"), width="stretch")

            # Line chart: monthly trend (all months, selected period highlighted)
            with right:
                trend = df[df["type"] == "expense"].groupby("month")["amount"].sum().reset_index()
                line = go.Figure(go.Scatter(
                    x=trend["month"], y=trend["amount"], mode="lines+markers", name="Monthly spending",
                    line=dict(color="#6366F1", width=3, shape="spline"), fill="tozeroy",
                    fillcolor="rgba(99,102,241,.15)", hovertemplate="%{x}<br>Rs.%{y:,.0f}<extra></extra>"))
                picked = trend[trend["month"].isin(dff["month"].unique())]
                line.add_trace(go.Scatter(x=picked["month"], y=picked["amount"], mode="markers", name="Selected period",
                                          marker=dict(color="#EC4899", size=13), hoverinfo="skip"))
                st.plotly_chart(style_fig(line, "Monthly spending trend"), width="stretch")

            # Bar chart: budget vs actual  |  spending by payment method
            b_left, b_right = st.columns([3, 2])
            with b_left:
                actual = [float(by_cat.get(c, 0)) for c in CATEGORIES]
                bars = go.Figure([
                    go.Bar(x=CATEGORIES, y=[budgets.get(c, 0) * n for c in CATEGORIES], name="Budget",
                           marker_color="rgba(148,163,184,.55)", hovertemplate="%{x} budget<br>Rs.%{y:,.0f}<extra></extra>"),
                    go.Bar(x=CATEGORIES, y=actual, name="Actual", marker_color=[CATEGORY_COLORS[c] for c in CATEGORIES],
                           hovertemplate="%{x} actual<br>Rs.%{y:,.0f}<extra></extra>")])
                bars.update_layout(barmode="group")
                st.plotly_chart(style_fig(bars, "Budget vs actual"), width="stretch")
            with b_right:
                by_pay = exp.groupby("payment_method")["amount"].sum().reindex(PAYMENT_METHODS).fillna(0)
                pay_fig = go.Figure(go.Bar(x=PAYMENT_METHODS, y=list(by_pay.values),
                                           marker_color=[PAYMENT_COLORS[p] for p in PAYMENT_METHODS],
                                           hovertemplate="%{x}<br>Rs.%{y:,.0f}<extra></extra>"))
                st.plotly_chart(style_fig(pay_fig, "Spending by payment method"), width="stretch")

        # ---------- Forecast ----------
        with tab_fc:
            st.caption("Straight-line (linear regression) forecast from your complete past months.")
            all_exp = df[df["type"] == "expense"]
            used = an.complete_months(all_exp)
            if len(used) < 2:
                st.info("Need at least 2 months of expense data to forecast.")
            else:
                totals = all_exp.groupby("month")["amount"].sum().reindex(used).fillna(0)
                predicted = an.linear_forecast(totals.values)
                target = str(pd.Period(used[-1], "M") + 1)
                total_budget = sum(budgets.values())

                m1, m2, m3 = st.columns(3)
                m1.metric(f"Forecast for {target}", money(predicted))
                m2.metric("Total monthly budget", money(total_budget))
                m3.metric("Difference", money(total_budget - predicted))
                if total_budget > 0 and predicted > total_budget:
                    st.warning(f"⚠️ Expected spending ({money(predicted)}) will cross your total budget by "
                               f"{money(predicted - total_budget)}.")
                elif total_budget > 0:
                    st.success("✅ Expected spending stays within your total budget.")

                fc = go.Figure(go.Scatter(x=used, y=list(totals.values), mode="lines+markers", name="Actual",
                                          line=dict(color="#6366F1", width=3, shape="spline")))
                fc.add_trace(go.Scatter(x=[used[-1], target], y=[totals.values[-1], predicted], mode="lines+markers",
                                        name="Forecast", line=dict(color="#EC4899", width=3, dash="dash"),
                                        marker=dict(size=12)))
                if total_budget > 0:
                    fc.add_hline(y=total_budget, line_dash="dot", line_color="#F97316", annotation_text="Budget")
                st.plotly_chart(style_fig(fc, "Monthly spending forecast"), width="stretch")

                # Forecast per category
                pivot = all_exp.pivot_table(index="month", columns="category", values="amount",
                                            aggfunc="sum").reindex(used).fillna(0)
                rows = []
                for cat in pivot.columns:
                    pred_c, b = an.linear_forecast(pivot[cat].values), budgets.get(cat, 0)
                    rows.append({"category": cat, "forecast": round(pred_c), "budget": round(b),
                                 "status": "⚠️ May go over" if b > 0 and pred_c > b else "✅ OK"})
                st.dataframe(style_table(pd.DataFrame(rows)), hide_index=True, width="stretch")

        # ---------- What-if simulator ----------
        with tab_wi:
            st.caption("Drag a slider to see how much you would save if you cut that category.")
            all_exp = df[df["type"] == "expense"]
            used = an.complete_months(all_exp)
            avg = all_exp[all_exp["month"].isin(used)].groupby("category")["amount"].sum() / max(len(used), 1)
            cols = st.columns(4)
            cuts = {}
            for i, cat in enumerate(CATEGORIES):
                cuts[cat] = cols[i % 4].slider(f"{cat_label(cat)}", 0, 100, 0, step=5, format="%d%%", key=f"cut_{cat}")
            monthly_save = sum(avg.get(c, 0) * cuts[c] / 100 for c in CATEGORIES)
            s1, s2 = st.columns(2)
            with s1:
                card("Monthly saving", money(monthly_save), "💰", "c-income", "based on your average month")
            with s2:
                card("Yearly saving", money(monthly_save * 12), "🚀", "c-savings", "monthly saving × 12")
            before = [float(avg.get(c, 0)) for c in CATEGORIES]
            after = [b * (1 - cuts[c] / 100) for b, c in zip(before, CATEGORIES)]
            sim = go.Figure([
                go.Bar(x=CATEGORIES, y=before, name="Now", marker_color="rgba(148,163,184,.55)"),
                go.Bar(x=CATEGORIES, y=after, name="After cut", marker_color=[CATEGORY_COLORS[c] for c in CATEGORIES])])
            sim.update_layout(barmode="group")
            st.plotly_chart(style_fig(sim, "Average monthly spending: now vs after cut"), width="stretch")

        # ---------- Unusual-spending alerts ----------
        with tab_al:
            st.caption("Rule: an expense is flagged when it is above (mean + 2 × standard deviation) "
                       "of its own category. Needs 5+ expenses in that category.")
            found = an.find_anomalies(df)
            found = found[found["id"].isin(dff["id"])]  # only the selected period
            if found.empty:
                st.success("✅ No unusual spending found in this period.")
            else:
                st.warning(f"🚨 {len(found)} unusual transaction(s) found in this period.")
                st.dataframe(style_table(found.drop(columns=["id"]).round(0)), hide_index=True, width="stretch")
                if st.button("🤖 Explain with AI"):
                    items = found[["date", "note", "category", "amount", "usual_avg"]].to_dict("records")
                    with st.spinner("Thinking..."):
                        r = call("POST", "/anomalies/explain", json={"items": items}, timeout=60)
                    if r:
                        st.markdown(f"<div class='soft'>{r['explanation']}</div>".replace("\n", "<br>"),
                                    unsafe_allow_html=True)

# =====================================================
# PAGE 4: AI Insights
# =====================================================
elif page == "🤖 AI Insights":
    st.subheader("🤖 AI Insights")
    month = month_picker(df, "ai_month")
    if month is None:
        st.info("No data yet. Add transactions or load sample data from the sidebar.")
    else:
        m = df[df["month"] == month]
        income = m[m["type"] == "income"]["amount"].sum()
        by_cat = m[m["type"] == "expense"].groupby("category")["amount"].sum()
        expense = by_cat.sum()

        # --- Monthly summary card + 3 personalised tips ---
        if st.button("✨ Generate monthly summary"):
            with st.spinner("Analysing your spending..."):
                r = call("GET", "/insights/summary", params={"month": month}, timeout=60)
            if r:
                st.session_state["insight"] = {"month": month, **r}
        ins = st.session_state.get("insight")
        if ins and ins["month"] == month:
            st.markdown(f"<div class='soft'><b>📝 Summary for {month}</b><br>{ins['summary']}</div>",
                        unsafe_allow_html=True)
            if ins["tips"]:
                st.markdown("**💡 Your saving tips**")
                for col, tip in zip(st.columns(len(ins["tips"])), ins["tips"]):
                    col.markdown(f"<div class='tip'>{tip}</div>", unsafe_allow_html=True)
        else:
            st.info("Click 'Generate monthly summary' to get your AI summary and saving tips.")

        # --- Top 3 overspending categories (spent minus budget) ---
        st.markdown("**🔥 Top 3 overspending categories**")
        gap = {c: float(by_cat.get(c, 0)) - budgets.get(c, 0) for c in CATEGORIES if by_cat.get(c, 0) > 0}
        top3 = sorted(gap, key=gap.get, reverse=True)[:3]
        for col, cat in zip(st.columns(3), top3):
            over = gap[cat] > 0
            note = f"Over budget by {money(gap[cat])}" if over else f"Within budget ({money(-gap[cat])} left)"
            col.markdown(
                f"<div class='soft' style='border-left:6px solid {CATEGORY_COLORS[cat]}'>"
                f"<b>{cat_label(cat)}</b><br>Spent {money(by_cat[cat])} · Budget {money(budgets.get(cat, 0))}<br>"
                f"<span style='color:{'#DC2626' if over else '#16A34A'}'>{note}</span></div>", unsafe_allow_html=True)

        # --- Monthly report PDF ---
        try:
            score = an.health_score(income, expense, budgets, by_cat)["score"]
            same = ins and ins["month"] == month
            pdf_bytes = an.make_pdf(month, income, expense, score, by_cat, budgets,
                                    ins["summary"] if same else "", ins["tips"] if same else None)
            st.download_button("📄 Download monthly report (PDF)", pdf_bytes,
                               file_name=f"report_{month}.pdf", mime="application/pdf")
        except Exception:
            st.warning("Could not create the PDF report. Check that fpdf2 is installed.")

        # --- Chat: answers only from the real database data ---
        st.markdown("**💬 Ask about your spending**")
        st.caption("The AI answers only from your real data.")
        if "chat" not in st.session_state:
            st.session_state["chat"] = []

        quick = ["Where do I spend most?", "Am I saving enough?", "Which month did I spend the most?",
                 "Where can I cut spending?"]
        for col, q in zip(st.columns(len(quick)), quick):
            if col.button(q, key=f"quick_{q}"):
                st.session_state["pending_q"] = q
                st.rerun()

        for msg in st.session_state["chat"]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        question = st.chat_input("Where do I spend most?") or st.session_state.pop("pending_q", None)
        if question:
            st.session_state["chat"].append({"role": "user", "content": question})
            r = call("POST", "/ask", json={"question": question}, timeout=60)
            answer = r.get("answer") if r else "Sorry, something went wrong."
            st.session_state["chat"].append({"role": "assistant", "content": answer})
            st.rerun()