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

# Ensure stopwords are downloaded
try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords')

# === Streamlit Config ===
st.set_page_config(page_title="GSC Analyzer", layout="wide")
st.title("📊 Google Search Console Keyword Optimizer")

st.sidebar.header("🔐 Google Login & Setup")
MAX_RESULTS = st.sidebar.slider("Max rows to analyze", 5, 100, 10)

# === Google Auth ===
if not os.path.exists("credentials.json"):
    st.error("❌ 'credentials.json' file is missing.")
    st.stop()

SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']

if st.sidebar.button("🔄 Disconnect Google Account"):
    if os.path.exists("token.json"):
        os.remove("token.json")
    try:
        st.experimental_rerun()
    except AttributeError:
        st.warning("Please manually refresh the page.")

creds = None
if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)

if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        # Step 1: Begin flow if not already started
        if "auth_flow" not in st.session_state:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            auth_url, _ = flow.authorization_url(prompt='consent')
            st.session_state.auth_flow = flow
            st.sidebar.markdown(f"🔗 [Click here to connect Google Account]({auth_url})")
            st.sidebar.info("After authorizing, paste the code below.")
            st.stop()

        # Step 2: Ask for auth code after user visits link
        auth_code = st.sidebar.text_input("🔑 Paste the authorization code here:")

        if auth_code:
            try:
                st.session_state.auth_flow.fetch_token(code=auth_code)
                creds = st.session_state.auth_flow.credentials
                with open("token.json", "w") as token:
                    token.write(creds.to_json())
                st.success("✅ Successfully connected to Google Search Console!")
                st.experimental_rerun()
            except Exception as e:
                st.error(f"❌ Authentication failed: {e}")
                del st.session_state["auth_flow"]
                st.stop()
        else:
            st.warning("Paste the code you get after login.")
            st.stop()



# === Build GSC Service ===
service = build("webmasters", "v3", credentials=creds)
site_list = service.sites().list().execute()
verified_sites = [s["siteUrl"] for s in site_list.get("siteEntry", []) if s.get("permissionLevel") == "siteFullUser"]

if not verified_sites:
    st.error("No verified GSC properties found.")
    st.stop()

selected_site = st.sidebar.selectbox("🌐 Select a GSC Property", verified_sites)
st.success(f"✅ Connected to: {selected_site}")

# === GSC Query ===
end_date = datetime.date.today()
start_date = end_date - relativedelta.relativedelta(months=16)
home_regex = f"^{selected_site}$"
request_body = {
    "startDate": start_date.strftime("%Y-%m-%d"),
    "endDate": end_date.strftime("%Y-%m-%d"),
    "dimensions": ["page", "query"],
    "dimensionFilterGroups": [{
        "filters": [
            {"dimension": "page", "operator": "excludingRegex", "expression": home_regex},
            {"dimension": "query", "operator": "excludingRegex", "expression": "natzir|analistaseo|analista seo"}
        ]
    }],
    "rowLimit": 25000
}

response = service.searchanalytics().query(siteUrl=selected_site, body=request_body).execute()
rows = response.get("rows", [])
if not rows:
    st.warning("No data found.")
    st.stop()

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

# === Clean Text ===
lang = "spanish"
stop_words = set(stopwords.words(lang))

def clean_text(text):
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = re.sub(r"\d+|[^\w\s]+", "", text)
    return " ".join([w for w in text.split() if w not in stop_words])

for col in ["title", "meta", "h1", "query"]:
    df[f"{col}_clean"] = df[col].apply(clean_text)

for col in ["title", "meta", "h1"]:
    df[f"{col}_similarity"] = df.apply(
        lambda row: fuzz.token_set_ratio(row["query_clean"], row[f"{col}_clean"]), axis=1)

df.drop(columns=[col for col in df.columns if col.endswith("_clean")], inplace=True)

# === Tabs ===
tab1, tab2 = st.tabs(["📊 GSC Data", "🧠 AI Suggestions"])

with tab1:
    st.dataframe(df, use_container_width=True)

with tab2:
    st.subheader("🧠 AI Content Suggestions")
    openai_key = st.text_input("🔑 Enter your OpenAI API key", type="password")
    generate_btn = st.button("🚀 Generate AI Optimized Content")

    if openai_key and generate_btn:
        from openai import OpenAI
        client = OpenAI(api_key=openai_key)

        def generar(prompt):
            try:
                return client.chat.completions.create(
                    model="gpt-4o",
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}]
                ).choices[0].message.content.strip()
            except Exception as e:
                return f"Error: {e}"

        PROMPTS = {
            "new_title": "Generate title under 60 chars in {lang}, include '{query}', no repetition. Use: '{title}'",
            "new_meta": "Generate meta under 160 chars in {lang}, include '{query}', engaging. Use: '{meta}'",
            "new_h1": "Generate H1 under 70 chars in {lang}, include '{query}'. Use: '{h1}'"
        }

        filtered_df = df[df["title_similarity"] <= 60].copy()
        st.info(f"Found {len(filtered_df)} rows with low title similarity")

        for idx, row in filtered_df.iterrows():
            query = row["query"]
            for field, template in PROMPTS.items():
                base_col = field.replace("new_", "")
                prompt = template.format(query=query, lang="English", **{base_col: row[base_col]})
                df.at[idx, field] = generar(prompt)

        def strip_quotes(text):
            return text[1:-1] if isinstance(text, str) and text.startswith('"') and text.endswith('"') else text

        for col in ["new_title", "new_meta", "new_h1"]:
            df[col] = df[col].apply(strip_quotes)

        st.success("✅ AI content generated!")
        st.dataframe(df[df["new_title"].notnull()][['query', 'page', 'title', 'new_title', 'meta', 'new_meta', 'h1', 'new_h1']], use_container_width=True)
        st.download_button("⬇ Download Enhanced CSV", df.to_csv(index=False), "gsc_optimized.csv", "text/csv")

    elif generate_btn and not openai_key:
        st.warning("⚠ Please enter your OpenAI API key.")
