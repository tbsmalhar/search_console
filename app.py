from flask import Flask, redirect, request, session, url_for, render_template
import os
import google_auth_oauthlib.flow
import googleapiclient.discovery
from google.oauth2.credentials import Credentials

app = Flask(__name__)
app.secret_key = "your-secret-key"  # Replace this with a secure key

# Set up Google OAuth scopes
SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
CLIENT_SECRETS_FILE = "client_secret.json"

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

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)

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
