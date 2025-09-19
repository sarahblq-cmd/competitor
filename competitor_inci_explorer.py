# app_competitor.py — Competitor INCI Explorer (Google Sheets backend, cached)
# ---------------------------------------------------------------------------
# What this does
# - Add competitor products (brand, product name, category, product_type)
# - Paste INCI list (comma/newline) → auto-links to Ingredients master
# - Explore products by Category / Product Type; view detailed INCI + functions
# - Ingredient frequency across selected scope
# - Caching to avoid Google Sheets 429 (quota) with manual Refresh button
# - Diagnostics expander to verify connection/tabs
#
# Requirements (requirements.txt)
#   streamlit
#   pandas
#   gspread
#   google-auth
#
# Secrets (local: .streamlit/secrets.toml; Cloud: App → Settings → Secrets)
# [gsheets]
# spreadsheet_id = "YOUR_SHEET_ID"
# [gsheets.service_account]
# type = "service_account"
# project_id = "..."
# private_key_id = "..."
# private_key = """
# -----BEGIN PRIVATE KEY-----
# (full multi-line key)
# -----END PRIVATE KEY-----
# """
# client_email = "...@...iam.gserviceaccount.com"
# client_id = "..."
# auth_uri = "https://accounts.google.com/o/oauth2/auth"
# token_uri = "https://oauth2.googleapis.com/token"
# auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
# client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/..."
# ---------------------------------------------------------------------------

import json
import time
from functools import wraps
from typing import Dict, Any

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Competitor INCI Explorer", layout="wide")
st.title("🔎 Competitor INCI Explorer")

# ---------------------------------------------------------------------------
# Quota-friendly: retry helper + caching layers
# ---------------------------------------------------------------------------

def with_backoff(fn):
    @wraps(fn)
    def _inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            if "429" in str(e):
                time.sleep(1.5)
                return fn(*args, **kwargs)
            raise
    return _inner

@st.cache_resource
def get_gc_and_sheet():
    cfg = st.secrets["gsheets"]
    sa = cfg["service_account"] if isinstance(cfg["service_account"], dict) else json.loads(cfg["service_account"])
    creds = Credentials.from_service_account_info(sa, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(cfg["spreadsheet_id"])
    return gc, sh

TEMPLATE = {
    "Brands": ["id", "name"],
    "Products": ["id", "brand_id", "product_name", "category", "product_type", "notes"],
    "Ingredients": ["id", "inci_name", "default_function", "cas"],
    "Product_Ingredients": ["id", "product_id", "ingredient_id", "inci_name_raw", "function_override", "percentage", "notes"],
}

@st.cache_data(ttl=60)
@with_backoff
def load_tab(name: str):
    _, sh = get_gc_and_sheet()
    tabs = [ws.title for ws in sh.worksheets()]
    if name not in tabs:
        ws = sh.add_worksheet(title=name, rows=2000, cols=20)
        ws.append_row(TEMPLATE[name])
    else:
        ws = sh.worksheet(name)
    vals = ws.get_all_values()
    headers = vals[0] if vals else TEMPLATE[name]
    rows = vals[1:] if len(vals) > 1 else []
    df = pd.DataFrame(rows, columns=headers)
    for col in ["id","brand_id","product_id","ingredient_id","percentage"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return ws, df

@with_backoff
def append_row(ws, row: Dict[str, Any]):
    headers = ws.row_values(1)
    ws.append_row([str(row.get(h, "")) for h in headers])

# Starter function mapping (expand as you go)
FUNCTION_MAP = {
    "aqua": "Solvent",
    "water": "Solvent",
    "glycerin": "Humectant",
    "butylene glycol": "Humectant",
    "dimethicone": "Emollient",
    "cyclopentasiloxane": "Emollient",
    "titanium dioxide": "UV Filter",
    "ethylhexyl methoxycinnamate": "UV Filter",
    "phenoxyethanol": "Preservative",
    "ethylhexylglycerin": "Preservative booster",
}

# ---------------------------------------------------------------------------
# Diagnostics (on-demand)
# ---------------------------------------------------------------------------
with st.expander("🧪 Diagnostics"):
    if st.button("Run connectivity check"):
        try:
            _, sh = get_gc_and_sheet()
            st.success("Connected to spreadsheet.")
            st.write("Tabs:", [w.title for w in sh.worksheets()])
        except Exception as e:
            st.error(f"Connection failed: {e}")

# Sidebar controls: Filters + Refresh
with st.sidebar:
    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.experimental_rerun()

# Load tabs (cached)
try:
    ws_brands, df_brands = load_tab("Brands")
    ws_prods, df_prods = load_tab("Products")
    ws_ings,  df_ings  = load_tab("Ingredients")
    ws_pi,    df_pi    = load_tab("Product_Ingredients")
except Exception as e:
    st.error(f"❌ Could not connect/load: {e}")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar Filters
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Data Entry: Add Competitor Product
# ---------------------------------------------------------------------------
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
        # Ensure brand
        ...
        # Split INCI list
        tokens = []
        for line in inci_raw.replace("\r", "\n").split("\n"):
            for part in line.split(","):
                name = part.strip()
                if name:
                    tokens.append(name)

        # process tokens...

        st.cache_data.clear()
        st.success(f"Saved product '{prod_name}' with {len(tokens)} ingredients.")

    except Exception as e:
        st.error(f"Failed to save: {e}")



        # Prepare next IDs
        next_ing_id = 1 if df_ings.empty else int(pd.to_numeric(df_ings["id"], errors='coerce').max()) + 1
        next_pi_id  = 1 if df_pi.empty   else int(pd.to_numeric(df_pi["id"],   errors='coerce').max()) + 1

        for inci in tokens:
            inci_norm = inci.strip()
            row_match = df_ings[df_ings.get("inci_name", pd.Series(dtype=str)).str.lower()==inci_norm.lower()] if not df_ings.empty else pd.DataFrame()
            if row_match.empty:
                func = FUNCTION_MAP.get(inci_norm.lower(), "")
                append_row(ws_ings, {"id": next_ing_id, "inci_name": inci_norm, "default_function": func, "cas": ""})
                df_ings.loc[len(df_ings)] = {"id": next_ing_id, "inci_name": inci_norm, "default_function": func, "cas": ""}
                ing_id = next_ing_id
                next_ing_id += 1
            else:
                ing_id = int(row_match.iloc[0]["id"]) if pd.notna(row_match.iloc[0]["id"]) else None
                if ing_id is None:
                    func = FUNCTION_MAP.get(inci_norm.lower(), "")
                    append_row(ws_ings, {"id": next_ing_id, "inci_name": inci_norm, "default_function": func, "cas": ""})
                    df_ings.loc[len(df_ings)] = {"id": next_ing_id, "inci_name": inci_norm, "default_function": func, "cas": ""}
                    ing_id = next_ing_id
                    next_ing_id += 1

            append_row(ws_pi, {
                "id": next_pi_id,
                "product_id": next_pid,
                "ingredient_id": ing_id,
                "inci_name_raw": inci_norm,
                "function_override": "",
                "percentage": "",
                "notes": ""
            })
            df_pi.loc[len(df_pi)] = {
                "id": next_pi_id, "product_id": next_pid, "ingredient_id": ing_id,
                "inci_name_raw": inci_norm, "function_override": "", "percentage": "", "notes": ""
            }
            next_pi_id += 1

        # Clear cached data so UI reflects updates
        st.cache_data.clear()
        st.success(f"Saved product '{prod_name}' with {len(tokens)} ingredients.")
    except Exception as e:
        st.error(f"Failed to save: {e}")

# ---------------------------------------------------------------------------
# Explore view
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Ingredient Frequency
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Ingredient Frequency in Current Scope")

fi = df_pi.merge(df_prods, left_on="product_id", right_on="id", how="left", suffixes=("","_prod"))
if sel_cat_q: fi = fi[fi["category"]==sel_cat_q]
if sel_ptype_q: fi = fi[fi["product_type"]==sel_ptype_q]

freq = fi.groupby("ingredient_id").agg(Count=("product_id","nunique")).reset_index()
freq = freq.merge(df_ings, left_on="ingredient_id", right_on="id", how="left")
freq = freq[["inci_name","default_function","Count"]].sort_values(["Count","inci_name"], ascending=[False,True])
st.dataframe(freq.reset_index(drop=True), use_container_width=True, hide_index=True)
