# app_competitor.py — Competitor INCI Explorer (Google Sheets backend)
# ------------------------------------------------------------
# Features
# - Add competitor product info (brand, product name, category, type)
# - Paste INCI list (comma/newline separated)
# - Auto-link each INCI into Ingredients master table
# - Explore by category/product type → see detailed competitor INCI usage
# - Ingredient frequency analysis

import json
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Competitor INCI Explorer", layout="wide")
st.title("🔎 Competitor INCI Explorer")

# ------------------------------
# Google Sheets Client
# ------------------------------

def get_client():
    cfg = st.secrets.get("gsheets")
    if not cfg:
        st.error("Secrets missing: [gsheets] not found.")
        st.stop()

    sa = cfg.get("service_account")
    if isinstance(sa, str):
        sa_info = json.loads(sa)
    else:
        sa_info = sa

    sid = cfg.get("spreadsheet_id")
    if not sid or "/" in sid:
        st.error("Use only the spreadsheet ID, not the full URL.")
        st.stop()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(sa_info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc, sid

TEMPLATE = {
    "Brands": ["id", "name"],
    "Products": ["id", "brand_id", "product_name", "category", "product_type", "notes"],
    "Ingredients": ["id", "inci_name", "default_function", "cas"],
    "Product_Ingredients": ["id", "product_id", "ingredient_id", "inci_name_raw", "function_override", "percentage", "notes"]
}

def ws_to_df(ws):
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=[])
    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    for col in ["id","brand_id","ingredient_id","product_id","percentage"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def df_append(ws, row):
    headers = ws.row_values(1)
    out = [str(row.get(h, "")) for h in headers]
    ws.append_row(out)

# Starter auto-function mapping (expand as needed)
FUNCTION_MAP = {
    "aqua": "Solvent",
    "water": "Solvent",
    "dimethicone": "Emollient",
    "cyclopentasiloxane": "Emollient",
    "ethylhexyl methoxycinnamate": "UV Filter",
    "titanium dioxide": "UV Filter",
    "glycerin": "Humectant",
    "butylene glycol": "Humectant",
    "phenoxyethanol": "Preservative",
    "ethylhexylglycerin": "Preservative booster",
}

# ------------------------------
# Connect and Load
# ------------------------------
try:
    gc, SSID = get_client()
    sh = gc.open_by_key(SSID)
    tabs = [ws.title for ws in sh.worksheets()]
    # Ensure tabs exist
    for t in TEMPLATE:
        if t not in tabs:
            sh.add_worksheet(title=t, rows=1000, cols=20)
            sh.worksheet(t).append_row(TEMPLATE[t])
    ws_brands = sh.worksheet("Brands")
    ws_prods = sh.worksheet("Products")
    ws_ings = sh.worksheet("Ingredients")
    ws_pi = sh.worksheet("Product_Ingredients")

    df_brands = ws_to_df(ws_brands)
    df_prods = ws_to_df(ws_prods)
    df_ings = ws_to_df(ws_ings)
    df_pi = ws_to_df(ws_pi)
except Exception as e:
    st.error(f"❌ Could not connect to Google Sheets: {e}")
    st.stop()

# ------------------------------
# Sidebar Filters
# ------------------------------
with st.sidebar:
    st.header("Filters")
    cats = ["(All)"] + sorted([c for c in df_prods.get("category", pd.Series(dtype=str)).dropna().unique().tolist() if c])
    sel_cat = st.selectbox("Category", cats)
    sel_cat_q = None if sel_cat == "(All)" else sel_cat

    if sel_cat_q:
        ptypes = ["(All)"] + sorted(df_prods[df_prods["category"]==sel_cat_q]["product_type"].dropna().unique().tolist())
    else:
        ptypes = ["(All)"] + sorted(df_prods.get("product_type", pd.Series(dtype=str)).dropna().unique().tolist())
    sel_ptype = st.selectbox("Product Type", ptypes)
    sel_ptype_q = None if sel_ptype == "(All)" else sel_ptype

# ------------------------------
# Data Entry Form
# ------------------------------
st.markdown("---")
st.header("➕ Add Competitor Product")
with st.form("add_competitor"):
    colA, colB = st.columns(2)
    with colA:
        brand_name = st.text_input("Brand")
        prod_name = st.text_input("Product Name")
        prod_cat = st.text_input("Category", placeholder="Bodycare, Skincare, etc.")
        prod_type = st.text_input("Product Type", placeholder="Body Wash, Serum, etc.")
    with colB:
        prod_notes = st.text_area("Notes")
        inci_raw = st.text_area("INCI list (comma or newline separated)", height=120)
    submitted = st.form_submit_button("Save to Google Sheets")

if submitted:
    try:
        # Ensure brand exists
        exists = df_brands[df_brands["name"].str.lower()==brand_name.lower()] if not df_brands.empty else pd.DataFrame()
        if exists.empty:
            next_bid = 1 if df_brands.empty else int(pd.to_numeric(df_brands["id"], errors='coerce').max())+1
            df_append(ws_brands, {"id": next_bid, "name": brand_name})
            df_brands.loc[len(df_brands)] = {"id": next_bid, "name": brand_name}
            bid = next_bid
        else:
            bid = int(exists.iloc[0]["id"])

        # Add product
        next_pid = 1 if df_prods.empty else int(pd.to_numeric(df_prods["id"], errors='coerce').max())+1
        df_append(ws_prods, {
            "id": next_pid,
            "brand_id": bid,
            "product_name": prod_name,
            "category": prod_cat,
            "product_type": prod_type,
            "notes": prod_notes
        })
        df_prods.loc[len(df_prods)] = {"id": next_pid, "brand_id": bid, "product_name": prod_name,
                                       "category": prod_cat, "product_type": prod_type, "notes": prod_notes}

        # Split INCI list
        tokens = []
        for line in inci_raw.replace("\r", "\n").split("\n"):
            for part in line.split(","):
                name = part.strip()
                if name:
                    tokens.append(name)

        next_ing_id = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max())+1
        next_pi_id = 1 if df_pi.empty else int(pd.to_numeric(df_pi["id"], errors='coerce').max())+1

        for inci in tokens:
            inci_norm = inci.strip()
            exists = df_ings[df_ings["inci_name"].str.lower()==inci_norm.lower()] if not df_ings.empty else pd.DataFrame()
            if exists.empty:
                func = FUNCTION_MAP.get(inci_norm.lower(), "")
                df_append(ws_ings, {"id": next_ing_id, "inci_name": inci_norm, "default_function": func, "cas": ""})
                df_ings.loc[len(df_ings)] = {"id": next_ing_id, "inci_name": inci_norm, "default_function": func, "cas": ""}
                ing_id = next_ing_id
                next_ing_id += 1
            else:
                ing_id = int(exists.iloc[0]["id"])

            df_append(ws_pi, {
                "id": next_pi_id,
                "product_id": next_pid,
                "ingredient_id": ing_id,
                "inci_name_raw": inci_norm,
                "function_override": "",
                "percentage": "",
                "notes": ""
            })
            df_pi.loc[len(df_pi)] = {"id": next_pi_id, "product_id": next_pid, "ingredient_id": ing_id,
                                     "inci_name_raw": inci_norm, "function_override": "", "percentage": "", "notes": ""}
            next_pi_id += 1

        st.success(f"Saved product '{prod_name}' with {len(tokens)} ingredients.")
    except Exception as e:
        st.error(f"Failed to save: {e}")

# ------------------------------
# Explore
# ------------------------------
st.markdown("---")
st.header("📊 Explore Competitor Products")

_dfv = df_prods.copy()
if sel_cat_q: _dfv = _dfv[_dfv["category"]==sel_cat_q]
if sel_ptype_q: _dfv = _dfv[_dfv["product_type"]==sel_ptype_q]

# Map brand_id → brand name
id_to_brand = {int(r["id"]): r["name"] for _, r in df_brands.iterrows() if pd.notna(r.get("id")) and pd.notna(r.get("name"))}
_dfv["brand"] = _dfv.get("brand_id", pd.Series(dtype=float)).map(lambda v: id_to_brand.get(int(v), "") if pd.notna(v) else "")

st.dataframe(_dfv[["id","brand","product_name","category","product_type","notes"]].reset_index(drop=True), use_container_width=True, hide_index=True)

sel_ids = st.multiselect("Select product IDs to view details", _dfv.get("id", pd.Series(dtype=float)).dropna().astype(int).tolist())
if sel_ids:
    for pid in sel_ids:
        st.subheader(f"Ingredients — Product ID {pid}")
        df_one = df_pi[df_pi["product_id"].astype(float)==float(pid)]
        merged = df_one.merge(df_ings, left_on="ingredient_id", right_on="id", how="left", suffixes=("","_ing"))
        show = merged[["inci_name_raw","default_function","function_override","percentage","notes"]]
        show.columns = ["INCI","Default Function","Override Function","%","Notes"]
        st.dataframe(show.reset_index(drop=True), use_container_width=True, hide_index=True)

# ------------------------------
# Ingredient Frequency
# ------------------------------
st.markdown("---")
st.subheader("Ingredient Frequency in Current Scope")

fi = df_pi.merge(df_prods, left_on="product_id", right_on="id", how="left", suffixes=("","_prod"))
if sel_cat_q: fi = fi[fi["category"]==sel_cat_q]
if sel_ptype_q: fi = fi[fi["product_type"]==sel_ptype_q]

freq = fi.groupby("ingredient_id").agg(Count=("product_id","nunique")).reset_index()
freq = freq.merge(df_ings, left_on="ingredient_id", right_on="id", how="left")
freq = freq[["inci_name","default_function","Count"]].sort_values(["Count","inci_name"], ascending=[False,True])
st.dataframe(freq.reset_index(drop=True), use_container_width=True, hide_index=True)