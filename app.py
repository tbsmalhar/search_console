import os, datetime, re, unicodedata, requests, chardet
import streamlit as st
import pandas as pd
from bs4 import BeautifulSoup
from collections import defaultdict
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dateutil import relativedelta
from nltk.corpus import stopwords
from fuzzywuzzy import fuzz
import nltk

# Ensure stopwords are available
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords')

# === Streamlit Config ===
st.set_page_config(page_title="GSC Analyzer", layout="wide")
st.title("📊 Google Search Console Keyword Analyzer")

# === Sidebar Config ===
st.sidebar.header("🔐 Google Login & Setup")

# Max result slider
MAX_RESULTS = st.sidebar.slider("Max rows to analyze", 5, 100, 10)

# === Google Auth Flow ===
if not os.path.exists("credentials.json"):
    st.error("❌ 'credentials.json' file is missing in the current folder.")
    st.stop()

SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
creds = None

if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)

from google_auth_oauthlib.flow import InstalledAppFlow

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if st.sidebar.button("🔗 Connect to Google"):
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            auth_url, _ = flow.authorization_url(prompt='consent')
            st.markdown(f"🔗 [Click here to authorize Google Account]({auth_url})")
            auth_code = st.text_input("Paste the authorization code here:")
            if auth_code:
                try:
                    flow.fetch_token(code=auth_code)
                    creds = flow.credentials
                    with open("token.json", "w") as token:
                        token.write(creds.to_json())
                    st.success("✅ Connected successfully!")
                    st.experimental_rerun()
                except Exception as e:
                    st.error(f"❌ Authentication failed: {e}")
                    st.stop()
            else:
                st.warning("Please paste the authorization code to continue.")
                st.stop()
        else:
            st.warning("Please click 'Connect to Google' to authorize.")
            st.stop()

# === Build API service ===
service = build("webmasters", "v3", credentials=creds)

# === Get list of verified sites ===
site_list = service.sites().list().execute()
verified_sites = [s["siteUrl"] for s in site_list.get("siteEntry", []) if s.get("permissionLevel") == "siteFullUser"]

if not verified_sites:
    st.error("❌ No verified GSC properties found.")
    st.stop()

# === Site selection ===
selected_site = st.sidebar.selectbox("🌐 Select a GSC Property", verified_sites)
st.success(f"✅ Connected to: {selected_site}")

# === Date Range for GSC ===
end_date = datetime.date.today()
start_date = end_date - relativedelta.relativedelta(months=16)
home_regex = f"^{selected_site}$"
branded_queries = "natzir|analistaseo|analista seo"

request_body = {
    "startDate": start_date.strftime("%Y-%m-%d"),
    "endDate": end_date.strftime("%Y-%m-%d"),
    "dimensions": ["page", "query"],
    "dimensionFilterGroups": [{
        "filters": [
            {"dimension": "page", "operator": "excludingRegex", "expression": home_regex},
            {"dimension": "query", "operator": "excludingRegex", "expression": branded_queries}
        ]
    }],
    "rowLimit": 25000
}

# === Fetch GSC Data ===
try:
    response = service.searchanalytics().query(siteUrl=selected_site, body=request_body).execute()
    rows = response.get("rows", [])
    if not rows:
        st.warning("No data found for the selected property.")
        st.stop()
except Exception as e:
    st.error(f"Error fetching GSC data: {e}")
    st.stop()

# === Parse into DataFrame ===
data = defaultdict(list)
for row in rows:
    keys = row.get("keys", [])
    data["page"].append(keys[0] if len(keys) > 0 else "")
    data["query"].append(keys[1] if len(keys) > 1 else "")
    for metric in ["clicks", "ctr", "impressions", "position"]:
        data[metric].append(row.get(metric, 0))

df = pd.DataFrame(data)
df["clicks"] = df["clicks"].astype(int)
df["ctr"] *= 100
df["impressions"] = df["impressions"].astype(int)
df["position"] = df["position"].round(2)
df = df.sort_values("clicks", ascending=False).drop_duplicates("page").head(MAX_RESULTS)

st.info(f"🔍 Loaded {len(df)} rows from GSC.")

# === Metadata Extraction ===
def get_meta(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        content = r.content.decode(chardet.detect(r.content)["encoding"] or "utf-8")
        soup = BeautifulSoup(content, "html.parser")
        return (
            soup.title.get_text() if soup.title else "No title",
            soup.select_one("meta[name='description']")["content"] if soup.select_one("meta[name='description']") else "No meta",
            soup.h1.get_text() if soup.h1 else "No h1"
        )
    except:
        return "Error", "Error", "Error"

df[["title", "meta", "h1"]] = pd.DataFrame(df["page"].apply(get_meta).tolist(), index=df.index)

# === Clean Text for Fuzzy Match ===
lang = "spanish"
stop_words = set(stopwords.words(lang))

def clean_text(text):
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = re.sub(r"\d+|[^\w\s]+", "", text)
    return " ".join([w for w in text.split() if w not in stop_words])

for col in ["title", "meta", "h1", "query"]:
    df[f"{col}_clean"] = df[col].apply(clean_text)

# === Fuzzy Matching ===
for col in ["title", "meta", "h1"]:
    df[f"{col}_similarity"] = df.apply(
        lambda row: fuzz.token_set_ratio(row["query_clean"], row[f"{col}_clean"]), axis=1)

# Drop temporary _clean columns
df.drop(columns=[col for col in df.columns if col.endswith("_clean")], inplace=True)

# === Display Results ===
st.subheader("📋 Results Table")
st.dataframe(df, use_container_width=True)

# Download Button
st.download_button("⬇ Download CSV", df.to_csv(index=False), "gsc_data.csv", mime="text/csv")
