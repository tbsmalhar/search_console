from flask import Flask, redirect, request, session, url_for, render_template, send_file
import os
import io
import datetime
import re
import unicodedata
from collections import defaultdict
import pandas as pd
import requests
from bs4 import BeautifulSoup
import chardet
from dateutil import relativedelta
from nltk.corpus import stopwords
from fuzzywuzzy import fuzz
from google.oauth2.credentials import Credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery

app = Flask(__name__)
app.secret_key = "your-secret-key"

SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
CLIENT_SECRETS_FILE = "client_secret.json"
MAX_RESULTS = 100

@app.route('/')
def index():
    return render_template("dashboard.html")

@app.route('/authorize')
def authorize():
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    authorization_url, state = flow.authorization_url(
        access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session['state']
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    return redirect(url_for('websites'))

@app.route('/websites')
def websites():
    if 'credentials' not in session:
        return redirect('authorize')
    creds = Credentials(**session['credentials'])
    service = googleapiclient.discovery.build('webmasters', 'v3', credentials=creds)
    site_list = service.sites().list().execute()
    websites = [s['siteUrl'] for s in site_list.get('siteEntry', [])]
    return render_template("dashboard.html", websites=websites)

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'credentials' not in session:
        return redirect('authorize')

    selected_site = request.form['selected_site']
    creds = Credentials(**session['credentials'])
    service = googleapiclient.discovery.build('webmasters', 'v3', credentials=creds)

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

    try:
        response = service.searchanalytics().query(siteUrl=selected_site, body=request_body).execute()
        rows = response.get("rows", [])
        if not rows:
            return "No data found for the selected property."
    except Exception as e:
        return f"Error fetching GSC data: {e}"

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

    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)

    return send_file(
        io.BytesIO(output.read().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='gsc_data.csv'
    )

def credentials_to_dict(creds):
    return {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }

if __name__ == '__main__':
    app.run(debug=True)
